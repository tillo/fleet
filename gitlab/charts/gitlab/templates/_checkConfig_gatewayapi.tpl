{{/*
Check if any Gateway API extension uses the deprecated global configuration.
*/}}
{{- define "gitlab.checkConfig.gatewayApi.envoy.global" -}}
{{- $keys := list "envoyProxySpec" "envoyClientTrafficPolicySpec" "envoySecurityPolicySpec" "class" "protocol" "addresses" "listeners" "gateway" "metrics" }}
{{- range $keys }}
{{-   if hasKey $.Values.global.gatewayApi . }}
Gateway API:
  The global.gatewayApi.{{ . }} settings moved away from global configuration. Please check
  https://docs.gitlab.com/charts/charts/envoygateway/ and https://docs.gitlab.com/charts/charts/globals/#gateway-api
  to migrate.
{{-   end }}
{{- end }}
{{- end -}}

{{/*
Check settings that contradict TLS terminated by a load balancer in front of the Gateway.
*/}}
{{- define "gitlab.checkConfig.gatewayApi.externalTlsTermination" -}}
{{- if eq "true" (include "gitlab.gatewayApi.externalTlsTermination" .) }}
{{-   if not .Values.global.hosts.https }}
Gateway API:
  global.gatewayApi.externalTlsTermination requires global.hosts.https to be true, so that
  GitLab generates HTTPS URLs for the load balancer that terminates TLS.
{{-   end }}
{{-   if .Values.global.appConfig.smartcard.enabled }}
Gateway API:
  Smartcard authentication is not supported with global.gatewayApi.externalTlsTermination:
  the Gateway must terminate TLS to verify client certificates.
{{-   end }}
{{-   range $name, $listener := .Values.gatewayApiResources.gateway.listeners }}
{{-     $protocol := upper (default "" $listener.protocol) }}
{{-     if has $protocol (list "HTTPS" "TLS") }}
Gateway API:
  The gatewayApiResources.gateway.listeners.{{ $name }}.protocol setting must not be HTTPS or TLS
  with global.gatewayApi.externalTlsTermination, because the load balancer terminates TLS.
{{-     else if and (eq $protocol "HTTP") (ne $name "http-default") }}
Gateway API:
  The gatewayApiResources.gateway.listeners.{{ $name }}.protocol setting must be empty with
  global.gatewayApi.externalTlsTermination. A listener set to HTTP is served on port 80 instead of
  gatewayApiResources.gateway.port, and bypasses the HTTP to HTTPS redirect.
{{-     end }}
{{-   end }}
{{- end }}
{{- end -}}
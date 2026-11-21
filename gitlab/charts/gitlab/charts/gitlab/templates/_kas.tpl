{{/* ######### KAS related templates */}}

{{- define "gitlab.kas.mountSecrets" -}}
{{- if (or .Values.global.kas.enabled .Values.global.appConfig.gitlab_kas.enabled) -}}
# mount secret for kas
- secret:
    name: {{ template "gitlab.kas.secret" . }}
    items:
      - key: {{ template "gitlab.kas.key" . }}
        path: kas/.gitlab_kas_secret
{{- end -}}
{{- end -}}{{/* "gitlab.kas.mountSecrets" */}}

{{/*
Returns the KAS external URL (for external agentk connections)
*/}}
{{- define "gitlab.appConfig.kas.externalUrl" -}}
{{-   if .Values.global.appConfig.gitlab_kas.externalUrl -}}
{{-     .Values.global.appConfig.gitlab_kas.externalUrl -}}
{{-   else -}}
{{-     $hostname := include "gitlab.kas.hostname" . -}}
{{-     $https := or .Values.global.hosts.https .Values.global.hosts.kas.https -}}
{{-     if and $https (eq (include "gitlab.kas.grpc.available" .) "true") -}}
{{-       printf "grpcs://%s" $hostname -}}
{{-     else if $https -}}
{{-       printf "wss://%s" $hostname -}}
{{-     else -}}
{{-       printf "ws://%s" $hostname -}}
{{-     end -}}
{{-   end -}}
{{- end -}}

{{/*
Whether the KAS gRPC Ingress is rendered. Takes a dict with:
  "local":    the kas chart's `ingress.grpc.enabled` (tri-state, kas chart only)
  "global":   `global.kas.ingress.grpc.enabled` (tri-state)
  "provider": `global.ingress.provider`
An explicit boolean wins, local before global. When both are unset the Ingress is
rendered for the NGINX provider only, because that is the provider the template can
annotate for gRPC.
*/}}
{{- define "gitlab.kas.ingress.grpc.enabled" -}}
{{-   if kindIs "bool" .local -}}
{{-     .local -}}
{{-   else if kindIs "bool" .global -}}
{{-     .global -}}
{{-   else -}}
{{-     eq (default "" .provider) "nginx" -}}
{{-   end -}}
{{- end -}}

{{/*
Returns "true" when native gRPC from agentk reaches KAS through the chart's networking.
Never with a relative URL root, because the gRPC path cannot be prefixed. Otherwise:

1. Gateway API with the chart-managed Envoy Gateway policies. gRPC passes through because
   of the BackendTrafficPolicy (useClientProtocol) on the KAS HTTPRoute, not because of the
   route itself, so this follows gitlab.gatewayApi.envoy.installRoutePolicies
   (global.gatewayApi.enabled plus configureEnvoy or installEnvoy). The Gateway must also live
   in the release namespace (global.gatewayApi.gatewayRef.namespace unset or equal to it),
   because the KAS ClientTrafficPolicy that keeps HTTP/2 on the listener can only target a
   Gateway in the same namespace. An external Gateway of another vendor, or one in another
   namespace, keeps WebSocket.
2. The KAS gRPC Ingress. An explicit global.kas.ingress.grpc.enabled is trusted as is. When
   it is unset, the Ingress only counts if Ingress is enabled globally (global.ingress.enabled,
   unset means enabled, like gitlab.ingress.enabled) and the provider is NGINX, the one the
   template can annotate for gRPC. Routing handled outside the chart keeps WebSocket.

Only reads `global` values, because it is evaluated from the webservice, sidekiq and toolbox
charts, which cannot see the kas chart's values. The kas chart's local toggles
(ingress.grpc.enabled, gatewayRoute.enabled, gatewayRoute.gatewayNamespace,
backendTrafficPolicy.spec, clientTrafficPolicy.spec) are therefore not seen here; prefer the
global settings, or set global.appConfig.gitlab_kas.externalUrl.
*/}}
{{- define "gitlab.kas.grpc.available" -}}
{{-   $relativeUrlRoot := default "" .Values.global.appConfig.relativeUrlRoot -}}
{{-   if eq $relativeUrlRoot "" -}}
{{-     if eq "true" (include "gitlab.gatewayApi.envoy.installRoutePolicies" .) -}}
{{-       $gatewayNamespace := default .Release.Namespace .Values.global.gatewayApi.gatewayRef.namespace -}}
{{-       if eq $gatewayNamespace .Release.Namespace -}}
true
{{-       end -}}
{{-     else -}}
{{-       $globalToggle := .Values.global.kas.ingress.grpc.enabled -}}
{{-       $ingressEnabled := true -}}
{{-       if kindIs "bool" .Values.global.ingress.enabled -}}
{{-         $ingressEnabled = .Values.global.ingress.enabled -}}
{{-       end -}}
{{-       if kindIs "bool" $globalToggle -}}
{{-         if $globalToggle }}true{{ end -}}
{{-       else if and $ingressEnabled (eq (default "" .Values.global.ingress.provider) "nginx") -}}
true
{{-       end -}}
{{-     end -}}
{{-   end -}}
{{- end -}}

{{- define "gitlab.kas.internal.scheme" -}}
{{- printf "%s" (ternary "grpcs" "grpc" (eq $.Values.global.kas.tls.enabled true)) -}}
{{- end -}}

{{/*
Returns the KAS internal URL (for GitLab backend connections)
*/}}
{{- define "gitlab.appConfig.kas.internalUrl" -}}
{{-   if .Values.global.appConfig.gitlab_kas.internalUrl -}}
{{-     .Values.global.appConfig.gitlab_kas.internalUrl -}}
{{-   else -}}
{{-     $serviceHost := include "gitlab.kas.serviceHost" . -}}
{{-     $scheme := include "gitlab.kas.internal.scheme" . -}}
{{-     $port := .Values.global.kas.service.apiExternalPort -}}
{{-     printf "%s://%s:%s" $scheme $serviceHost (toString $port) -}}
{{-   end -}}
{{- end -}}

{{/*
Returns the KAS client timeout in seconds
*/}}
{{- define "gitlab.appConfig.kas.clientTimeoutSeconds" -}}
{{- with .Values.global.appConfig.gitlab_kas.clientTimeoutSeconds -}}
client_timeout_seconds: {{ . }}
{{- end -}}
{{- end -}}

{{/*
Return the KAS service host
*/}}
{{- define "gitlab.kas.serviceHost" -}}
{{-     $serviceName := include "gitlab.kas.serviceName" . -}}
{{-     include "gitlab.assembleServiceAddress" (dict "name" $serviceName "context" $) -}}
{{- end -}}

{{/*
Return the KAS service name
*/}}
{{- define "gitlab.kas.serviceName" -}}
{{- include "gitlab.other.fullname" (dict "context" . "chartName" "kas") -}}
{{- end -}}


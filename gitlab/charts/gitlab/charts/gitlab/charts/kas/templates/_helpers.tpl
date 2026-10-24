{{/*
Returns the secret name for the Secret containing the TLS certificate and key.
Uses `ingress.tls.secretName` first and falls back to `global.ingress.tls.secretName`
if there is a shared tls secret for all ingresses.
*/}}
{{- define "kas.tlsSecret" -}}
{{- $defaultName := (dict "secretName" "") -}}
{{- if .Values.global.ingress.configureCertmanager -}}
{{- $_ := set $defaultName "secretName" (printf "%s-kas-tls" .Release.Name) -}}
{{- else -}}
{{- $_ := set $defaultName "secretName" (include "gitlab.wildcard-self-signed-cert-name" .) -}}
{{- end -}}
{{- pluck "secretName" .Values.ingress.tls .Values.global.ingress.tls $defaultName | first -}}
{{- end -}}

{{/*
Build Redis config for KAS
*/}}
{{- define "kas.redis" -}}
{{- if .Values.global.redis.kas -}}
{{-   $_ := set $ "redisConfigName" "kas" -}}
{{- else if .Values.global.redis.sharedState -}}
{{-   $_ := set $ "redisConfigName" "sharedState" -}}
{{- end -}}
{{- include "gitlab.redis.selectedMergedConfig" . -}}
{{- if .redisMergedConfig.user }}
username: {{ .redisMergedConfig.user }}
{{- end -}}
{{- if .redisMergedConfig.password.enabled }}
password_file: /etc/kas/redis/{{ printf "%s-password" (default "redis" .redisConfigName) }}
{{- end }}
database_index: {{ .redisMergedConfig.database }}
{{/* Save these before calling gitlab.redis.host which mutates state */}}
{{- $sentinelTLS := .redisMergedConfig.sentinelTLS | default (dict) -}}
{{- $redisTLS := .redisMergedConfig.redisTLS | default (dict) -}}
{{- if not .redisMergedConfig.sentinels }}
server:
  address: {{ template "gitlab.redis.host" . }}:{{ template "gitlab.redis.port" . }}
{{- else }}
sentinel:
  addresses:
  {{- range $i, $entry := .redisMergedConfig.sentinels }}
    - {{ quote (print (trim $entry.host) ":" ( default 26379 $entry.port | int ) ) -}}
  {{ end }}
  master_name: {{ template "gitlab.redis.host" . }}
{{- if .redisMergedConfig.sentinelAuth.enabled }}
  sentinel_password_file: "/etc/kas/redis-sentinel/redis-sentinel-password"
{{- end }}
{{- if $sentinelTLS.enabled }}
  tls:
    enabled: true
{{-   if (kindIs "map" $sentinelTLS.caFile) }}
    ca_certificate_file: /etc/kas/redis-sentinel/{{ $sentinelTLS.caFile.key }}
{{-   end }}
{{-   if (kindIs "map" $sentinelTLS.cert) }}
    certificate_file: /etc/kas/redis-sentinel/{{ $sentinelTLS.cert.key }}
{{-   end }}
{{-   if (kindIs "map" $sentinelTLS.key) }}
    key_file: /etc/kas/redis-sentinel/{{ $sentinelTLS.key.key }}
{{-   end }}
{{- end }}
{{- end -}}
{{- if eq (.redisMergedConfig.scheme | default "") "rediss" }}
tls:
  enabled: true
{{-   if (kindIs "map" $redisTLS.caFile) }}
  ca_certificate_file: /etc/kas/redis/{{ $redisTLS.caFile.key }}
{{-   end }}
{{-   if (kindIs "map" $redisTLS.cert) }}
  certificate_file: /etc/kas/redis/{{ $redisTLS.cert.key }}
{{-   end }}
{{-   if (kindIs "map" $redisTLS.key) }}
  key_file: /etc/kas/redis/{{ $redisTLS.key.key }}
{{-   end }}
{{- end -}}
{{- end -}}

{{/*
Labels to select Pods created by the KAS Deployment. Used for Service, PodMonitor, ServiceMonitor, etc.
*/}}
{{- define "kas.podSelectorLabels" -}}
app: {{ template "name" . }}
release: {{ .Release.Name }}
{{- end -}}


{{/*
Returns the secret name for the Secret containing the TLS certificate and key for Workspaces.
Uses `ingress.tls.workspacesSecretName` first and falls back to `global.ingress.tls.workspacesSecretName`.
*/}}
{{- define "workspaces.tlsSecret" -}}
{{- $defaultName := (dict "workspacesSecretName" "") -}}
{{- if .Values.global.ingress.configureCertmanager -}}
{{- $_ := set $defaultName "workspacesSecretName" (printf "%s-kas-workspaces-tls" .Release.Name) -}}
{{- end -}}
{{- pluck "workspacesSecretName" .Values.ingress.tls .Values.global.ingress.tls $defaultName | first -}}
{{- end -}}

{{/*
Whether the section-scoped ClientTrafficPolicy for the KAS listener is rendered: Envoy Gateway
is configured by the chart, the Gateway lives in the release namespace (the policy targets it
with a LocalPolicyTargetReference), and the KAS listener is HTTPS. In HTTP-only deployments all
listeners share port 80 and Envoy Gateway rejects section-scoped policies for them.
*/}}
{{- define "kas.gatewayApi.sectionCtp.enabled" -}}
{{- $envoy := and .Values.global.gatewayApi.enabled (eq "true" (include "gitlab.gatewayApi.configureEnvoy" .)) -}}
{{- $sameNamespace := eq "true" (include "gitlab.gatewayApi.gateway.sameNamespace" .) -}}
{{- $https := or .Values.global.hosts.https .Values.global.hosts.kas.https -}}
{{- if and $envoy $sameNamespace $https -}}
true
{{- end -}}
{{- end -}}

{{/*
Whether the KAS gRPC Ingress is rendered. Never with a relative URL root, because the gRPC path
cannot be prefixed. <no value> hostnames are not empty: https://github.com/helm/helm/issues/13487.
*/}}
{{- define "kas.ingress.grpc.render" -}}
{{- if and .Values.global.kas.enabled (eq (include "gitlab.ingress.enabled" .) "true") -}}
{{-   $hostname := include "gitlab.kas.hostname" . | trim -}}
{{-   $relativeUrlRoot := default "" .Values.global.appConfig.relativeUrlRoot -}}
{{-   $grpcEnabled := include "gitlab.kas.ingress.grpc.enabled" (dict "local" .Values.ingress.grpc.enabled "global" .Values.global.kas.ingress.grpc.enabled "provider" .Values.global.ingress.provider) -}}
{{-   if and (eq $grpcEnabled "true") (ne $hostname "") (ne $hostname "<no value>") (eq $relativeUrlRoot "") -}}
true
{{-   end -}}
{{- end -}}
{{- end -}}

{{/*
Name of the Service the KAS gRPC Ingress routes to: with Traefik, the dedicated gRPC Service.
*/}}
{{- define "kas.ingress.grpc.serviceName" -}}
{{- $name := include "gitlab.kas.serviceName" . -}}
{{- if eq .Values.global.ingress.provider "traefik" -}}
{{-   printf "%s-grpc" ($name | trunc 58 | trimSuffix "-") -}}
{{- else -}}
{{-   $name -}}
{{- end -}}
{{- end -}}

{{/*
Whether the chart renders a Traefik ServersTransport for KAS: Traefik routes to KAS through Ingress,
and KAS serves TLS.
*/}}
{{- define "kas.traefik.serversTransport.enabled" -}}
{{- if and .Values.global.kas.enabled .Values.global.kas.tls.enabled (eq .Values.global.ingress.provider "traefik") (eq (include "gitlab.ingress.enabled" .) "true") -}}
true
{{- end -}}
{{- end -}}

{{/*
Annotations of a KAS Service routed by Traefik. Takes a dict with "context" and "scheme". The scheme wins
over service.annotations, and the chart's ServersTransport is used unless service.annotations sets one.
*/}}
{{- define "kas.traefik.serviceAnnotations" -}}
{{- $ := .context -}}
{{- $annotations := merge (dict "traefik.ingress.kubernetes.io/service.serversscheme" .scheme) (include "gitlab.serviceAnnotations" $ | fromYaml) -}}
{{- if eq (include "kas.traefik.serversTransport.enabled" $) "true" -}}
{{-   $_ := merge $annotations (dict "traefik.ingress.kubernetes.io/service.serverstransport" (printf "%s-%s@kubernetescrd" $.Release.Namespace (include "fullname" $))) -}}
{{- end -}}
{{- toYaml $annotations -}}
{{- end -}}

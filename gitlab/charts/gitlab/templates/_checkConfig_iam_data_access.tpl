{{/*
Ensures that grpc.host is configured when iamDataAccessService is enabled
*/}}
{{- define "gitlab.checkConfig.iamDataAccessService.grpc.host" -}}
  {{- with .Values.global.appConfig.iamDataAccessService -}}
    {{- if .enabled -}}
      {{- if not (dig "grpc" "host" "" .) }}
iamDataAccessService:
    grpc.host is required when iamDataAccessService is enabled. Please set `global.appConfig.iamDataAccessService.grpc.host`.
      {{- end -}}
    {{- end -}}
  {{- end -}}
{{- end -}}
{{/* END gitlab.checkConfig.iamDataAccessService.grpc.host */}}

{{/*
Ensures that grpc.port is configured when iamDataAccessService is enabled
*/}}
{{- define "gitlab.checkConfig.iamDataAccessService.grpc.port" -}}
  {{- with .Values.global.appConfig.iamDataAccessService -}}
    {{- if .enabled -}}
      {{- if not (dig "grpc" "port" 0 .) }}
iamDataAccessService:
    grpc.port is required when iamDataAccessService is enabled. Please set `global.appConfig.iamDataAccessService.grpc.port`.
      {{- end -}}
    {{- end -}}
  {{- end -}}
{{- end -}}
{{/* END gitlab.checkConfig.iamDataAccessService.grpc.port */}}

{{/*
Ensures that grpc.secure is a boolean when iamDataAccessService is enabled
*/}}
{{- define "gitlab.checkConfig.iamDataAccessService.grpc.secure" -}}
  {{- with .Values.global.appConfig.iamDataAccessService -}}
    {{- if .enabled -}}
      {{- $grpc := dig "grpc" dict . -}}
      {{- if and (kindIs "map" $grpc) (hasKey $grpc "secure") -}}
        {{- if not (kindIs "bool" $grpc.secure) }}
iamDataAccessService:
    grpc.secure must be a boolean. Please set `global.appConfig.iamDataAccessService.grpc.secure` to `true` or `false`.
        {{- end -}}
      {{- end -}}
    {{- end -}}
  {{- end -}}
{{- end -}}
{{/* END gitlab.checkConfig.iamDataAccessService.grpc.secure */}}

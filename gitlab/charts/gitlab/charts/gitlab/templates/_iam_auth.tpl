{{/* ######### iam-auth service related templates */}}

{{/*
Return the iam-auth service token secret
*/}}

{{- define "gitlab.appConfig.iamAuthService.authToken.secret" -}}
{{- default (printf "%s-iam-auth-secret" .Release.Name) ((.Values.global.appConfig.iamAuthService).authToken).secret | quote -}}
{{- end -}}

{{- define "gitlab.appConfig.iamAuthService.authToken.key" -}}
{{- default "iam_auth_service_token" ((.Values.global.appConfig.iamAuthService).authToken).key | quote -}}
{{- end -}}

{{/*
Return the iam-auth service HTTP URL. Mirrors Authn::IamAuthService.url in Rails.
*/}}
{{- define "gitlab.appConfig.iamAuthService.url" -}}
{{- with .Values.global.appConfig.iamAuthService }}
{{-   if .enabled }}
{{-     $host := dig "http" "host" "" . }}
{{-     $port := dig "http" "port" 0 . | int }}
{{-     if and $host $port }}https://{{ $host }}:{{ $port }}{{ end }}
{{-   end }}
{{- end }}
{{- end -}}

{{/*
Mount secret for iam-auth service token
*/}}
{{- define "gitlab.appConfig.iamAuthService.mountSecrets" -}}
{{- if .Values.global.appConfig.iamAuthService.enabled -}}
# mount secret for iam-auth service token
- secret:
    name: {{ template "gitlab.appConfig.iamAuthService.authToken.secret" . }}
    items:
      - key: {{ template "gitlab.appConfig.iamAuthService.authToken.key" . }}
        path: iam-auth/.gitlab_iam_auth_secret
{{- end -}}
{{- end -}}

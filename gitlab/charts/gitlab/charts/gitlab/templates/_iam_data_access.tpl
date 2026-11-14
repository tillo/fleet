{{/* ######### iam-data-access service related templates */}}

{{/*
Return the iam-data-access service token secret
*/}}

{{- define "gitlab.appConfig.iamDataAccessService.authToken.secret" -}}
{{- default (printf "%s-iam-data-access-secret" .Release.Name) ((.Values.global.appConfig.iamDataAccessService).authToken).secret | quote -}}
{{- end -}}

{{- define "gitlab.appConfig.iamDataAccessService.authToken.key" -}}
{{- default "iam_data_access_service_token" ((.Values.global.appConfig.iamDataAccessService).authToken).key | quote -}}
{{- end -}}

{{/*
Mount secret for iam-data-access service token
*/}}
{{- define "gitlab.appConfig.iamDataAccessService.mountSecrets" -}}
{{- if .Values.global.appConfig.iamDataAccessService.enabled -}}
# mount secret for iam-data-access service token
- secret:
    name: {{ template "gitlab.appConfig.iamDataAccessService.authToken.secret" . }}
    items:
      - key: {{ template "gitlab.appConfig.iamDataAccessService.authToken.key" . }}
        path: iam-data-access/.gitlab_iam_data_access_secret
{{- end -}}
{{- end -}}

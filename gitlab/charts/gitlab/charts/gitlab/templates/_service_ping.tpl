{{/* ######### Service Ping related templates */}}

{{/*
Name of, and key within, the Secret that holds the Service Ping internal API key.
*/}}
{{- define "gitlab.appConfig.servicePing.jwtSecret.secret" -}}
{{- default (printf "%s-gitlab-service-ping-secret" .Release.Name) ((.Values.global.appConfig.servicePing).jwtSecret).secret | quote -}}
{{- end -}}{{/* "gitlab.appConfig.servicePing.jwtSecret.secret" */}}

{{- define "gitlab.appConfig.servicePing.jwtSecret.key" -}}
{{- default "secret" ((.Values.global.appConfig.servicePing).jwtSecret).key | quote -}}
{{- end -}}{{/* "gitlab.appConfig.servicePing.jwtSecret.key" */}}

{{- define "gitlab.appConfig.servicePing.mountSecrets" -}}
# mount secret for Service Ping internal API
- secret:
    name: {{ include "gitlab.appConfig.servicePing.jwtSecret.secret" . }}
    items:
      - key: {{ include "gitlab.appConfig.servicePing.jwtSecret.key" . }}
        path: service_ping/.gitlab_service_ping_secret
    optional: true
{{- end -}}{{/* "gitlab.appConfig.servicePing.mountSecrets" */}}

{{- define "gitlab.appConfig.servicePing" -}}
service_ping:
  secret_file: /etc/gitlab/service_ping/.gitlab_service_ping_secret
{{- end -}}{{/* "gitlab.appConfig.servicePing" */}}

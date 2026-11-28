{{/* ######### mailroom templates */}}

{{- define "gitlab.appConfig.incomingEmail.mountSecrets" -}}
# mount secrets for incomingEmail
{{- if and $.Values.global.appConfig.incomingEmail.enabled (eq $.Values.global.appConfig.incomingEmail.deliveryMethod "webhook") }}
- secret:
    name: {{ template "gitlab.appConfig.incomingEmail.authToken.secret" . }}
    items:
      - key: {{ template "gitlab.appConfig.incomingEmail.authToken.key" . }}
        path: mailroom/incoming_email_webhook_secret
{{- end }}
{{- end -}}{{/* "gitlab.appConfig.incomingEmail.mountSecrets" "*/}}

{{/* Only Webservice verifies mailroom tokens, so only it mounts the public keys. */}}
{{- define "gitlab.appConfig.incomingEmail.mountPublicKeys" -}}
{{- $incomingEmail := $.Values.global.appConfig.incomingEmail }}
{{- $publicKeys := $incomingEmail.publicKeyFiles | default (dict) }}
{{- if and $incomingEmail.enabled (eq $incomingEmail.deliveryMethod "webhook") $publicKeys.secret $publicKeys.keys }}
- secret:
    name: {{ $publicKeys.secret | quote }}
    items:
      {{- range $publicKeys.keys }}
      - key: {{ . | quote }}
        path: mailroom/incoming_email_public_key_{{ . }}
      {{- end }}
{{- end }}
{{- end -}}{{/* "gitlab.appConfig.incomingEmail.mountPublicKeys" */}}

{{- define "gitlab.appConfig.serviceDeskEmail.mountSecrets" -}}
# mount secrets for serviceDeskEmail
{{- if and $.Values.global.appConfig.serviceDeskEmail.enabled (eq $.Values.global.appConfig.serviceDeskEmail.deliveryMethod "webhook") }}
- secret:
    name: {{ template "gitlab.appConfig.serviceDeskEmail.authToken.secret" . }}
    items:
      - key: {{ template "gitlab.appConfig.serviceDeskEmail.authToken.key" . }}
        path: mailroom/service_desk_email_webhook_secret
{{- end }}
{{- end -}}{{/* "gitlab.appConfig.serviceDeskEmail.mountSecrets" "*/}}

{{- define "gitlab.appConfig.serviceDeskEmail.mountPublicKeys" -}}
{{- $serviceDeskEmail := $.Values.global.appConfig.serviceDeskEmail }}
{{- $publicKeys := $serviceDeskEmail.publicKeyFiles | default (dict) }}
{{- if and $serviceDeskEmail.enabled (eq $serviceDeskEmail.deliveryMethod "webhook") $publicKeys.secret $publicKeys.keys }}
- secret:
    name: {{ $publicKeys.secret | quote }}
    items:
      {{- range $publicKeys.keys }}
      - key: {{ . | quote }}
        path: mailroom/service_desk_email_public_key_{{ . }}
      {{- end }}
{{- end }}
{{- end -}}{{/* "gitlab.appConfig.serviceDeskEmail.mountPublicKeys" */}}

{{/*
Return the gitlab-mailroom webhook secrets
*/}}

{{- define "gitlab.appConfig.incomingEmail.authToken.secret" -}}
{{- default (printf "%s-incoming-email-auth-token" .Release.Name) $.Values.global.appConfig.incomingEmail.authToken.secret | quote -}}
{{- end -}}

{{- define "gitlab.appConfig.incomingEmail.authToken.key" -}}
{{- default "authToken" $.Values.global.appConfig.incomingEmail.authToken.key | quote -}}
{{- end -}}

{{- define "gitlab.appConfig.serviceDeskEmail.authToken.secret" -}}
{{- default (printf "%s-service-desk-email-auth-token" .Release.Name) $.Values.global.appConfig.serviceDeskEmail.authToken.secret | quote -}}
{{- end -}}

{{- define "gitlab.appConfig.serviceDeskEmail.authToken.key" -}}
{{- default "authToken" $.Values.global.appConfig.serviceDeskEmail.authToken.key | quote -}}
{{- end -}}

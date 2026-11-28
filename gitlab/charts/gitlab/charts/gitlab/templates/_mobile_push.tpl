{{- define "gitlab.appConfig.mobilePush.configuration" -}}
{{- $apns := .Values.global.appConfig.mobilePush.apns -}}
{{- if trim (toString (default "" $apns.authKey.secret)) }}
mobile_push:
  apns:
    auth_key_path: /etc/gitlab/mobile_push/apns_auth_key.p8
    key_id: {{ trim (toString (default "" $apns.keyId)) | quote }}
    team_id: {{ trim (toString (default "" $apns.teamId)) | quote }}
{{-   if trim (toString (default "" $apns.topic)) }}
    topic: {{ trim (toString (default "" $apns.topic)) | quote }}
{{-   end }}
{{- end }}
{{- end -}}{{/* "gitlab.appConfig.mobilePush.configuration" */}}

{{- define "gitlab.appConfig.mobilePush.mountSecrets" -}}
{{- $apns := .Values.global.appConfig.mobilePush.apns -}}
{{- if trim (toString (default "" $apns.authKey.secret)) }}
# mount secret for mobile push APNs auth key
- secret:
    name: {{ trim (toString (default "" $apns.authKey.secret)) }}
    items:
      - key: {{ $apns.authKey.key }}
        path: mobile_push/apns_auth_key.p8
{{- end -}}
{{- end -}}{{/* "gitlab.appConfig.mobilePush.mountSecrets" */}}

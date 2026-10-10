{{/*
Ensure that the mobile push APNs settings are strings and are complete when
any of them is provided
*/}}
{{- define "gitlab.checkConfig.mobilePush.apns" -}}
{{- with $.Values.global.appConfig.mobilePush.apns }}
{{-   $nonStrings := list -}}
{{-   range $name, $value := dict "authKey.secret" .authKey.secret "keyId" .keyId "teamId" .teamId "topic" .topic }}
{{-     if and (not (kindIs "invalid" $value)) (not (kindIs "string" $value)) }}
{{-       $nonStrings = append $nonStrings (printf "`global.appConfig.mobilePush.apns.%s`" $name) -}}
{{-     end }}
{{-   end }}
{{-   if $nonStrings }}
mobilePush:
    Mobile push APNs settings must be strings to avoid YAML type coercion.
    Quote the value of: {{ join ", " $nonStrings }}.
    See https://docs.gitlab.com/charts/charts/globals#mobile-push-notification-settings
{{-   end -}}
{{-   $secret := trim (toString (default "" .authKey.secret)) -}}
{{-   $keyId := trim (toString (default "" .keyId)) -}}
{{-   $teamId := trim (toString (default "" .teamId)) -}}
{{-   $topic := trim (toString (default "" .topic)) -}}
{{-   if or $secret $keyId $teamId $topic }}
{{-     if not $secret }}
mobilePush:
    When configuring mobile push APNs, be sure to provide the Kubernetes secret
    containing the APNs auth key (`global.appConfig.mobilePush.apns.authKey.secret`).
    See https://docs.gitlab.com/charts/charts/globals#mobile-push-notification-settings
{{-     end -}}
{{-     if not $keyId }}
mobilePush:
    When configuring mobile push APNs, be sure to specify the key ID of the APNs
    auth key (`global.appConfig.mobilePush.apns.keyId`).
    See https://docs.gitlab.com/charts/charts/globals#mobile-push-notification-settings
{{-     end -}}
{{-     if not $teamId }}
mobilePush:
    When configuring mobile push APNs, be sure to specify the Apple team ID that
    owns the APNs auth key (`global.appConfig.mobilePush.apns.teamId`).
    See https://docs.gitlab.com/charts/charts/globals#mobile-push-notification-settings
{{-     end -}}
{{-   end -}}
{{- end -}}
{{- end -}}
{{/* END gitlab.checkConfig.mobilePush.apns */}}

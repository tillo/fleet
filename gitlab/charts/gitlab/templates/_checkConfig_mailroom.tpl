{{/*
Ensure that tenantId and clientId are set if Microsoft Graph settings are used in incomingEmail
*/}}
{{- define "gitlab.checkConfig.incomingEmail.microsoftGraph" -}}
{{- with $.Values.global.appConfig.incomingEmail }}
{{-   if (and .enabled (eq .inboxMethod "microsoft_graph")) }}
{{-     if not .tenantId }}
incomingEmail:
    When configuring incoming email with Microsoft Graph, be sure to specify the tenant ID.
    See https://docs.gitlab.com/administration/incoming_email/#microsoft-graph
{{-     end -}}
{{-     if not .clientId }}
incomingEmail:
    When configuring incoming email with Microsoft Graph, be sure to specify the client ID.
    See https://docs.gitlab.com/administration/incoming_email/#microsoft-graph
{{-     end -}}
{{-   end -}}
{{- end -}}
{{- end -}}
{{/* END gitlab.checkConfig.incomingEmail.microsoftGraph */}}

{{/*
Ensure that incomingEmail is enabled too if serviceDesk is enabled
*/}}
{{- define "gitlab.checkConfig.serviceDesk" -}}
{{-   if $.Values.global.appConfig.serviceDeskEmail.enabled }}
{{-     if not $.Values.global.appConfig.incomingEmail.enabled }}
serviceDesk:
    When configuring Service Desk email, you must also configure incoming email.
    See https://docs.gitlab.com/charts/charts/globals#incoming-email-settings
{{-     end -}}
{{-     if (not (and (contains "+%{key}@" $.Values.global.appConfig.incomingEmail.address) (contains "+%{key}@" $.Values.global.appConfig.serviceDeskEmail.address))) }}
serviceDesk:
    When configuring Service Desk email, both incoming email and Service Desk email address must contain the "+%{key}" tag.
    See https://docs.gitlab.com/user/project/service_desk/configure/#custom-email-address
{{-     end -}}
{{-   end -}}
{{- end -}}
{{/* END gitlab.checkConfig.serviceDesk */}}

{{/*
Ensure that tenantId and clientId are set if Microsoft Graph settings are used in serviceDesk
*/}}
{{- define "gitlab.checkConfig.serviceDesk.microsoftGraph" -}}
{{- with $.Values.global.appConfig.serviceDesk }}
{{-   if (and .enabled (eq .inboxMethod "microsoft_graph")) }}
{{-     if not .tenantId }}
incomingEmail:
    When configuring Service Desk with Microsoft Graph, be sure to specify the tenant ID.
    See https://docs.gitlab.com/administration/incoming_email/#microsoft-graph
{{-     end -}}
{{-     if not .clientId }}
incomingEmail:
    When configuring Service Desk with Microsoft Graph, be sure to specify the client ID.
    See https://docs.gitlab.com/administration/incoming_email/#microsoft-graph
{{-     end -}}
{{-   end -}}
{{- end -}}
{{- end -}}
{{/* END gitlab.checkConfig.serviceDesk.microsoftGraph */}}

{{/*
Ensure that incomingEmail's deliveryMethod is either sidekiq or webhook
*/}}
{{- define "gitlab.checkConfig.incomingEmail.deliveryMethod" -}}
{{- if not (or (eq $.Values.global.appConfig.incomingEmail.deliveryMethod "sidekiq") (eq $.Values.global.appConfig.incomingEmail.deliveryMethod "webhook")) }}
incomingEmail:
    Delivery method should be either "sidekiq" or "webhook"
    See https://docs.gitlab.com/charts/installation/command-line-options/#incoming-email-configuration
{{- end -}}
{{- end -}}
{{/* END gitlab.checkConfig.incomingEmail.deliveryMethod */}}

{{/*
Ensure that serviceDeskEmail's deliveryMethod is either sidekiq or webhook
*/}}
{{- define "gitlab.checkConfig.serviceDeskEmail.deliveryMethod" -}}
{{- if not (or (eq $.Values.global.appConfig.serviceDeskEmail.deliveryMethod "sidekiq") (eq $.Values.global.appConfig.serviceDeskEmail.deliveryMethod "webhook")) }}
serviceDeskEmail:
    Delivery method should be either "sidekiq" or "webhook"
    See https://docs.gitlab.com/charts/installation/command-line-options/#service-desk-email-configuration
{{- end -}}
{{- end -}}
{{/* END gitlab.checkConfig.serviceDeskEmail.deliveryMethod */}}

{{/*
Ensure publicKeyFiles names both a secret and the fields to read from it.
Half a configuration mounts nothing, so asymmetric tokens would be silently rejected.
*/}}
{{- define "gitlab.checkConfig.mailroom.publicKeyFiles" -}}
{{- range $mailbox := list "incomingEmail" "serviceDeskEmail" }}
{{-   $config := index $.Values.global.appConfig $mailbox }}
{{-   $publicKeys := $config.publicKeyFiles | default (dict) }}
{{-   if and $config.enabled (eq $config.deliveryMethod "webhook") }}
{{-     if and $publicKeys.secret (not $publicKeys.keys) }}
{{ $mailbox }}:
    `global.appConfig.{{ $mailbox }}.publicKeyFiles.secret` is set but `keys` is empty, so no public key is mounted. List the secret fields holding the PEM public keys.
{{-     end }}
{{-     if and $publicKeys.keys (not $publicKeys.secret) }}
{{ $mailbox }}:
    `global.appConfig.{{ $mailbox }}.publicKeyFiles.keys` is set but `secret` is empty. Name the secret holding those fields.
{{-     end }}
{{-   end }}
{{- end -}}
{{- end -}}
{{/* END gitlab.checkConfig.mailroom.publicKeyFiles */}}

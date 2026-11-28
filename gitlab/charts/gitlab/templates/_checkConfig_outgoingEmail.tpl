{{/*
Ensure that Amazon SES mailer and Microsoft Graph mailer are not both enabled.
These outgoing email delivery methods are mutually exclusive.
*/}}
{{- define "gitlab.checkConfig.outgoingEmail.mailerExclusive" -}}
{{-   if and $.Values.global.appConfig.amazon_ses_mailer.enabled $.Values.global.appConfig.microsoft_graph_mailer.enabled }}
outgoingEmail:
    global.appConfig.amazon_ses_mailer and global.appConfig.microsoft_graph_mailer are mutually exclusive.
    Enable only one of them.
    See https://docs.gitlab.com/charts/charts/globals#outgoing-email
{{-   end -}}
{{- end -}}
{{/* END gitlab.checkConfig.outgoingEmail.mailerExclusive */}}

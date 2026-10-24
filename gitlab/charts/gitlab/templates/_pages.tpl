{{/* ######### gitlab-pages related templates */}}

{{/*
Return the gitlab-pages secret
*/}}

{{- define "gitlab.pages.apiSecret.secret" -}}
{{- default (printf "%s-gitlab-pages-secret" .Release.Name) $.Values.global.pages.apiSecret.secret | quote -}}
{{- end -}}

{{- define "gitlab.pages.apiSecret.key" -}}
{{- default "shared_secret" $.Values.global.pages.apiSecret.key | quote -}}
{{- end -}}

{{- define "gitlab.pages.authSecret.secret" -}}
{{ default (printf "%s-gitlab-pages-auth-secret" .Release.Name) $.Values.global.pages.authSecret.secret }}
{{- end -}}

{{- define "gitlab.pages.authSecret.key" -}}
{{ default "password" $.Values.global.pages.authSecret.key }}
{{- end -}}

{{/*
Returns the Pages hostname.
If the hostname is set in `global.hosts.pages.name`, that will be returned,
otherwise the hostname will be assembed using `pages` as the prefix, and the `gitlab.assembleHost` function.
*/}}
{{- define "gitlab.pages.hostname" -}}
{{- coalesce $.Values.global.pages.host $.Values.global.hosts.pages.name (include "gitlab.assembleHost"  (dict "name" "pages" "context" . )) -}}
{{- end -}}

{{/*
Returns the effective hostname for the pages-web Gateway listener and the
GitLab Pages HTTPRoute. When global.hosts.pages.hostnameOverride is set it
takes precedence; otherwise the standard Pages hostname is used. The
wildcard prefix is applied here too, since both the Gateway listener and the
HTTPRoute must match on the exact same hostname for the route to attach, so
a single template keeps the two call sites in sync if the override or
wildcard logic ever changes.
*/}}
{{- define "gitlab.pages.gatewayHostname" -}}
{{- $hostname := .Values.global.hosts.pages.hostnameOverride | default (include "gitlab.pages.hostname" .) -}}
{{- if not .Values.global.pages.namespaceInPath -}}
{{- $hostname = printf "*.%s" $hostname -}}
{{- end -}}
{{- $hostname -}}
{{- end -}}

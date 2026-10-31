{{/* vim: set filetype=mustache: */}}

{{/* Ensure shared-secrets.provider names a backend the chart implements. */}}
{{- define "gitlab.checkConfig.sharedSecrets.provider" -}}
{{-   $provider := include "gitlab.shared-secrets.provider" . -}}
{{-   if not (has $provider (list "job" "controller")) }}
shared-secrets: unknown provider
    `shared-secrets.provider` must be `job` or `controller`, but was `{{ $provider }}`.
    See https://docs.gitlab.com/charts/charts/shared-secrets/
{{-   end -}}
{{- end -}}
{{/* END gitlab.checkConfig.sharedSecrets.provider */}}

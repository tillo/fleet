{{/*
Override ai-gateway.gitlab.url helper
Uses `ai-gateway.gitlab.url` when set, and the chart's GitLab URL otherwise.
In production mode the AIGW requires the gitlab url to be https for the key exchange to work
Otherwise it will fail for security reasons
*/}}
{{- define "ai-gateway.gitlab.url" -}}
{{- coalesce (.Values.gitlab).url (include "gitlab.gitlab.url" .) -}}
{{- end -}}

{{/*
Override ai-gateway.gitlab.apiUrl helper
Uses `ai-gateway.gitlab.apiUrl` when set, and derives it from ai-gateway.gitlab.url otherwise.
*/}}
{{- define "ai-gateway.gitlab.apiUrl" -}}
{{- coalesce (.Values.gitlab).apiUrl (printf "%s/api/v4" (include "ai-gateway.gitlab.url" . | trimSuffix "/")) -}}
{{- end -}}

{{/*
Resolves one field of an AI Gateway key secret reference, for example `aigwSigningKey.secret`,
and falls back to the chart-generated default.

The key helpers render in two contexts: in the ai-gateway subchart, which reads the reference
from its own values, and in the shared-secrets manifest, which reads it under `ai-gateway`.

Usage: include "gitlab.ai-gateway.keyRef" (dict "context" . "name" "aigwSigningKey" "field" "secret" "default" "...")
*/}}
{{- define "gitlab.ai-gateway.keyRef" -}}
{{- $values := .context.Values -}}
{{- if ne .context.Chart.Name "ai-gateway" -}}
{{-   $values = index .context.Values "ai-gateway" -}}
{{- end -}}
{{- $ref := index $values .name | default (dict) -}}
{{- coalesce (index $ref .field) .default | quote -}}
{{- end -}}

{{/*
Override Duo Workflow key secret helpers
*/}}
{{- define "ai-gateway.duoWorkflowSigningKey.secret" -}}
{{- include "gitlab.ai-gateway.keyRef" (dict "context" . "name" "duoWorkflowSigningKey" "field" "secret" "default" (printf "%s-ai-gateway-duo-workflow-signing-secret" .Release.Name)) -}}
{{- end -}}

{{- define "ai-gateway.duoWorkflowSigningKey.key" -}}
{{- include "gitlab.ai-gateway.keyRef" (dict "context" . "name" "duoWorkflowSigningKey" "field" "key" "default" "duoWorkflowSigningKey") -}}
{{- end -}}

{{- define "ai-gateway.duoWorkflowValidationKey.secret" -}}
{{- include "gitlab.ai-gateway.keyRef" (dict "context" . "name" "duoWorkflowValidationKey" "field" "secret" "default" (printf "%s-ai-gateway-duo-workflow-validation-secret" .Release.Name)) -}}
{{- end -}}

{{- define "ai-gateway.duoWorkflowValidationKey.key" -}}
{{- include "gitlab.ai-gateway.keyRef" (dict "context" . "name" "duoWorkflowValidationKey" "field" "key" "default" "duoWorkflowValidationKey") -}}
{{- end -}}

{{/*
Override AI Gateway key secret helpers
*/}}
{{- define "ai-gateway.aigwSigningKey.secret" -}}
{{- include "gitlab.ai-gateway.keyRef" (dict "context" . "name" "aigwSigningKey" "field" "secret" "default" (printf "%s-ai-gateway-aigw-signing-secret" .Release.Name)) -}}
{{- end -}}

{{- define "ai-gateway.aigwSigningKey.key" -}}
{{- include "gitlab.ai-gateway.keyRef" (dict "context" . "name" "aigwSigningKey" "field" "key" "default" "aigwSigningKey") -}}
{{- end -}}

{{- define "ai-gateway.aigwValidationKey.secret" -}}
{{- include "gitlab.ai-gateway.keyRef" (dict "context" . "name" "aigwValidationKey" "field" "secret" "default" (printf "%s-ai-gateway-aigw-validation-secret" .Release.Name)) -}}
{{- end -}}

{{- define "ai-gateway.aigwValidationKey.key" -}}
{{- include "gitlab.ai-gateway.keyRef" (dict "context" . "name" "aigwValidationKey" "field" "key" "default" "aigwValidationKey") -}}
{{- end -}}

{{- define "gitlab.ai-gateway.hostname" -}}
{{- coalesce $.Values.global.hosts.aiGateway.name (include "gitlab.assembleHost"  (dict "name" "ai-gateway" "context" . )) -}}
{{- end }}

{{- define "gitlab.ai-gateway.grpcHostname" -}}
{{- printf "grpc-%s" (include "gitlab.ai-gateway.hostname" .) -}}
{{- end -}}


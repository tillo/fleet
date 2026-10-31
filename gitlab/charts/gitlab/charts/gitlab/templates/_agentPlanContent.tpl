{{/*
Generates a templated config for agent_plan_content key in gitlab.yml.

Usage:
{{ include "gitlab.appConfig.agentPlanContent.configuration" ( \
     dict                                                      \
         "config" .Values.path.to.agent_plan_content.config    \
         "context" $                                           \
     ) }}
*/}}
{{- define "gitlab.appConfig.agentPlanContent.configuration" -}}
agent_plan_content:
  enabled: {{ if kindIs "bool" .config.enabled }}{{ eq .config.enabled true }}{{ end }}
  {{- if not .context.Values.global.appConfig.object_store.enabled }}
  {{-   include "gitlab.appConfig.objectStorage.configuration" (dict "name" "agent_plan_content" "config" .config "context" .context) | nindent 2 }}
  {{- end }}
{{- end -}}{{/* "gitlab.appConfig.agentPlanContent.configuration" */}}

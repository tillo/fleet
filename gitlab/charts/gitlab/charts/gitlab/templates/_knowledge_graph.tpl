{{/* ######### Knowledge Graph related templates */}}

{{- define "gitlab.appConfig.orbit.config" -}}
{{- $orbit := .Values.global.appConfig.orbit -}}
{{- $knowledgeGraph := .Values.global.appConfig.knowledgeGraph -}}
{{- $config := $knowledgeGraph | default dict -}}
{{- if kindIs "map" $orbit -}}
{{- $config = $orbit -}}
{{- end -}}
{{- toYaml $config -}}
{{- end -}}{{/* "gitlab.appConfig.orbit.config" */}}

{{- define "gitlab.appConfig.knowledgeGraph.mountSecrets" -}}
{{- with include "gitlab.appConfig.orbit.config" . | fromYaml -}}
{{- if and .enabled .jwtSecret -}}
# mount secret for knowledge graph
- secret:
    name: {{ .jwtSecret.secret | quote }}
    items:
      - key: {{ default "secret" .jwtSecret.key | quote }}
        path: knowledge_graph/.gitlab_knowledge_graph_secret
{{- end -}}
{{- end -}}
{{- end -}}{{/* "gitlab.appConfig.knowledgeGraph.mountSecrets" */}}

{{- define "gitlab.appConfig.knowledgeGraph" -}}
{{- $orbit := include "gitlab.appConfig.orbit.config" . | fromYaml -}}
{{- if $orbit.enabled -}}
knowledge_graph:
  enabled: true
  secret_file: /etc/gitlab/knowledge_graph/.gitlab_knowledge_graph_secret
  grpc_endpoint: {{ $orbit.grpcEndpoint | quote }}
{{- end -}}
{{- end -}}

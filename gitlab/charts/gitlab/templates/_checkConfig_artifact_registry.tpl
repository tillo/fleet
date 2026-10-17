{{/*
Ensures that apiUrl is configured when artifactRegistry is enabled.

authToken is deliberately not checked: an environment may set the endpoint
before the shared service token exists, in which case Rails reads no credential
and the Artifact Registry service calls fail closed.
*/}}
{{- define "gitlab.checkConfig.artifactRegistry.apiUrl" -}}
  {{- with .Values.global.appConfig.artifactRegistry -}}
    {{- if .enabled -}}
      {{- if not .apiUrl }}
artifactRegistry:
    apiUrl is required when artifactRegistry is enabled. Please set `global.appConfig.artifactRegistry.apiUrl`.
      {{- end -}}
    {{- end -}}
  {{- end -}}
{{- end -}}
{{/* END gitlab.checkConfig.artifactRegistry.apiUrl */}}

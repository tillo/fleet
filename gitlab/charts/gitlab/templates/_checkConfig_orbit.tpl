{{/*
Ensure that only one Orbit configuration root is set.
*/}}
{{- define "gitlab.checkConfig.orbit.configurationRoots" -}}
{{- $global := .Values.global | default dict -}}
{{- $appConfig := get $global "appConfig" | default dict -}}
{{- $orbit := get $appConfig "orbit" -}}
{{- $knowledgeGraph := get $appConfig "knowledgeGraph" -}}
{{- if and (kindIs "map" $orbit) (kindIs "map" $knowledgeGraph) }}
global.appConfig:
    `global.appConfig.orbit` and `global.appConfig.knowledgeGraph` cannot both be set. Move the legacy `knowledgeGraph` configuration to `orbit`.
{{- end -}}
{{- end -}}
{{/* END gitlab.checkConfig.orbit.configurationRoots */}}

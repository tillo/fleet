{{/*
Generates a templated config for ci_catalog_bundles key in gitlab.yml.

Usage:
{{ include "gitlab.appConfig.ciCatalogBundles.configuration" ( \
     dict                                                      \
         "config" .Values.path.to.ci_catalog_bundles.config    \
         "context" $                                           \
     ) }}
*/}}
{{- define "gitlab.appConfig.ciCatalogBundles.configuration" -}}
ci_catalog_bundles:
  enabled: {{ if kindIs "bool" .config.enabled }}{{ eq .config.enabled true }}{{ end }}
  {{- if not .context.Values.global.appConfig.object_store.enabled }}
  {{-   include "gitlab.appConfig.objectStorage.configuration" (dict "name" "ci_catalog_bundles" "config" .config "context" .context) | nindent 2 }}
  {{- end }}
{{- end -}}{{/* "gitlab.appConfig.ciCatalogBundles.configuration" */}}

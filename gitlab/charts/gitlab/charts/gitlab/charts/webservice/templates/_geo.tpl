{{/*
Renders the ingress class field for Ingresses to handle Geo traffic.

It expects a dictionary with two entries:
  - `global` which contains global ingress settings, e.g. .Values.global.ingress
  - `context` which is the parent context (either `.` or `$`)
*/}}
{{- define "webservice.geo.ingress.class.field" -}}
{{- $class := include "gitlab.geo.ingress.class.name" .context -}}
{{- if not (eq $class "none") -}}
ingressClassName: {{ $class | quote }}
{{- end -}}
{{- end -}}

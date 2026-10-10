{{/* vim: set filetype=mustache: */}}

{{- define "gitlab.application.labels" -}}
app.kubernetes.io/name: {{ .Release.Name }}
{{- end -}}

{{- define "gitlab.standardLabels" -}}
app: {{ template "name" . }}
chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
release: {{ .Release.Name }}
heritage: {{ .Release.Service }}
{{- if .Values.global.application.create }}
{{ include "gitlab.application.labels" . }}
{{- end -}}
{{- end -}}


{{- define "gitlab.selectorLabels" -}}
app: {{ template "name" . }}
release: {{ .Release.Name }}
{{ if .Values.global.application.create -}}
{{ include "gitlab.application.labels" . }}
{{- end -}}
{{- end -}}

{{- define "gitlab.commonLabels" -}}
{{- $commonLabels := merge (pluck "labels" (default (dict) .Values.common) | first) .Values.global.common.labels}}
{{- if $commonLabels }}
{{-   range $key, $value := $commonLabels }}
{{ $key }}: {{ $value | quote }}
{{-   end }}
{{- end -}}
{{- end -}}

{{/* Deprecated, do not use these labels.*/}}
{{- define "gitlab.immutableLabels" -}}
app: {{ template "name" . }}
chart: {{ .Chart.Name }}
release: {{ .Release.Name }}
heritage: {{ .Release.Service }}
{{ if .Values.global.application.create -}}
{{ include "gitlab.application.labels" . }}
{{- end -}}
{{- end -}}


{{- define "gitlab.nodeSelector" -}}
{{- $nodeSelector := default .Values.global.nodeSelector .Values.nodeSelector -}}
{{- if $nodeSelector }}
nodeSelector:
  {{- toYaml $nodeSelector | nindent 2 }}
{{- end }}
{{- end -}}

{{- define "gitlab.tolerations" -}}
{{- $tolerations := default .Values.global.tolerations .Values.tolerations -}}
{{- if $tolerations }}
tolerations:
  {{- toYaml $tolerations | nindent 2 }}
{{- end }}
{{- end -}}

{{/*
Return priorityClassName for Pod definitions
*/}}
{{- define "gitlab.priorityClassName" -}}
{{- $pcName := default .Values.global.priorityClassName .Values.priorityClassName -}}
{{- if $pcName }}
priorityClassName: {{ $pcName }}
{{- end -}}
{{- end -}}

{{/*
Return dnsConfig for Pod definitions.

Resolves the effective dnsConfig with a local-first override pattern:
the sub-chart's `.Values.dnsConfig` takes precedence, falling back to
`.Values.global.dnsConfig`. When neither is set (the default), no
`dnsConfig` key is emitted, preserving existing behavior.

See https://kubernetes.io/docs/concepts/services-networking/dns-pod-service/#pod-dns-config
*/}}
{{- define "gitlab.dnsConfig" -}}
{{- $dnsConfig := default .Values.global.dnsConfig .Values.dnsConfig -}}
{{- if $dnsConfig }}
dnsConfig:
  {{- toYaml $dnsConfig | nindent 2 }}
{{- end }}
{{- end -}}

{{/*
Define a app.kubernetes.io/name: {{ .Chart.Name }} and app.kubernetes.io/version: {{ .Chart.AppVersion }} label for kiali on pods, deployments, statefulsets, and daemonsets.
*/}}
{{- define "gitlab.app.kubernetes.io.labels" -}}
{{ include "gitlab.application.labels" . }}
app.kubernetes.io/version: {{ coalesce .Values.imageTag (.image | default (dict)).tag (include "gitlab.versionTag" .) }}
{{- end -}}

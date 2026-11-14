{{/* Common templates for PodDisruptionBudget */}}

{{/*
Returns the appropriate apiVersion for PodDisruptionBudget.

It expects a dictionary with two entries:
  - `global` which contains global PDB settings, e.g. .Values.global.pdb
  - `local` which contains local PDB settings, e.g. .Values.sidekiq.pdb

Callers also pass `context` (the parent context, either `.` or `$`) for parity
with `gitlab.hpa.apiVersion` and `gitlab.ingress.apiVersion`. It is accepted but
not used, because the helper no longer probes `.Capabilities.APIVersions`.
*/}}
{{- define "gitlab.pdb.apiVersion" -}}
{{-   $local := default dict .local -}}
{{-   if (get $local "apiVersion") -}}
{{-     .local.apiVersion -}}
{{-   else if .global.apiVersion -}}
{{-     .global.apiVersion -}}
{{-   else -}}
{{-     print "policy/v1" -}}
{{-   end -}}
{{- end -}}

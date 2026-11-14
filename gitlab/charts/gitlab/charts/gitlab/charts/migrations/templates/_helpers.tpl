{{/* vim: set filetype=mustache: */}}

{{/*
Create a default fully qualified job name.
*/}}
{{- define "migrations.jobname" -}}
{{- $name := include "fullname" . | trunc 55 | trimSuffix "-" -}}
{{- printf "%s-%s" $name ( include "gitlab.jobNameSuffix" . ) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Create a default fully qualified job name for the batched-background-migrations wait Job.
The "-bbm" marker keeps it distinct from the schema-migrations Job of the same release,
and the fullname is truncated to leave room for the marker and the suffix within 63 chars.
*/}}
{{- define "migrations.batchedBackgroundMigrations.jobname" -}}
{{- $name := printf "%s-bbm" ( include "fullname" . | trunc 51 | trimSuffix "-" ) -}}
{{- printf "%s-%s" $name ( include "gitlab.jobNameSuffix" . ) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* vim: set filetype=mustache: */}}

{{- define "toolbox.backups.cron.persistence.persistentVolumeClaim" -}}
metadata:
{{- if not .Values.backups.cron.persistence.useGenericEphemeralVolume }}
  name: {{ template "fullname" . }}-backup-tmp
  namespace: {{ $.Release.Namespace }}
{{- end }}
  labels:
    {{- include "gitlab.standardLabels" . | nindent 4 }}
    {{- include "gitlab.commonLabels" . | nindent 4 }}
spec:
  accessModes:
    - {{ .Values.backups.cron.persistence.accessMode | quote }}
  resources:
    requests:
      storage: {{ .Values.backups.cron.persistence.size | quote }}
{{- if .Values.backups.cron.persistence.volumeName }}
  volumeName: {{ .Values.backups.cron.persistence.volumeName }}
{{- end }}
{{- if .Values.backups.cron.persistence.storageClass }}
{{- if (eq "-" .Values.backups.cron.persistence.storageClass) }}
  storageClassName: ""
{{- else }}
  storageClassName: "{{ .Values.backups.cron.persistence.storageClass }}"
{{- end -}}
{{- end }}
  selector:
{{- if .Values.backups.cron.persistence.matchLabels }}
    matchLabels:
      {{- toYaml .Values.backups.cron.persistence.matchLabels | nindent 6 }}
{{- end -}}
{{- if .Values.backups.cron.persistence.matchExpressions }}
    matchExpressions:
      {{- toYaml .Values.backups.cron.persistence.matchExpressions | nindent 6 }}
{{- end -}}
{{- end -}}

{{/*
Returns the secret configuring access to the object storage for backups.

Usage:
  {{ include "toolbox.backups.objectStorage.config.secret" .Values.backups.objectStorage }}

*/}}
{{- define "toolbox.backups.objectStorage.config.secret" -}}
{{-   if eq .backend "gcs" -}}
{{- if .config -}}
- secret:
    name: {{ .config.secret }}
    items:
      - key: {{ default "config" .config.key }}
        path: objectstorage/{{ default "config" .config.key }}
{{- end -}}
{{-   else if eq .backend "azure" -}}
- secret:
    name: {{ .config.secret }}
    items:
      - key: {{ default "config" .config.key }}
        path: objectstorage/azure_config
{{-   else -}}
- secret:
    name: {{ .config.secret }}
    items:
      - key: {{ default "config" .config.key }}
        path: {{ ternary ".s3cfg" "objectstorage/.s3cfg" (eq .backend "s3") }}
{{-   end -}}
{{- end -}}

{{/*
Return the secret name for toolbox registry database password
*/}}
{{- define "toolbox.registry.database.password.secret" -}}
{{- $database := default (dict) .Values.backups.registry.database -}}
{{- dig "password" "secret" "" $database | default (printf "%s-toolbox-registry-database-password" .Release.Name) -}}
{{- end -}}

{{/*
Return the secret key for toolbox registry database backup password
*/}}
{{- define "toolbox.registry.database.password.backupKey" -}}
{{- $database := default (dict) .Values.backups.registry.database -}}
{{- dig "password" "backupPasswordKey" "backupPassword" $database | default "backupPassword" -}}
{{- end -}}

{{/*
Return the secret key for toolbox registry database restore password
*/}}
{{- define "toolbox.registry.database.password.restoreKey" -}}
{{- $database := default (dict) .Values.backups.registry.database -}}
{{- dig "password" "restorePasswordKey" "restorePassword" $database | default "restorePassword" -}}
{{- end -}}

{{/*
Registry database configuration sources for the toolbox secrets volume.

These projected volume sources place registry metadata database credentials and
connection configuration under /etc/gitlab/registry-db/ for backup/restore
operations.

The sources are all optional, to maintain backward compatibility when registry
database is not configured:
1. Connection parameters (host, port, database, SSL config) from registry chart ConfigMap
2. Database usernames from toolbox ConfigMap
3. Database passwords from Secret

Usage:
  {{- include "toolbox.registry.databaseBackupCredentialsSources" . | nindent 10 }}

Files:
  - connection.env: Database connection parameters
  - backup-user.env / restore-user.env: Database usernames
  - backup-pass / restore-pass: Database passwords
*/}}
{{- define "toolbox.registry.databaseBackupCredentialsSources" -}}
- configMap:
    name: {{ include "gitlab.other.fullname" ( dict "context" . "chartName" "registry" ) }}-db-connection-config
    items:
    - key: db-connection.env
      path: registry-db/connection.env
    optional: true
- configMap:
    name: {{ template "fullname" . }}-registry-db-backuprestore-users
    items:
    - key: backup-user
      path: registry-db/backup-user.env
    - key: restore-user
      path: registry-db/restore-user.env
    optional: true
- secret:
    name: {{ include "toolbox.registry.database.password.secret" . }}
    items:
    - key: {{ template "toolbox.registry.database.password.backupKey" . }}
      path: registry-db/backup-pass
    - key: {{ template "toolbox.registry.database.password.restoreKey" . }}
      path: registry-db/restore-pass
    optional: true
{{- end -}}

{{/*
Return the secret name for toolbox openbao database backup/restore passwords.
Defaults to a generated name so the volume source never has name: "" — an empty
name fails Kubernetes admission even with optional: true.
Pattern matches toolbox.registry.database.password.secret.
*/}}
{{- define "toolbox.openbao.database.password.secret" -}}
{{- $database := default (dict) ((.Values.backups).openbao).database -}}
{{- (($database.password).secret) | default (printf "%s-toolbox-openbao-database-password" .Release.Name) -}}
{{- end -}}

{{/*
Return the secret key for the OpenBao database backup password.
*/}}
{{- define "toolbox.openbao.database.password.backupKey" -}}
{{- $database := default (dict) ((.Values.backups).openbao).database -}}
{{- (($database.password).backupPasswordKey) | default "backupPassword" -}}
{{- end -}}

{{/*
Return the secret key for the OpenBao database restore password.
*/}}
{{- define "toolbox.openbao.database.password.restoreKey" -}}
{{- $database := default (dict) ((.Values.backups).openbao).database -}}
{{- (($database.password).restorePasswordKey) | default "restorePassword" -}}
{{- end -}}

{{/*
OpenBao database configuration sources for the toolbox secrets volume.

Projects OpenBao DB credentials for backup/restore under /etc/gitlab/openbao-db/:
- connection.env: HOST, PORT, NAME from toolbox ConfigMap (no USER — operation-dependent)
- backup-user.env / restore-user.env: per-operation USERNAME from toolbox ConfigMap
- backup-pass / restore-pass: per-operation PASSWORD from Secret

Usage:
  {{- include "toolbox.openbao.databaseBackupCredentialsSources" . | nindent 10 }}
*/}}
{{- define "toolbox.openbao.databaseBackupCredentialsSources" -}}
- configMap:
    name: {{ template "fullname" . }}-openbao-db-connection-config
    items:
    - key: db-connection.env
      path: openbao-db/connection.env
    optional: true
- configMap:
    name: {{ template "fullname" . }}-openbao-db-backuprestore-users
    items:
    - key: backup-user
      path: openbao-db/backup-user.env
    - key: restore-user
      path: openbao-db/restore-user.env
    optional: true
- secret:
    name: {{ include "toolbox.openbao.database.password.secret" . }}
    items:
    - key: {{ template "toolbox.openbao.database.password.backupKey" . }}
      path: openbao-db/backup-pass
    - key: {{ template "toolbox.openbao.database.password.restoreKey" . }}
      path: openbao-db/restore-pass
    optional: true
{{- end -}}

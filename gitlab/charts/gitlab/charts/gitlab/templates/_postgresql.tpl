{{/*
Returns parts for a Gitlab configuration to setup a mutual TLS connection
with the PostgreSQL database.
*/}}
{{- define "gitlab.psql.ssl.config" -}}
{{- if .Values.global.psql.ssl }}
sslmode: verify-ca
sslrootcert: '/etc/gitlab/postgres/ssl/server-ca.pem'
sslcert: '/etc/gitlab/postgres/ssl/client-certificate.pem'
sslkey: '/etc/gitlab/postgres/ssl/client-key.pem'
{{- end -}}
{{- end -}}

{{/*
Returns the projected volume source that places the files required for a
mutual TLS connection at the paths of `gitlab.psql.ssl.config`, relative to
the secrets volume mounted at /etc/gitlab.

The client key is owned by root. The secrets volumes use `defaultMode: 0400`,
and the kubelet changes the group of each file to the pod's fsGroup and adds
group read, so the key ends up root:<fsGroup> 0440. libpq accepts root-owned
keys with permissions u=rw,g=r (0640) or less. Without an fsGroup, the key is
only readable by root, see "gitlab.postgresql.ssl.chartsWithoutFsGroup".
*/}}
{{- define "gitlab.psql.ssl.secretSource" -}}
{{- if .Values.global.psql.ssl }}
- secret:
    name: {{ .Values.global.psql.ssl.secret | required "Missing required secret containing SQL SSL certificates and keys. Make sure to set `global.psql.ssl.secret`" }}
    items:
      - key: {{ .Values.global.psql.ssl.serverCA | required "Missing required key name of SQL server certificate. Make sure to set `global.psql.ssl.serverCA`" }}
        path: postgres/ssl/server-ca.pem
      - key: {{ .Values.global.psql.ssl.clientCertificate | required "Missing required key name of SQL client certificate. Make sure to set `global.psql.ssl.clientCertificate`" }}
        path: postgres/ssl/client-certificate.pem
      - key: {{ .Values.global.psql.ssl.clientKey | required "Missing required key name of SQL client key file. Make sure to set `global.psql.ssl.clientKey`" }}
        path: postgres/ssl/client-key.pem
{{- end -}}
{{- end -}}
{{/*
Returns the K8s Secret definition for the PostgreSQL password.
*/}}
{{- define "gitlab.psql.secret" -}}
{{- $useSecret := include "gitlab.boolean.local" (dict "local" (pluck "useSecret" (index .Values.psql "password") | first) "global" .Values.global.psql.password.useSecret "default" true) -}}
{{- if $useSecret -}}
- secret:
    name: {{ template "gitlab.psql.password.secret" . }}
    items:
      - key: {{ template "gitlab.psql.password.key" . }}
        path: postgres/psql-password-{{ .Schema }}
{{- end -}}
{{- end -}}

{{/*
Returns the single-quoted path to the file where the PostgreSQL password is stored.
*/}}
{{- define "gitlab.psql.password.file" -}}
{{- $useSecret := include "gitlab.boolean.local" (dict "local" (pluck "useSecret" (index .Values.psql "password") | first) "global" .Values.global.psql.password.useSecret "default" true) -}}
{{- if not $useSecret -}}
{{- pluck "file" (index .Values.psql "password") (.Values.global.psql.password) | first | squote -}}
{{- else -}}
{{- printf "/etc/gitlab/postgres/psql-password-%s" .Schema | squote -}}
{{- end -}}
{{- end -}}

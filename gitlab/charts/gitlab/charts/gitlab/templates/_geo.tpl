{{/* ######## Geo related templates */}}
{{/*
Returns "secondary" if geo.enabled & geo.role: secondary
*/}}
{{- define "gitlab.geo.secondary" -}}
{{- with .Values.global.geo -}}
{{- if and (eq true (default false .enabled)) ( eq "secondary" (default "primary" (lower .role))) -}}
secondary
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "gitlab.geo.config" -}}
{{- if .Values.global.geo.enabled -}}
geo:
  node_name: {{ default "" .Values.global.geo.nodeName }}
  registry_replication:
    enabled: {{ eq true (default false .Values.global.geo.registry.replication.enabled) }}
    primary_api_url: {{ .Values.global.geo.registry.replication.primaryApiUrl | quote }}
{{- end -}}
{{- end -}}

{{/*
Returns the contents of the `database_geo.yml` blob

As the Rails application checks for the existance of `database_geo.yml`,
this should not be included _at all_ unless `gitlab.geo.secondary`. As
such, we don't check that state here.
*/}}
{{- define "gitlab.geo.database.yml" -}}
geo:
  adapter: postgresql
  encoding: unicode
  database_tasks: true
  database: {{ template "gitlab.geo.psql.database" . }}
  username: {{ template "gitlab.geo.psql.username" . }}
  password: <%= File.read("/etc/gitlab/postgres/geo-psql-password").strip.to_json %>
  host: {{ template "gitlab.geo.psql.host" . }}
  port: {{ template "gitlab.geo.psql.port" . }}
  # load_balancing:
  #   hosts:
  #     - host1.example.com
  #     - host2.example.com
  {{- include "gitlab.geo.psql.ssl.config" . | nindent 2 }}
{{- end -}}

{{/*
Returns parts for a Gitlab configuration to setup a mutual TLS connection
with the Geo PostgreSQL database.

Consumed as part of "gitlab.geo.database.yml", no check of `global.geo.secondary`
*/}}
{{- define "gitlab.geo.psql.ssl.config" -}}
{{- if .Values.global.geo.psql.ssl }}
sslmode: verify-ca
sslrootcert: '/etc/gitlab/postgres/ssl/geo-server-ca.pem'
sslcert: '/etc/gitlab/postgres/ssl/geo-client-certificate.pem'
sslkey: '/etc/gitlab/postgres/ssl/geo-client-key.pem'
{{- end -}}
{{- end -}}

{{/*
Returns the projected volume source that places the files required for a
mutual TLS connection to the Geo Secondary DB at the paths of
`gitlab.geo.psql.ssl.config`. See `gitlab.psql.ssl.secretSource` for the
permissions of the client key.
*/}}
{{- define "gitlab.geo.psql.ssl.secretSource" -}}
{{- if and ( include "gitlab.geo.secondary" $ ) .Values.global.geo.psql.ssl }}
- secret:
    name: {{ .Values.global.geo.psql.ssl.secret | required "Missing required secret containing SQL SSL certificates and keys. Make sure to set `global.geo.psql.ssl.secret`" }}
    items:
      - key: {{ .Values.global.geo.psql.ssl.serverCA | required "Missing required key name of SQL server certificate. Make sure to set `global.geo.psql.ssl.serverCA`" }}
        path: postgres/ssl/geo-server-ca.pem
      - key: {{ .Values.global.geo.psql.ssl.clientCertificate | required "Missing required key name of SQL client certificate. Make sure to set `global.geo.psql.ssl.clientCertificate`" }}
        path: postgres/ssl/geo-client-certificate.pem
      - key: {{ .Values.global.geo.psql.ssl.clientKey | required "Missing required key name of SQL client key file. Make sure to set `global.geo.psql.ssl.clientKey`" }}
        path: postgres/ssl/geo-client-key.pem
{{- end -}}
{{- end -}}

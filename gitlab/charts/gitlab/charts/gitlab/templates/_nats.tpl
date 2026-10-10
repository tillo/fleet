{{/* ######### NATS related templates */}}

{{/*
Renders the `nats` block of gitlab.yml (audit event streaming).

An empty (or absent) `servers` list means NATS is not configured, so the block
is omitted and Rails falls back to the Sidekiq delivery path.
*/}}
{{- define "gitlab.appConfig.nats" -}}
{{- $context := . -}}
{{- with .Values.global.appConfig.nats -}}
{{- if .servers -}}
nats:
  servers:
  {{- range .servers }}
    - {{ . | quote }}
  {{- end }}
  {{- if .connectTimeout }}
  connect_timeout: {{ .connectTimeout | int }}
  {{- end }}
  {{- if .streamReplicas }}
  stream_replicas: {{ .streamReplicas | int }}
  {{- end }}
  {{- if eq (include "gitlab.appConfig.nats.tls.enabled" $context) "true" }}
  tls:
    ca_file: "/srv/gitlab/config/nats/ca.crt"
    cert: "/srv/gitlab/config/nats/tls.crt"
    key: "/srv/gitlab/config/nats/tls.key"
  {{- end }}
{{- end -}}
{{- end -}}
{{- end -}}{{/* "gitlab.appConfig.nats" */}}

{{/*
Return the NATS TLS Secret name for the calling component (component-local
`nats.tls.secret`, then `global.appConfig.nats.tls.secret`), or an empty string
when none is configured. No release-derived default: a component without a
configured secret must not mount one that does not exist.
Usage: {{ include "gitlab.appConfig.nats.tls.secret" $ }}
*/}}
{{- define "gitlab.appConfig.nats.tls.secret" -}}
{{- $local := (($.Values.nats).tls).secret -}}
{{- $global := (($.Values.global.appConfig.nats).tls).secret -}}
{{- default $global $local | default "" -}}
{{- end -}}

{{/*
Whether the NATS TLS files should be mounted: servers present, TLS enabled, and
a secret resolvable for this component.
Usage: {{ if eq (include "gitlab.appConfig.nats.tls.enabled" $) "true" }}
*/}}
{{- define "gitlab.appConfig.nats.tls.enabled" -}}
{{- $nats := .Values.global.appConfig.nats -}}
{{- $secret := include "gitlab.appConfig.nats.tls.secret" . -}}
{{- if and $nats $nats.servers ($nats.tls).enabled (ne $secret "") -}}
true
{{- end -}}
{{- end -}}

{{/*
Mount NATS TLS secrets in projected volume sources.
Usage: {{ include "gitlab.appConfig.nats.mountSecrets" $ | nindent 10 }}
*/}}
{{- define "gitlab.appConfig.nats.mountSecrets" -}}
{{- if eq (include "gitlab.appConfig.nats.tls.enabled" $) "true" }}
# mount secret for NATS mutual TLS
- secret:
    name: {{ include "gitlab.appConfig.nats.tls.secret" $ }}
    items:
      - key: "ca.crt"
        path: "nats/ca.crt"
      - key: "tls.crt"
        path: "nats/tls.crt"
      - key: "tls.key"
        path: "nats/tls.key"
{{- end }}
{{- end -}}

{{/*
Volume mounts for NATS TLS files.
Usage: {{ include "gitlab.appConfig.nats.volumeMounts" (dict "context" $ "secretsVolumeName" "webservice-secrets") | nindent 12 }}
*/}}
{{- define "gitlab.appConfig.nats.volumeMounts" -}}
{{- $context := .context -}}
{{- if eq (include "gitlab.appConfig.nats.tls.enabled" $context) "true" }}
- name: {{ .secretsVolumeName }}
  mountPath: /srv/gitlab/config/nats/ca.crt
  subPath: nats/ca.crt
  readOnly: true
- name: {{ .secretsVolumeName }}
  mountPath: /srv/gitlab/config/nats/tls.crt
  subPath: nats/tls.crt
  readOnly: true
- name: {{ .secretsVolumeName }}
  mountPath: /srv/gitlab/config/nats/tls.key
  subPath: nats/tls.key
  readOnly: true
{{- end }}
{{- end -}}

{{/*
Configure script that materializes the NATS TLS files into the secrets directory.
Usage: {{ include "gitlab.appConfig.nats.configureScript" $ | nindent 4 }}
*/}}
{{- define "gitlab.appConfig.nats.configureScript" -}}
{{- if eq (include "gitlab.appConfig.nats.tls.enabled" $) "true" }}
  if [ -d /init-config/nats ]; then
    mkdir -p /init-secrets/nats
    cp -v -L /init-config/nats/ca.crt /init-secrets/nats/ca.crt
    cp -v -L /init-config/nats/tls.crt /init-secrets/nats/tls.crt
    cp -v -L /init-config/nats/tls.key /init-secrets/nats/tls.key
  fi
{{- end }}
{{- end -}}

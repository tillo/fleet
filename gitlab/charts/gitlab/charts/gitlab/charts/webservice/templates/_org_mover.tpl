{{/* ######### Org Mover related templates */}}

{{/*
Org Mover internal API trusted public keys, for GitLab Cells.
Only webservice serves the Org Mover internal API, so only webservice
receives the keys.

Each entry of global.appConfig.orgMover.publicKeys is projected into the
secrets volume as "org-mover/<index>.pem". The webservice container reads
it at "/etc/gitlab/org-mover/<index>.pem". The helpers in this file must
agree on that path.
*/}}

{{/*
Renders the `org_mover` block of gitlab.yml.
Usage: {{- with include "webservice.orgMover.appConfig" . }}{{ . | nindent 6 }}{{- end }}
*/}}
{{- define "webservice.orgMover.appConfig" -}}
{{- with (dig "orgMover" "publicKeys" list .Values.global.appConfig) }}
org_mover:
  public_key_files:
  {{- range $index, $_ := . }}
    - "/etc/gitlab/org-mover/{{ $index }}.pem"
  {{- end }}
{{- end }}
{{- end -}}{{/* "webservice.orgMover.appConfig" */}}

{{/*
Mount Org Mover public key secrets in projected volume sources.
Usage: {{- with include "webservice.orgMover.mountSecrets" $ }}{{ . | nindent 10 }}{{- end }}
*/}}
{{- define "webservice.orgMover.mountSecrets" -}}
{{- range $index, $publicKey := (dig "orgMover" "publicKeys" list $.Values.global.appConfig) }}
- secret:
    name: {{ required (printf "global.appConfig.orgMover.publicKeys[%d].secret is required" $index) $publicKey.secret | quote }}
    items:
      - key: {{ default "public.pem" $publicKey.key | quote }}
        path: "org-mover/{{ $index }}.pem"
{{- end }}
{{- end -}}{{/* "webservice.orgMover.mountSecrets" */}}

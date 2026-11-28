{{/*
The registry config.yml, as a gomplate template that the configure init
container renders.
*/}}
{{- define "registry.config.tpl" -}}
version: 0.1
log:
  {{ toYaml .Values.log | nindent 2 | trim }}
http:
  addr: :{{ .Values.service.internalPort }}
  # `host` is not configurable
  # `prefix` is not configurable
  {{- if .Values.tls.enabled }}
  tls:
    certificate: /etc/docker/registry/tls/tls.crt
    key: /etc/docker/registry/tls/tls.key
    {{- if and .Values.tls.clientCAs (kindIs "slice" .Values.tls.clientCAs) }}
    clientCAs: {{- toYaml .Values.tls.clientCAs | nindent 6 }}
    {{- end }}
    {{- if and .Values.tls.cipherSuites (kindIs "slice" .Values.tls.cipherSuites) }}
    ciphersuites: {{- toYaml .Values.tls.cipherSuites | nindent 6 }}
    {{- end }}
    minimumTLS: {{ default "tls1.2" .Values.tls.minimumTLS | quote }}
  {{- end }}
  {{- if .Values.debug }}
  debug:
    addr: :{{ .Values.debug.addr.port | int }}
    prometheus:
      enabled: {{ (or .Values.metrics.enabled .Values.debug.prometheus.enabled) }}
      path: {{ default .Values.metrics.path .Values.debug.prometheus.path }}
    {{- if .Values.debug.tls.enabled }}
    tls:
      enabled: true
      {{- if .Values.debug.tls.secretName }}
      certificate: /etc/docker/registry/tls/tls-debug.crt
      key: /etc/docker/registry/tls/tls-debug.key
      {{-   if and .Values.debug.tls.clientCAs (kindIs "slice" .Values.debug.tls.clientCAs) }}
      clientCAs: {{- toYaml .Values.debug.tls.clientCAs | nindent 8 }}
      {{-   end }}
      minimumTLS: {{ .Values.debug.tls.minimumTLS | quote }}
      {{- end }}
    {{- end }}
  {{- end }}
  draintimeout: {{ .Values.draintimeout }}
  headers:
    X-Content-Type-Options: [nosniff]
  secret: {{ include "registry.config.secret" (dict "context" $ "placeholder" "HTTP_SECRET" "file" "httpSecret") }}
  relativeurls: {{ .Values.relativeurls }}
health:
  {{- if kindIs "map" .Values.health }}
  {{ toYaml .Values.health | nindent 2 | trim }}
  {{- end }}
auth:
  token:
    realm: {{ template "registry.authEndpoint" . }}/jwt/auth
    service: {{ .Values.tokenService }}
    issuer: {{ coalesce .Values.tokenIssuer  (dig "registry" "tokenIssuer" "gitlab-issuer" .Values.global ) }}
    # This is provided from the initContainer execution, at a known path.
    rootcertbundle: /etc/docker/registry/certificate.crt
    autoredirect: {{ .Values.authAutoRedirect }}
validation:
  disabled: {{ eq true .Values.validation.disabled }}
  {{- with .Values.validation.manifests }}
  manifests:
    referencelimit: {{ .referencelimit }}
    payloadsizelimit: {{ .payloadsizelimit }}
    urls:
      allow:
    {{- range .urls.allow }}
      - {{ . }}
    {{- end }}
      deny:
    {{- range .urls.deny }}
      - {{ . }}
    {{- end }}
  {{- end }}
{{- include "registry.notifications.config" $ | nindent 0 }}
reporting:
  {{- if .Values.reporting.sentry.enabled }}
  sentry:
    enabled: true
    dsn: {{ .Values.reporting.sentry.dsn }}
    {{- if .Values.reporting.sentry.environment }}
    environment: {{ .Values.reporting.sentry.environment }}
    {{- end }}
  {{- end }}
profiling:
  {{- if .Values.profiling.stackdriver.enabled }}
  stackdriver:
    enabled: true
    service: {{ template "gitlab.registry.profiling.stackdriver.service" . }}
    serviceversion: {{ .Values.image.tag }}
    {{- if .Values.profiling.stackdriver.projectid }}
    projectid: {{ .Values.profiling.stackdriver.projectid }}
    {{- end }}
    {{- if .Values.profiling.stackdriver.credentials.secret }}
    keyfile: /etc/docker/registry/profiling-key.json
    {{- end }}
  {{- end }}
storage:
{{- if and .Values.storage.secret (not .Values.init.script) }}
{{ include "registry.config.secretYaml" (dict "context" $ "file" "storage/config" "indent" 2) }}
  {{ include "registry.gomplate" (dict "context" $ "expression" (include "registry.config.storageDeleteDefault" .)) }}
{{- end }}
  maintenance:
    readonly:
      enabled: {{ .Values.maintenance.readonly.enabled }}
    uploadpurging:
      enabled: {{ .Values.maintenance.uploadpurging.enabled }}
      age: {{ .Values.maintenance.uploadpurging.age }}
      interval: {{ .Values.maintenance.uploadpurging.interval }}
      dryrun: {{ .Values.maintenance.uploadpurging.dryrun }}
  {{- if .Values.storage }}
  {{- with .Values.storage.redirect }}
  redirect:
    disable: {{ default false .disable }}
    exceptions:
    {{- range .exceptions }}
      - {{ . }}
    {{- end }}
  {{- end }}
  {{- else }}
  {{- include (print $.Template.BasePath "/_storage_default.yaml") . | nindent 2}}
  {{- end }}
middleware:
{{- if .Values.middleware.storage }}
  storage:
  {{ range $index, $v := .Values.middleware.storage }}
  - name: {{ $v.name }}
    options: {{ omit $v.options "privatekeySecret" "privatekey" | toYaml | nindent 6 }}
      {{- if $v.options.privatekeySecret }}
      privatekey: {{ printf "/etc/docker/registry/middleware.storage/%d/%s" $index $v.options.privatekeySecret.key }}
      {{ end }}
  {{ end }}
{{- end }}
{{- include "registry.database.config" $ | nindent 0 }}
{{- include "registry.gc.config" $ | nindent 0 }}
{{- include "registry.redis.config" $ | nindent 0}}
{{- include "registry.rateLimiter.config" $ | nindent 0 }}
{{- end -}}

{{/*
Minimal configuration, in order to do database migrations.
*/}}
{{- define "registry.migrations.config.tpl" -}}
version: 0.1
log:
  {{ toYaml .Values.log | nindent 2 | trim }}
{{- include "registry.database.config" $ | nindent 0 }}
{{- end -}}

{{/*
Gomplate expression that prints the `delete.enabled: true` default unless the
storage Secret sets `delete.enabled`, either in flow style
(`delete: {enabled: false}`) or in block style, where blank, comment and other
indented lines may come before `enabled:`. It matches a regex because
`data.YAML` rejects repeated keys, which the registry accepts.
*/}}
{{- define "registry.config.storageDeleteDefault" -}}
{{- $flow := `\{[^}\n]*enabled[ \t]*:` -}}
{{- $block := `(?:#.*)?\n(?:(?:[ \t#].*)?\n)*[ \t]+enabled[ \t]*:` -}}
{{- $setsEnabled := printf `(?m)^delete[ \t]*:[ \t]*(?:%s|%s)` $flow $block -}}
{{- printf `file.Read "/config/storage/config" | strings.ReplaceAll "\r\n" "\n" | regexp.Match %q | test.Ternary "" "delete:\n    enabled: true"` $setsEnabled -}}
{{- end -}}

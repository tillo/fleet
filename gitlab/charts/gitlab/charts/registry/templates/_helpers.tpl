{{/* vim: set filetype=mustache: */}}

{{/*
Return the registry authEndpoint
Defaults to the globally set gitlabHostname if an authEndpoint hasn't been provided
to the chart
*/}}
{{- define "registry.authEndpoint" -}}
{{- if .Values.authEndpoint -}}
{{- .Values.authEndpoint -}}
{{- else -}}
{{- template "gitlab.gitlab.url" . -}}
{{- end -}}
{{- end -}}

{{/*
Returns the hostname.
If the hostname is set in `global.hosts.registry.name`, that will be returned,
otherwise the hostname will be assembed using `registry` as the prefix, and the `gitlab.assembleHost` function.
*/}}
{{- define "registry.hostname" -}}
{{- coalesce .Values.global.hosts.registry.name (include "gitlab.assembleHost"  (dict "name" "registry" "context" . )) -}}
{{- end -}}

{{/*
Returns the secret name for the Secret containing the TLS certificate and key.
Uses `ingress.tls.secretName` first and falls back to `global.ingress.tls.secretName`
if there is a shared tls secret for all ingresses.
*/}}
{{- define "registry.tlsSecret" -}}
{{- $defaultName := (dict "secretName" "") -}}
{{- if .Values.global.ingress.configureCertmanager -}}
{{- $_ := set $defaultName "secretName" (printf "%s-registry-tls" .Release.Name) -}}
{{- else -}}
{{- $_ := set $defaultName "secretName" (include "gitlab.wildcard-self-signed-cert-name" .) -}}
{{- end -}}
{{- pluck "secretName" .Values.ingress.tls .Values.global.ingress.tls $defaultName | first -}}
{{- end -}}

{{/*
Populate registry notifications
*/}}
{{- define "registry.notifications.config" -}}
{{- $geoNotifier := include "global.geo.registry.syncNotifier" . | fromYaml -}}
{{- $notifications := merge $.Values.global.registry.notifications $geoNotifier -}}
{{- if $notifications }}
notifications:
  {{- if $notifications.events }}
  events:
    {{- toYaml $.Values.global.registry.notifications.events | nindent 4 }}
  {{- end -}}
  {{- $endpoints := concat (list) $notifications.endpoints $geoNotifier.endpoints | uniq -}}
  {{- if $endpoints }}
  endpoints:
    {{- range $endpoint := $endpoints -}}
      {{- if $endpoint.name -}}
        {{- $headers := pluck "headers" $endpoint | first -}}
        {{- $endpoint = omit $endpoint "headers" }}
        {{- toYaml (list $endpoint) | nindent 4 }}
        {{- if $headers }}
      headers:
          {{- range $header, $value := $headers -}}
            {{- if kindIs "map" $value -}}
              {{- if hasKey $value "secret" }}
                {{- $fileName := printf "SECRET_%s_%s" $value.secret (default "value" $value.key) }}
                {{- if $.Values.init.script }}
        {{ $header }}: {{ $fileName }}
                {{- else }}
        {{ $header }}:
{{ include "registry.config.secretYaml" (dict "context" $ "file" (printf "notifications/%s" $fileName) "indent" 10) }}
                {{- end }}
              {{- end -}}
            {{- else }}
        {{ $header }}: {{ $value }}
            {{- end -}}
          {{- end -}}
        {{- end -}}
      {{- end -}}
    {{- end -}}
  {{- end -}}
{{- end -}}
{{- end -}}

{{/*
Individual secret items to be used as volumes
Usage:
{{ include "registry.notifications.secrets.item " ( \
     dict
         "value" $value
         "fileName" $fileName
     ) }}
*/}}
{{- define "registry.notifications.secrets.item" -}}
- secret:
    name: {{ .value.secret }}
    items:
      - key: {{ default "value" .value.key }}
        path: notifications/SECRET_{{ .fileName }}
{{- end }}

{{/*
Sensitive registry notification headers mounted as secrets
*/}}
{{- define "registry.notifications.secrets" -}}
{{- $geoNotifier := include "global.geo.registry.syncNotifier" . | fromYaml -}}
{{- $notifications := merge $.Values.global.registry.notifications $geoNotifier -}}
{{- if $notifications }}
  {{- $uniqSecrets := list -}}
  {{- $endpoints := concat (list) $notifications.endpoints $geoNotifier.endpoints | uniq -}}
  {{- range $endpoint := $endpoints -}}
    {{- if and $endpoint.name $endpoint.headers -}}
      {{- range $header, $value := $endpoint.headers -}}
        {{- if kindIs "map" $value -}}
          {{- if hasKey $value "secret" }}
            {{- $fileName := printf "%s_%s" $value.secret (default "value" $value.key) -}}
            {{- if not (has $fileName $uniqSecrets) }}
              {{- $uniqSecrets = append $uniqSecrets $fileName }}
{{ include "registry.notifications.secrets.item" (dict "value" $value "fileName" $fileName) }}
            {{- end -}}
          {{- end -}}
        {{- end -}}
      {{- end -}}
    {{- end -}}
  {{- end -}}
{{- end -}}
{{- end -}}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
*/}}
{{- define "registry.fullname" -}}
{{- include "fullname" $ -}}
{{- end -}}
{{/*
Return the sub-chart serviceAccount name
If that is not present it will use the global chart serviceAccount name
Failing that a serviceAccount will be generated automatically
*/}}
{{- define "registry.serviceAccount.name" -}}
{{- coalesce .Values.serviceAccount.name .Values.global.serviceAccount.name ( include "registry.fullname" . ) -}}
{{- end -}}

{{/*
Create a default fully qualified job name.
*/}}
{{- define "registry.migrations.jobname" -}}
{{- $name := include "registry.fullname" . | trunc 55 | trimSuffix "-" -}}
{{- printf "%s-migrations-%s" $name ( include "gitlab.jobNameSuffix" . ) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Optionally create a node affinity rule to optionally deploy registry pods in a specific zone
*/}}
{{- define "registry.affinity" -}}
{{- $affinityOptions := list "hard" "soft" }}
{{- if or
  (has (default .Values.global.antiAffinity "") $affinityOptions)
  (has (default .Values.antiAffinity "") $affinityOptions)
  (has (default .Values.global.nodeAffinity "") $affinityOptions)
  (has (default .Values.nodeAffinity "") $affinityOptions)
}}
affinity:
  {{- if eq (default .Values.global.antiAffinity .Values.antiAffinity) "hard" }}
    podAntiAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        - topologyKey: {{ default .Values.global.affinity.podAntiAffinity.topologyKey .Values.affinity.podAntiAffinity.topologyKey | quote }}
          labelSelector:
            matchLabels:
              app: {{ template "name" . }}
              release: {{ .Release.Name }}
  {{- else if eq (default .Values.global.antiAffinity .Values.antiAffinity) "soft" }}
    podAntiAffinity:
      preferredDuringSchedulingIgnoredDuringExecution:
        - weight: 1
          podAffinityTerm:
            topologyKey: {{ default .Values.global.affinity.podAntiAffinity.topologyKey .Values.affinity.podAntiAffinity.topologyKey | quote }}
            labelSelector:
              matchLabels:
                app: {{ template "name" . }}
                release: {{ .Release.Name }}
  {{- end -}}
  {{- if eq (default .Values.global.nodeAffinity .Values.nodeAffinity) "hard" }}
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
          - matchExpressions:
              - key: {{ default .Values.global.affinity.nodeAffinity.key .Values.affinity.nodeAffinity.key | quote }}
                operator: In
                values: {{ default .Values.global.affinity.nodeAffinity.values .Values.affinity.nodeAffinity.values | toYaml | nindent 16 }}

  {{- else if eq (default .Values.global.nodeAffinity .Values.nodeAffinity) "soft" }}
    nodeAffinity:
      preferredDuringSchedulingIgnoredDuringExecution:
        - weight: 1
          preference:
            matchExpressions:
              - key: {{ default .Values.global.affinity.nodeAffinity.key .Values.affinity.nodeAffinity.key | quote }}
                operator: In
                values: {{ default .Values.global.affinity.nodeAffinity.values .Values.affinity.nodeAffinity.values | toYaml | nindent 16 }}
  {{- end -}}
{{- end -}}
{{- end }}

{{/*
Render the standard labels for resources related to the registry migration.
These differ from the standard labels so the migration related Pod's are not
matched by the registry PDB and Deployment selectors.
*/}}
{{- define "registry.migration.standardLabels" -}}
{{- $labels := (include "gitlab.standardLabels" .) | fromYaml }}
{{- $_ := set $labels "app" "registry-migrations" }}
{{- toYaml $labels }}
{{- end -}}

{{/*
Emit a gomplate action into config.yml.tpl. The action is recorded in the
context and a placeholder is emitted in its place, which
"registry.gomplate.render" swaps for the action. Values cannot produce the
placeholder, because it contains NUL characters that the render rejects in
anything else.
*/}}
{{- define "registry.gomplate" -}}
{{- $actions := default (list) .context.registryGomplateActions -}}
{{- $_ := set .context "registryGomplateActions" (append $actions (printf "{%% %s %%}" .expression)) -}}
{{- include "registry.gomplate.placeholder" (len $actions) -}}
{{- end -}}

{{- define "registry.gomplate.placeholder" -}}
{{- printf "\x00gomplate-action-%d\x00" (int .) -}}
{{- end -}}

{{/*
Render the named `template` into a gomplate template.

With `escape`, every `{%` that values put into the text is escaped, so that
gomplate prints it instead of evaluating it. The placeholders emitted by
"registry.gomplate" while rendering are then replaced by their actions.

Rendering fails if the text holds a NUL character other than one copy of each
placeholder, so a value cannot forge an action, or if a placeholder is missing.

Usage:
{{ include "registry.gomplate.render" (dict "context" $ "template" "registry.config.tpl" "escape" true) }}
*/}}
{{- define "registry.gomplate.render" -}}
{{- $start := len (default (list) .context.registryGomplateActions) -}}
{{- $text := include .template .context -}}
{{- $actions := default (list) .context.registryGomplateActions -}}
{{- $unclaimed := $text -}}
{{- range $index := untilStep $start (len $actions) 1 -}}
{{-   $parts := splitn (include "registry.gomplate.placeholder" $index) 2 $unclaimed -}}
{{-   $unclaimed = print $parts._0 (default "" $parts._1) -}}
{{- end -}}
{{- if contains "\x00" $unclaimed -}}
{{-   fail "registry: a value rendered into the registry configuration contains a NUL character." -}}
{{- end -}}
{{- range $index := untilStep $start (len $actions) 1 -}}
{{-   if not (contains (include "registry.gomplate.placeholder" $index) $text) -}}
{{-     fail (printf "registry: placeholder %d of the gomplate template %s is missing." $index $.template) -}}
{{-   end -}}
{{- end -}}
{{- if .escape -}}
{{-   $text = replace "{%" "{%\"{%\"%}" $text -}}
{{- end -}}
{{- range $index := untilStep $start (len $actions) 1 -}}
{{-   $text = replace (include "registry.gomplate.placeholder" $index) (index $actions $index) $text -}}
{{- end -}}
{{- $text -}}
{{- end -}}

{{/*
Render a secret as a string in config.yml.tpl.

With a custom `init.script`, emit the legacy placeholder for the script to
replace. Otherwise gomplate reads the mounted file and emits it as a JSON
string, which is valid YAML for any content. Trailing newlines are dropped,
as the legacy `$(cat file)` did.

Usage:
{{ include "registry.config.secret" (dict "context" $ "placeholder" "HTTP_SECRET" "file" "httpSecret") }}
*/}}
{{- define "registry.config.secret" -}}
{{- if .context.Values.init.script -}}
{{ .placeholder | quote }}
{{- else -}}
{{- $expression := printf "file.Read %q | strings.TrimRight \"\\n\" | data.ToJSON" (printf "/config/%s" .file) -}}
{{- include "registry.gomplate" (dict "context" .context "expression" $expression) -}}
{{- end -}}
{{- end -}}

{{/*
Render a secret that holds YAML into config.yml.tpl, indented by `indent`
spaces. The registry parses the content as YAML, as it did when the legacy
script inserted it. Must be placed at the start of a line.

Usage:
{{ include "registry.config.secretYaml" (dict "context" $ "file" "storage/config" "indent" 2) }}
*/}}
{{- define "registry.config.secretYaml" -}}
{{- $expression := printf "file.Read %q | strings.TrimRight \"\\n\" | strings.Indent %d" (printf "/config/%s" .file) (int .indent) -}}
{{- include "registry.gomplate" (dict "context" .context "expression" $expression) -}}
{{- end -}}

{{/*
File mode for the TLS and database key material mounted into the registry.
With an fsGroup, the kubelet makes the files readable by that group. Without
one, they are owned by root and must be world-readable for the registry user.
*/}}
{{- define "registry.keyFileMode" -}}
{{- if dig "fsGroup" "" (default (dict) .Values.securityContext) -}}
0440
{{- else -}}
0444
{{- end -}}
{{- end -}}

{{/*
Projected volume sources for the files that config.yml references, other than
config.yml itself and the database client certificates.
*/}}
{{- define "registry.files.sources" -}}
- secret:
    name: {{ template "gitlab.registry.certificate.secret" . }}
    items:
      - key: registry-auth.crt
        path: certificate.crt
{{- if and .Values.storage.secret .Values.storage.extraKey }}
- secret:
    name: {{ .Values.storage.secret }}
    items:
      - key: {{ .Values.storage.extraKey }}
        path: storage/{{ .Values.storage.extraKey }}
        mode: {{ include "registry.keyFileMode" . }}
{{- end }}
{{- range $index, $v := .Values.middleware.storage }}
{{-   if $v.options.privatekeySecret }}
- secret:
    name: {{ $v.options.privatekeySecret.secret }}
    items:
      - key: {{ $v.options.privatekeySecret.key }}
        path: {{ printf "middleware.storage/%d/%s" $index $v.options.privatekeySecret.key }}
        mode: {{ include "registry.keyFileMode" $ }}
{{-   end }}
{{- end }}
{{- if .Values.profiling.stackdriver.credentials.secret }}
- secret:
    name: {{ .Values.profiling.stackdriver.credentials.secret }}
    items:
      - key: {{ template "gitlab.registry.profiling.stackdriver.credentials.key" . }}
        path: profiling-key.json
        mode: {{ include "registry.keyFileMode" . }}
{{- end }}
{{- if .Values.tls.enabled }}
- secret:
    name: {{ include "gitlab.registry.tls.secret" . }}
    items:
      - key: tls.crt
        path: tls/tls.crt
        mode: {{ include "registry.keyFileMode" . }}
      - key: tls.key
        path: tls/tls.key
        mode: {{ include "registry.keyFileMode" . }}
{{- end }}
{{- if and .Values.debug.tls.enabled .Values.debug.tls.secretName }}
- secret:
    name: {{ .Values.debug.tls.secretName }}
    items:
      - key: tls.crt
        path: tls/tls-debug.crt
        mode: {{ include "registry.keyFileMode" . }}
      - key: tls.key
        path: tls/tls-debug.key
        mode: {{ include "registry.keyFileMode" . }}
{{- end }}
{{- end -}}

{{/*
Command of the configure init container when no custom `init.script` is set.
*/}}
{{- define "registry.configure.command" -}}
["gomplate", "--left-delim", "{%", "--right-delim", "%}", "--file", "/templates/config.yml.tpl", "--out", "/registry/config.yml"]
{{- end -}}

{{/*
Volume mounts that place the rendered config.yml and the files it references
under /etc/docker/registry.

With a custom `init.script`, the script copies everything into the
registry-server-config volume. Otherwise each file is mounted on its own,
because a volume cannot be mounted inside a read-only one. Set `database` to
mount only what the database configuration needs.

Usage:
{{ include "registry.server.volumeMounts" (dict "context" $ "database" false) }}
*/}}
{{- define "registry.server.volumeMounts" -}}
{{- $values := .context.Values -}}
{{- if $values.init.script -}}
- name: registry-server-config
  mountPath: /etc/docker/registry/
  readOnly: true
{{- else -}}
- name: registry-server-config
  mountPath: /etc/docker/registry/config.yml
  subPath: config.yml
  readOnly: true
{{- if not .database }}
- name: registry-files
  mountPath: /etc/docker/registry/certificate.crt
  subPath: certificate.crt
  readOnly: true
{{-   if and $values.storage.secret $values.storage.extraKey }}
- name: registry-files
  mountPath: /etc/docker/registry/storage
  subPath: storage
  readOnly: true
{{-   end }}
{{-   if include "registry.middleware.privatekeys" .context }}
- name: registry-files
  mountPath: /etc/docker/registry/middleware.storage
  subPath: middleware.storage
  readOnly: true
{{-   end }}
{{-   if $values.profiling.stackdriver.credentials.secret }}
- name: registry-files
  mountPath: /etc/docker/registry/profiling-key.json
  subPath: profiling-key.json
  readOnly: true
{{-   end }}
{{-   if or $values.tls.enabled (and $values.debug.tls.enabled $values.debug.tls.secretName) }}
- name: registry-files
  mountPath: /etc/docker/registry/tls
  subPath: tls
  readOnly: true
{{-   end }}
{{- end }}
{{- if include "registry.database.ssl.enabled" .context }}
- name: registry-files
  mountPath: /etc/docker/registry/ssl
  subPath: ssl
  readOnly: true
{{- end }}
{{- end }}
{{- end -}}

{{/*
Returns "true" when any `middleware.storage` entry mounts a private key.
*/}}
{{- define "registry.middleware.privatekeys" -}}
{{- range .Values.middleware.storage -}}
{{-   if .options.privatekeySecret -}}
true
{{-   end -}}
{{- end -}}
{{- end -}}

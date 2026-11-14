{{/* vim: set filetype=mustache: */}}

{{/*
gitlab.secrets.manifest is the single declarative source of truth for every secret
the chart generates. Both provisioning backends are projections of this list:

  - `shared-secrets.provider: job`        -> templates/shared-secrets/_manifest_shell.tpl
                                            translates each entry into shell for the
                                            pre-install/pre-upgrade hook Job.
  - `shared-secrets.provider: controller` -> templates/shared-secrets/gitlabsecrets.yaml
                                            renders each entry into the `spec.secrets`
                                            of an apps.gitlab.com/v2alpha1 GitLabSecrets.

To add a secret, add one entry here. Do not add generation logic anywhere else.

Entry fields
------------
  name          Secret name. Always resolve it through the existing name helper so the
                generator and the consuming templates cannot drift. A user-supplied
                `secret` value only renames the Secret; the chart still generates it.
                The job backend patches missing `--from-literal` keys and new Rails
                fields into an existing Secret, which is what lets a release pick up
                secret fields added in a later version. It never patches a Secret it
                builds from files (x509, rsa, sshHostKeys, raw bytes).
  comment       Human-readable label, emitted as a shell comment by the job backend.
  generators    One or more generators. Most produce a single key; `x509` and
                `sshHostKeys` produce several and name each one.

Generator types
---------------
  random       key, charset (alphanumeric|hex|lowerAlphanumeric), length,
               encoding (none|base64), wrap (none|jsonArray)
  bytes        key, length, encoding (raw|base64). `raw` also needs `file`, the scratch
               filename the job writes the bytes to before loading them.
  static       key, value
  rsa          key, bits, file
  x509         commonName, bits, days, certKey, keyKey, certFile, keyFile

`file`, `certFile` and `keyFile` name scratch paths inside the job's temporary directory.
They are job-backend detail and are stripped before the manifest reaches the controller.
  sshHostKeys  (no params; key names come from `ssh-keygen -A`)
  railsSecrets key, env, fields[] -- see the railsSecrets branch in _manifest_shell.tpl
  (any)        persistent: true for a value tied to persisted state. Controller-only:
               the Job ignores it. See doc/development/secrets_controller.md.

Every `name` and `key` must reach YAML as a double-quoted string, because
`gitlab.secrets.load` parses this list and a bare `0777`, `1e3` or `null` would become a
number or nothing. Most helpers already end in `| quote`. The ones that return bare output
get `| quote` here instead. The OpenBao helpers cannot quote themselves, because
values.yaml calls them inside single-quoted `tpl` strings. Never add `| quote` to a helper
that already quotes: that stores the quote characters in the name.

Numbers are consumed through `gitlab.secrets.load`, which coerces them with `int`.
Helm decodes YAML numbers as float64 while the GitLab Operator's renderer uses int64,
so without that coercion `length: 4096` would behave differently in each.

NEVER put shell into this manifest. Entries carry descriptors only; the job backend
turns them into `$(gen_random ...)`. A `$(...)` here would make the entry unusable by
the controller backend and break the single source of truth.
*/}}
{{- define "gitlab.secrets.manifest" -}}
- name: {{ include "gitlab.migrations.initialRootPassword.secret" . }}
  comment: Initial root password
  generators:
    - type: random
      key: {{ include "gitlab.migrations.initialRootPassword.key" . }}
      charset: alphanumeric
      length: 64

- name: {{ include "gitlab.gitlab-shell.authToken.secret" . }}
  comment: Gitlab shell
  generators:
    - type: random
      key: {{ include "gitlab.gitlab-shell.authToken.key" . }}
      charset: alphanumeric
      length: 64

- name: {{ include "gitlab.gitaly.authToken.secret" . }}
  comment: Gitaly secret
  generators:
    - type: random
      key: {{ include "gitlab.gitaly.authToken.key" . }}
      charset: alphanumeric
      length: 64

- name: {{ include "gitlab.gitlab-runner.registrationToken.secret" . }}
  comment: Gitlab runner secret
  generators:
    - type: random
      key: runner-registration-token
      charset: alphanumeric
      length: 64
      persistent: true
    - type: static
      key: runner-token
      value: ""

{{- if or (eq .Values.global.pages.enabled true) (not (empty .Values.global.pages.host)) }}
- name: {{ include "gitlab.pages.apiSecret.secret" . }}
  comment: GitLab Pages API secret
  generators:
    - type: random
      key: {{ include "gitlab.pages.apiSecret.key" . }}
      charset: alphanumeric
      length: 32
      encoding: base64
{{- end }}

{{- if and (eq .Values.global.pages.enabled true) (eq .Values.global.pages.accessControl true) }}
- name: {{ include "gitlab.pages.authSecret.secret" . | quote }}
  comment: GitLab Pages auth secret for hashing cookie store when using access control
  generators:
    - type: random
      key: {{ include "gitlab.pages.authSecret.key" . | quote }}
      charset: alphanumeric
      length: 64
      encoding: base64

- name: {{ include "oauth.gitlab-pages.secret" . | quote }}
  comment: GitLab Pages OAuth secret
  generators:
    - type: random
      key: {{ include "oauth.gitlab-pages.appIdKey" . | quote }}
      charset: alphanumeric
      length: 64
      persistent: true
    - type: random
      key: {{ include "oauth.gitlab-pages.appSecretKey" . | quote }}
      charset: alphanumeric
      length: 64
      persistent: true
{{- end }}

{{- if .Values.global.kas.enabled }}
- name: {{ include "gitlab.kas.secret" . }}
  comment: Gitlab-kas secret
  generators:
    - type: random
      key: {{ include "gitlab.kas.key" . }}
      charset: alphanumeric
      length: 32
      encoding: base64

- name: {{ include "gitlab.kas.privateApi.secret" . }}
  comment: Gitlab-kas private API secret
  generators:
    - type: random
      key: {{ include "gitlab.kas.privateApi.key" . }}
      charset: alphanumeric
      length: 32
      encoding: base64

- name: {{ include "gitlab.kas.websocketToken.secret" . }}
  comment: Gitlab-kas WebSocket Token secret
  generators:
    - type: bytes
      key: {{ include "gitlab.kas.websocketToken.key" . }}
      length: 72
      encoding: base64
{{- end }}

{{- if .Values.global.appConfig.incomingEmail.enabled }}
- name: {{ include "gitlab.appConfig.incomingEmail.authToken.secret" . }}
  comment: Gitlab-mailroom incomingEmail webhook secret
  generators:
    - type: random
      key: {{ include "gitlab.appConfig.incomingEmail.authToken.key" . }}
      charset: alphanumeric
      length: 32
      encoding: base64
{{- end }}

{{- if .Values.global.appConfig.serviceDeskEmail.enabled }}
- name: {{ include "gitlab.appConfig.serviceDeskEmail.authToken.secret" . }}
  comment: Gitlab-mailroom serviceDeskEmail webhook secret
  generators:
    - type: random
      key: {{ include "gitlab.appConfig.serviceDeskEmail.authToken.key" . }}
      charset: alphanumeric
      length: 32
      encoding: base64
{{- end }}

- name: {{ include "gitlab.registry.certificate.secret" . }}
  comment: Registry certificates
  generators:
    - type: x509
      commonName: {{ coalesce .Values.registry.tokenIssuer (dig "registry" "tokenIssuer" "gitlab-issuer" .Values.global) | quote }}
      bits: 4096
      days: 3650
      certKey: registry-auth.crt
      keyKey: registry-auth.key
      # Scratch filenames inside the Job's mktemp directory. They only need to be
      # stable within one run; the key names above are what consumers actually read.
      certFile: certs/registry-example-com.crt
      keyFile: certs/registry-example-com.key

- name: {{ include "gitlab.rails-secrets.secret" . }}
  comment: config/secrets.yaml
  generators:
    - type: railsSecrets
      key: secrets.yml
      env: {{ index .Values "shared-secrets" "env" | quote }}
      fields:
        - path: secret_key_base
          shape: scalar
          charset: hex
          length: 128
          note: equivalent to secureRandom.hex(64)
        - path: otp_key_base
          shape: scalar
          charset: hex
          length: 128
          note: equivalent to secureRandom.hex(64)
        - path: db_key_base
          shape: scalar
          charset: hex
          length: 128
          note: equivalent to secureRandom.hex(64)
        - path: encrypted_settings_key_base
          shape: scalar
          charset: hex
          length: 128
          note: equivalent to secureRandom.hex(64)
        - path: openid_connect_signing_key
          shape: pem
          bits: 2048
        # The two `_key` fields below are YAML lists so that keys can be rotated: the
        # last key encrypts, every key decrypts, in order. Adding a key to the end and
        # running a background re-encryption is the supported rotation path. See
        # https://gitlab.com/gitlab-org/gitlab/-/issues/494976
        - path: active_record_encryption_primary_key
          shape: list
          charset: alphanumeric
          length: 32
        - path: active_record_encryption_deterministic_key
          shape: list
          charset: alphanumeric
          length: 32
        - path: active_record_encryption_key_derivation_salt
          shape: scalar
          charset: alphanumeric
          length: 32

- name: {{ include "gitlab.gitlab-shell.hostKeys.secret" . }}
  comment: Shell ssh host keys
  generators:
    - type: sshHostKeys

- name: {{ include "gitlab.workhorse.secret" . }}
  comment: Gitlab-workhorse secret
  generators:
    - type: random
      key: {{ include "gitlab.workhorse.key" . }}
      charset: alphanumeric
      length: 32
      encoding: base64

- name: {{ include "gitlab.registry.httpSecret.secret" . }}
  comment: Registry http.secret secret
  generators:
    - type: random
      key: {{ include "gitlab.registry.httpSecret.key" . }}
      charset: lowerAlphanumeric
      length: 128
      encoding: base64

- name: {{ include "gitlab.registry.notificationSecret.secret" . }}
  comment: Container Registry notification_secret
  generators:
    - type: random
      key: {{ include "gitlab.registry.notificationSecret.key" . }}
      charset: alphanumeric
      length: 32
      wrap: jsonArray

{{- if .Values.global.praefect.enabled }}
{{-   if not .Values.global.praefect.psql.host }}
- name: {{ include "gitlab.praefect.dbSecret.secret" . }}
  comment: Praefect DB password
  generators:
    - type: random
      key: {{ include "gitlab.praefect.dbSecret.key" . }}
      charset: alphanumeric
      length: 32
      persistent: true
{{-   end }}

- name: {{ include "gitlab.praefect.authToken.secret" . }}
  comment: Praefect auth token
  generators:
    - type: random
      key: {{ include "gitlab.praefect.authToken.key" . }}
      charset: alphanumeric
      length: 64
{{- end }}

{{- if and .Values.openbao.install (dig "config" "unseal" "static" "enabled" false .Values.openbao) }}
- name: {{ include "gitlab.openbao.unseal.secret" . }}
  comment: OpenBao static unseal key
  generators:
    - type: bytes
      key: {{ include "gitlab.openbao.unseal.key" . | quote }}
      length: 32
      encoding: raw
      file: bao-unseal
      persistent: true
{{- end }}

{{- if or .Values.openbao.install .Values.global.openbao.enabled }}
- name: {{ include "gitlab.openbao.authenticationTokenSecretFilePath.secret" . | quote }}
  comment: Authentication token secret for Openbao Rails requests
  generators:
    - type: random
      key: {{ include "gitlab.openbao.authenticationTokenSecretFilePath.key" . | quote }}
      charset: alphanumeric
      length: 32
{{- end }}

{{- if .Values.global.appConfig.iamAuthService.enabled }}
- name: {{ include "gitlab.appConfig.iamAuthService.authToken.secret" . }}
  comment: Service token used by GitLab to authenticate internal API requests to the iam-auth service
  generators:
    - type: random
      key: {{ include "gitlab.appConfig.iamAuthService.authToken.key" . }}
      charset: alphanumeric
      length: 64
{{- end }}

{{- if .Values.global.appConfig.iamDataAccessService.enabled }}
- name: {{ include "gitlab.appConfig.iamDataAccessService.authToken.secret" . }}
  comment: Service token used by GitLab to authenticate internal API requests to the iam-data-access service
  generators:
    - type: random
      key: {{ include "gitlab.appConfig.iamDataAccessService.authToken.key" . }}
      charset: alphanumeric
      length: 64
{{- end }}

{{- if index .Values "ai-gateway" "install" }}
- name: {{ include "ai-gateway.duoWorkflowSigningKey.secret" . }}
  comment: Duo workflow signing key
  generators:
    - type: rsa
      key: {{ include "ai-gateway.duoWorkflowSigningKey.key" . }}
      bits: 4096
      file: duo_workflow_signing.key

- name: {{ include "ai-gateway.duoWorkflowValidationKey.secret" . }}
  comment: Duo workflow validation key
  generators:
    - type: rsa
      key: {{ include "ai-gateway.duoWorkflowValidationKey.key" . }}
      bits: 4096
      file: duo_workflow_validation.key

- name: {{ include "ai-gateway.aigwSigningKey.secret" . }}
  comment: AI Gateway signing key
  generators:
    - type: rsa
      key: {{ include "ai-gateway.aigwSigningKey.key" . }}
      bits: 4096
      file: aigw_signing.key

- name: {{ include "ai-gateway.aigwValidationKey.secret" . }}
  comment: AI Gateway validation key
  generators:
    - type: rsa
      key: {{ include "ai-gateway.aigwValidationKey.key" . }}
      bits: 4096
      file: aigw_validation.key
{{- end }}
{{- end -}}

{{/*
gitlab.secrets.load parses gitlab.secrets.manifest and returns a normalized list.

It exists to close two traps in the YAML round trip:

  1. `fromYamlArray` reports a parse failure by returning a single-element list holding
     the error string, which would otherwise silently reduce the secret set to nothing.
     The kindIs check below turns that into a hard template failure.
  2. Helm decodes YAML numbers as float64. `int` coercion here means a single code path
     for both Helm and the Operator's int64 renderer.

Usage:
  {{- range $secret := include "gitlab.secrets.load" . | fromYamlArray }}
*/}}
{{- define "gitlab.secrets.load" -}}
{{- $entries := include "gitlab.secrets.manifest" . | fromYamlArray -}}
{{- $out := list -}}
{{- range $entry := $entries -}}
{{-   if not (kindIs "map" $entry) -}}
{{-     fail (printf "shared-secrets: manifest failed to parse. fromYamlArray returned: %v" $entry) -}}
{{-   end -}}
{{-   if not $entry.name -}}
{{-     fail (printf "shared-secrets: manifest entry is missing 'name': %v" $entry) -}}
{{-   end -}}
{{-   if not $entry.generators -}}
{{-     fail (printf "shared-secrets: manifest entry %q has no generators" $entry.name) -}}
{{-   end -}}
{{-   range $generator := $entry.generators -}}
{{/*    A `random` or `bytes` generator without a positive length would render
        `gen_random '<charset>' 0`, silently producing an empty secret. */}}
{{-     if has $generator.type (list "random" "bytes") -}}
{{-       if not (gt (int $generator.length) 0) -}}
{{-         fail (printf "shared-secrets: %q generator %q in entry %q needs a positive length" $generator.type (default "" $generator.key) $entry.name) -}}
{{-       end -}}
{{-     end -}}
{{/*    charset must be spelled out. Defaulting it in the shell would leave the field
        absent from the GitLabSecrets resource, so the controller would apply a default
        of its own and the two backends could disagree on the alphabet. */}}
{{-     if eq $generator.type "random" -}}
{{-       if not $generator.charset -}}
{{-         fail (printf "shared-secrets: random generator %q in entry %q must set a charset" (default "" $generator.key) $entry.name) -}}
{{-       end -}}
{{-     end -}}
{{-     if eq $generator.type "railsSecrets" -}}
{{-       range $field := $generator.fields -}}
{{-         if and (ne $field.shape "pem") (not $field.charset) -}}
{{-           fail (printf "shared-secrets: rails field %q in entry %q must set a charset" $field.path $entry.name) -}}
{{-         end -}}
{{-       end -}}
{{-     end -}}
{{/*    The shell backend dispatches on the first generator, so railsSecrets has to
        stand alone rather than be mixed with others. */}}
{{-     if and (eq $generator.type "railsSecrets") (gt (len $entry.generators) 1) -}}
{{-       fail (printf "shared-secrets: entry %q must declare railsSecrets as its only generator" $entry.name) -}}
{{-     end -}}
{{-   end -}}
{{-   $generators := list -}}
{{-   range $generator := $entry.generators -}}
{{-     $normalized := omit $generator "length" "bits" "days" "fields" -}}
{{-     if hasKey $generator "length" -}}
{{-       $_ := set $normalized "length" (int $generator.length) -}}
{{-     end -}}
{{-     if hasKey $generator "bits" -}}
{{-       $_ := set $normalized "bits" (int $generator.bits) -}}
{{-     end -}}
{{-     if hasKey $generator "days" -}}
{{-       $_ := set $normalized "days" (int $generator.days) -}}
{{-     end -}}
{{-     if hasKey $generator "fields" -}}
{{-       $fields := list -}}
{{-       range $field := $generator.fields -}}
{{-         $normalizedField := omit $field "length" "bits" -}}
{{-         if hasKey $field "length" -}}
{{-           $_ := set $normalizedField "length" (int $field.length) -}}
{{-         end -}}
{{-         if hasKey $field "bits" -}}
{{-           $_ := set $normalizedField "bits" (int $field.bits) -}}
{{-         end -}}
{{-         $fields = append $fields $normalizedField -}}
{{-       end -}}
{{-       $_ := set $normalized "fields" $fields -}}
{{-     end -}}
{{-     $generators = append $generators $normalized -}}
{{-   end -}}
{{-   $out = append $out (merge (dict "generators" $generators) (omit $entry "generators")) -}}
{{- end -}}
{{- toYaml $out -}}
{{- end -}}

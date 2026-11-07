{{/* vim: set filetype=mustache: */}}

{{/*
Translates gitlab.secrets.manifest entries into shell for the shared-secrets Job.

This is the only place that knows how a declarative generator becomes a command. The
manifest itself carries no shell, so that the controller backend can consume the same
entries. See templates/shared-secrets/_manifest.tpl.

Two templates per entry:
  gitlab.secrets.shell.prepare  commands that must run before the secret is created
                                (openssl, ssh-keygen, writing random bytes to a file)
  gitlab.secrets.shell.args     the kubectl argument list

Recipes reproduce what the hand-written script did. `encoding: base64` is standard base64
with no line wrapping and no trailing newline, the same as `bytes` with `encoding: base64`.
*/}}

{{/*
Args: charset name. Returns the tr(1) character class.

There is deliberately no default. `hex` and `alphanumeric` draw from different alphabets,
so guessing one would silently change what a secret looks like. It also has to stay
explicit because the controller backend reads the same field: a value the chart defaults
in shell would reach the controller as absent, leaving it to apply a default of its own.
*/}}
{{- define "gitlab.secrets.shell.charset" -}}
{{-   if eq . "alphanumeric" -}}a-zA-Z0-9
{{-   else if eq . "hex" -}}a-f0-9
{{-   else if eq . "lowerAlphanumeric" -}}a-z0-9
{{-   else -}}{{ fail (printf "shared-secrets: unknown charset %v, expected alphanumeric, hex or lowerAlphanumeric" .) }}
{{-   end -}}
{{- end -}}

{{/* Args: a generator map. Returns the command substitution producing its value. */}}
{{- define "gitlab.secrets.shell.value" -}}
{{-   $charset := include "gitlab.secrets.shell.charset" .charset -}}
{{-   $raw := printf "gen_random '%s' %d" $charset (int .length) -}}
{{-   $encoding := default "none" .encoding -}}
{{-   if eq $encoding "base64" -}}
{{-     $raw = printf "%s | base64 -w 0" $raw -}}
{{-   else if ne $encoding "none" -}}
{{-     fail (printf "shared-secrets: unknown encoding %q" $encoding) -}}
{{-   end -}}
{{-   $wrap := default "none" .wrap -}}
{{-   if eq $wrap "jsonArray" -}}
[\"$({{ $raw }})\"]
{{-   else if eq $wrap "none" -}}
$({{ $raw }})
{{-   else -}}
{{-     fail (printf "shared-secrets: unknown wrap %q" $wrap) -}}
{{-   end -}}
{{- end -}}

{{/* Args: a manifest entry. Emits the commands that must run before creation. */}}
{{- define "gitlab.secrets.shell.prepare" -}}
{{- range $generator := .generators -}}
{{-   if eq $generator.type "x509" }}
mkdir -p certs
openssl req -new -newkey rsa:{{ int $generator.bits }} -subj "/CN={{ $generator.commonName }}" -nodes -x509 -keyout {{ $generator.keyFile }} -out {{ $generator.certFile }} -days {{ int $generator.days }}
{{-   else if eq $generator.type "sshHostKeys" }}
ssh-keygen -A
mkdir -p host_keys
cp /etc/ssh/ssh_host_* host_keys/
{{-   else if eq $generator.type "rsa" }}
openssl genrsa -out {{ $generator.file }} {{ int $generator.bits }}
{{-   else if and (eq $generator.type "bytes") (eq (default "base64" $generator.encoding) "raw") }}
gen_random_bytes {{ int $generator.length }} > {{ $generator.file }}
{{-   end -}}
{{- end -}}
{{- end -}}

{{/* Args: a manifest entry. Returns the kubectl argument list, space-prefixed. */}}
{{- define "gitlab.secrets.shell.args" -}}
{{- range $generator := .generators -}}
{{-   if eq $generator.type "random" }} --from-literal={{ $generator.key }}={{ include "gitlab.secrets.shell.value" $generator }}
{{-   else if eq $generator.type "static" }} --from-literal={{ $generator.key }}={{ $generator.value | quote }}
{{-   else if eq $generator.type "bytes" }}
{{-     if eq (default "base64" $generator.encoding) "raw" }} --from-file={{ $generator.key }}={{ $generator.file }}
{{-     else }} --from-literal={{ $generator.key }}=$(gen_random_base64 {{ int $generator.length }})
{{-     end -}}
{{-   else if eq $generator.type "rsa" }} --from-file={{ $generator.key }}={{ $generator.file }}
{{-   else if eq $generator.type "x509" }} --from-file={{ $generator.keyKey }}={{ $generator.keyFile }} --from-file={{ $generator.certKey }}={{ $generator.certFile }}
{{-   else if eq $generator.type "sshHostKeys" }} --from-file host_keys
{{-   else }}{{ fail (printf "shared-secrets: generator type %q has no shell translation" $generator.type) }}
{{-   end -}}
{{- end -}}
{{- end -}}

{{/*
The Rails secret is the one entry that cannot use generate_secret_if_needed.

generate_secret_if_needed creates every other secret once. Afterwards it only relabels it
and patches in missing `--from-literal` keys; it never patches `--from-file` keys. This one
has to survive partial content: fields are added to config/secrets.yml over releases, so
the script reads whatever exists, generates only what is missing, and applies the result.
Losing db_key_base here would make every encrypted column in the database unreadable.

The field list comes from the manifest; the merge algorithm stays here. It keeps every
declared field that has a value, generates declared fields that are missing, null or
empty, and keeps lists as they are. It writes the document back from the field list, so
it drops undeclared fields and the blocks of every environment other than $env.

Args: a manifest entry whose sole generator is of type railsSecrets.
*/}}
{{- define "gitlab.secrets.shell.railsSecrets" -}}
{{- $generator := first .generators -}}
{{- $fields := $generator.fields -}}
if [ -n "$env" ]; then
  rails_secret={{ .name | quote }}

  # Fetch the values from the existing secret if it exists
  if $(kubectl --namespace=$namespace get secret $rails_secret > /dev/null 2>&1); then
    kubectl --namespace=$namespace get secret $rails_secret -o jsonpath="{.data.{{ $generator.key | replace "." "\\." }}}" | base64 --decode > secrets.yml
{{- range $field := $fields }}
    {{ $field.path }}=$(fetch_rails_value secrets.yml "${env}.{{ $field.path }}")
{{- end }}
  fi;

  # Generate defaults for any unset secrets
{{- range $field := $fields }}
{{-   if eq $field.shape "pem" }}
  {{ $field.path }}="${{"{"}}{{ $field.path }}:-$(openssl genrsa {{ int $field.bits }})}"
{{-   else if eq $field.shape "list" }}
  {{ $field.path }}=${{"{"}}{{ $field.path }}:-"- $(gen_random '{{ include "gitlab.secrets.shell.charset" $field.charset }}' {{ int $field.length }})"}
{{-   else }}
  {{ $field.path }}="${{"{"}}{{ $field.path }}:-$(gen_random '{{ include "gitlab.secrets.shell.charset" $field.charset }}' {{ int $field.length }})}"{{ if $field.note }} # {{ $field.note }}{{ end }}
{{-   end -}}
{{- end }}

  # Update the existing secret
  cat << EOF > rails-secrets.yml
apiVersion: v1
kind: Secret
metadata:
  name: $rails_secret
type: Opaque
stringData:
  {{ $generator.key }}: |-
    $env:
{{- range $field := $fields }}
{{-   if eq $field.shape "pem" }}
      {{ $field.path }}: |
$(echo "${{"{"}}{{ $field.path }}}" | awk '{print "        " $0}')
{{-   else if eq $field.shape "list" }}
{{- /* Indent every line, not just the first. A rotated key list arrives from
       fetch_rails_value as multi-line text, and a literal prefix here would leave the
       second and later entries at column 0, producing YAML that kubectl rejects.
       Same treatment as the pem branch above. */}}
      {{ $field.path }}:
$(echo "${{"{"}}{{ $field.path }}}" | awk '{print "        " $0}')
{{-   else }}
      {{ $field.path }}: ${{ $field.path }}
{{-   end -}}
{{- end }}
EOF
  kubectl --validate=false --namespace=$namespace apply -f rails-secrets.yml
  label_secret $rails_secret
fi
{{- end -}}

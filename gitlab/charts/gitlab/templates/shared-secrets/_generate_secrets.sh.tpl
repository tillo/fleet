# vim: set filetype=sh:

# Fail fast on errors.
set -e
set -o pipefail

namespace={{ .Release.Namespace }}
release={{ .Release.Name }}
env={{ index .Values "shared-secrets" "env" }}

pushd $(mktemp -d)

# Args pattern, length
function gen_random(){
  head -c 4096 /dev/urandom | LC_CTYPE=C tr -cd $1 | head -c $2
}

# Args: length
function gen_random_base64(){
  local len="$1"
  gen_random_bytes "$len" | base64 -w0
}

# Args: length
function gen_random_bytes(){
  local len="$1"
  head -c "$len" /dev/urandom
}

# Args: yaml file, search path
function fetch_rails_value(){
  local value=$(yq --prettyPrint --no-colors ".${2}" $1)

  # Don't return null values
  if [ "${value}" != "null" ]; then echo "${value}"; fi
}

# Args: secretname
function label_secret(){
  local secret_name=$1
{{ if not .Values.global.application.create -}}
  # Remove application labels if they exist
  kubectl --namespace=$namespace label \
    secret $secret_name $(echo '{{ include "gitlab.application.labels" . | replace ": " "=" | replace "\r\n" " " | replace "\n" " " }}' | sed -E 's/=[^ ]*/-/g')
{{ end }}
  kubectl --namespace=$namespace label \
    --overwrite \
    secret $secret_name {{ include "gitlab.standardLabels" . | replace ": " "=" | replace "\r\n" " " | replace "\n" " " }} {{ include "gitlab.commonLabels" . | replace ": " "=" | replace "\r\n" " " | replace "\n" " " }}
}

# Args: secretname, args
function generate_secret_if_needed(){
  local secret_args=( "${@:2}")
  local secret_name=$1

  if ! $(kubectl --namespace=$namespace get secret $secret_name > /dev/null 2>&1); then
    kubectl --namespace=$namespace create secret generic $secret_name ${secret_args[@]}
  else
    echo "secret \"$secret_name\" already exists."

    for arg in "${secret_args[@]}"; do
      local from=$(echo -n ${arg} | cut -d '=' -f1)

      if [ -z "${from##*literal*}" ]; then
        local key=$(echo -n ${arg} | cut -d '=' -f2)
        local desiredValue=$(echo -n ${arg} | cut -d '=' -f3-)
        local flags="--namespace=$namespace --allow-missing-template-keys=false"

        if ! $(kubectl $flags get secret $secret_name -ojsonpath="{.data.${key}}" > /dev/null 2>&1); then
          echo "key \"${key}\" does not exist. patching it in."

          if [ "${desiredValue}" != "" ]; then
            desiredValue=$(echo -n "${desiredValue}" | base64 -w 0)
          fi

          kubectl --namespace=$namespace patch secret ${secret_name} -p "{\"data\":{\"$key\":\"${desiredValue}\"}}"
        fi
      fi
    done
  fi

  label_secret $secret_name
}


{{/*
Every secret below comes from gitlab.secrets.manifest, translated by
gitlab.secrets.shell.*. To add a secret, edit the manifest, not this file.
*/}}
{{- range $secret := include "gitlab.secrets.load" . | fromYamlArray }}
{{-   if eq (first $secret.generators).type "railsSecrets" }}

# {{ $secret.comment }}
{{ include "gitlab.secrets.shell.railsSecrets" $secret }}
{{-   else }}

# {{ $secret.comment }}
{{-     include "gitlab.secrets.shell.prepare" $secret }}
generate_secret_if_needed {{ $secret.name | quote }}{{ include "gitlab.secrets.shell.args" $secret }}
{{-   end }}
{{- end }}

{{/*
Ensure a database is configured when OpenBao is installed.

The database must be configured explicitly via global.openbao.psql or
openbao.config.storage.postgresql.connection with host, database, username, and password.
Password is required and is not inherited from the main DB.

NOTE: We intentionally check global.openbao.psql and openbao.config.storage.postgresql.connection
directly rather than the merged openbao.postgresql.configuration template. The merged template
falls back to global.psql (the main Rails DB), which would cause this check to silently pass
even when no dedicated OpenBao database is configured.
*/}}
{{- define "gitlab.checkConfig.openbao.database" -}}
{{- if .Values.openbao.install -}}
{{-   $openbaoConfig := coalesce ((.Values.openbao).config) .Values.config | default dict -}}
{{-   $conn := ((($openbaoConfig.storage).postgresql).connection | default dict) -}}
{{-   $globalObaPsql := ((.Values.global).openbao).psql | default dict -}}
{{-   $host := coalesce $conn.host $globalObaPsql.host "" -}}
{{-   if not $host }}
openbao: no database configured
    It appears OpenBao is enabled but no database was configured. Configure `global.openbao.psql` with host, database, username, and password. See https://docs.gitlab.com/charts/charts/openbao/#database-configuration
{{-   end }}
{{-   $password := coalesce $conn.password $globalObaPsql.password (dict) -}}
{{-   if not (hasKey $password "secret") }}
openbao: no database password configured
    OpenBao requires an explicit database password. Configure `global.openbao.psql.password` with secret/key. Password is not inherited from the main DB. See https://docs.gitlab.com/charts/charts/openbao/#database-configuration
{{-   end -}}
{{- end -}}
{{- end -}}
{{/* END gitlab.checkConfig.openbao.database */}}

{{/*
Ensure the unseal secret fields are named whenever their mounts are rendered.

Both mounts take their subPath from global.openbao.unseal.*Field. An empty value renders an empty
subPath, which the API server rejects, and an empty currentKeyField also makes the shared-secrets
job generate `--from-file==bao-unseal`. Neither surfaces a usable error.

previousKeyField without previousKeyId renders cleanly - the previous mount is gated on the ID - but
it means a rotation was only half configured. Once currentKey* names the new key that leaves OpenBao
holding the new key alone, with the root key still encrypted with the old one, and it cannot unseal.

Equal IDs are rejected because a rotation needs a fresh ID for the new key. OpenBao itself accepts
them when the key material matches too, so that check is deliberately stricter than OpenBao. Equal
fields are different: they mount one key under two IDs, which OpenBao rejects outright when it parses
its seal config. The field check stays quiet while the IDs are equal, so its message never claims two
IDs when there is only one.

Gated on static.enabled to match the mounts themselves: with static unsealing off nothing reads
these values, so leftover rotation settings must not block an AWS KMS render.
*/}}
{{- define "gitlab.checkConfig.openbao.unseal" -}}
{{- if .Values.openbao.install -}}
{{-   $openbaoConfig := coalesce ((.Values.openbao).config) .Values.config | default dict -}}
{{-   $static := (($openbaoConfig.unseal).static | default dict) -}}
{{-   $unseal := (((.Values.global).openbao).unseal | default dict) -}}
{{-   if $static.enabled -}}
{{-     if not $unseal.currentKeyField }}
openbao: no current unseal key field configured
    `global.openbao.unseal.currentKeyField` is empty. Set it to the unseal secret field holding the current key. See https://docs.gitlab.com/charts/charts/openbao/#rotate-the-static-unseal-key
{{-     end -}}
{{-     if and $static.previousKeyId (not $unseal.previousKeyField) }}
openbao: no previous unseal key field configured
    `config.unseal.static.previousKeyId` is set but `global.openbao.unseal.previousKeyField` is empty. Set it to the unseal secret field holding the previous key. See https://docs.gitlab.com/charts/charts/openbao/#rotate-the-static-unseal-key
{{-     end -}}
{{-     if and $unseal.previousKeyField (not $static.previousKeyId) }}
openbao: no previous unseal key ID configured
    `global.openbao.unseal.previousKeyField` is set but `config.unseal.static.previousKeyId` is empty, so the previous key is neither mounted nor configured. Set the previous key ID and path, or remove `previousKeyField` if the rotation is complete. See https://docs.gitlab.com/charts/charts/openbao/#rotate-the-static-unseal-key
{{-     end -}}
{{-     if and $static.previousKeyId (eq $static.previousKeyId ($static.currentKeyId | default "")) }}
openbao: unseal key IDs are the same
    `config.unseal.static.previousKeyId` and `config.unseal.static.currentKeyId` are both `{{ $static.previousKeyId }}`. OpenBao tells the two keys apart by their IDs, so give the new key an ID that has never been used. See https://docs.gitlab.com/charts/charts/openbao/#rotate-the-static-unseal-key
{{-     end -}}
{{-     if and $static.previousKeyId $static.currentKeyId $unseal.previousKeyField (eq $unseal.previousKeyField ($unseal.currentKeyField | default "")) (ne $static.previousKeyId $static.currentKeyId) }}
openbao: unseal key fields are the same
    `global.openbao.unseal.previousKeyField` and `global.openbao.unseal.currentKeyField` are both `{{ $unseal.previousKeyField }}`, so both key IDs mount the same key. OpenBao refuses to start with one key under two IDs. Set `currentKeyField` to the field holding the new key. See https://docs.gitlab.com/charts/charts/openbao/#rotate-the-static-unseal-key
{{-     end -}}
{{-   end -}}
{{- end -}}
{{- end -}}
{{/* END gitlab.checkConfig.openbao.unseal */}}

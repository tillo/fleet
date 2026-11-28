{{/*
gitlab.gitaly.tomlFromValues encodes a value map as TOML.

It is NOT encoded with Helm's toToml, because Helm represents every number from
.Values (and anything via fromYaml) as a float64, and toToml renders an integral
float64 with a trailing ".0" (e.g. 100 -> "100.0").

Instead the map is JSON-encoded by Helm and converted to TOML by gomplate at
pod startup time

Usage:
  {{ include "gitlab.gitaly.tomlFromValues" $config }}
*/}}
{{- define "gitlab.gitaly.tomlFromValues" -}}
{% (`{{ . | toJson }}` | data.JSON | data.ToTOML) %}
{{- end -}}


{{- define "gitlab.gitaly.storageNames" -}}
{{- range (coalesce $.Values.internal.names $.Values.global.gitaly.internal.names) }} {{ . | quote }} {{- end }}
{{- end -}}

{{- define "gitlab.praefect.gitaly.storageNames" -}}
{{- range $_, $storage := $.Values.global.praefect.virtualStorages -}}
{{ range until ($storage.gitalyReplicas | int) }} {{ printf "%s-gitaly-%s-%d" $.Release.Name $storage.name . | quote }} {{- end }}
{{- end -}}
{{- end -}}

{{- /*
gitlab.gitaly.configuration returns the full config.toml content as YAML. It
first handles the current keys under .Values by converting them to snake case.
It then merges in any keys found in the new .Values.configuration block, which
is the new method of configuring Gitaly.
*/ -}}
{{- define "gitlab.gitaly.configuration" -}}
{{- $cfg := dict -}}

{{- $_ := set $cfg "bin_dir" "/usr/local/bin" -}}
{{- $_ := set $cfg "listen_addr" (printf ":%v" (coalesce .Values.service.internalPort .Values.global.gitaly.service.internalPort)) -}}
{{- $_ := set $cfg "graceful_restart_timeout" (.Values.gracefulRestartTimeout | toString | duration) -}}

{{- $_ := set $cfg "gitlab-shell" (dict "dir" "/srv/gitlab-shell") -}}
{{- $_ := set $cfg "gitlab" (dict "secret_file" "/etc/gitlab-secrets/shell/.gitlab_shell_secret" "url" (printf "%s/" (include "gitlab.workhorse.url" .))) -}}
{{- $_ := set $cfg "hooks" (dict "custom_hooks_dir" "/home/git/custom_hooks") -}}

{{- $git := dict "use_bundled_binaries" true "ignore_gitconfig" true -}}
{{- if .Values.gpgSigning.enabled -}}{{- $_ := set $git "signing_key" "/etc/gitlab-secrets/gitaly/signing_key.gpg" -}}{{- end -}}
{{- with .Values.git -}}
{{-   if .catFileCacheSize -}}{{- $_ := set $git "catfile_cache_size" .catFileCacheSize -}}{{- end -}}
{{-   if .config -}}
{{-     $gitConfig := list -}}
{{-     range .config -}}{{- $gitConfig = append $gitConfig (dict "key" (toString .key) "value" (toString .value)) -}}{{- end -}}
{{-     $_ := set $git "config" $gitConfig -}}
{{-   end -}}
{{- end -}}
{{- $_ := set $cfg "git" $git -}}

{{- $logging := dict "dir" "/var/log/gitaly" -}}
{{- with .Values.logging -}}
{{-   if .level -}}{{- $_ := set $logging "level" .level -}}{{- end -}}
{{-   if .format -}}{{- $_ := set $logging "format" .format -}}{{- end -}}
{{-   if .sentryDsn -}}{{- $_ := set $logging "sentry_dsn" .sentryDsn -}}{{- end -}}
{{-   if .sentryEnvironment -}}{{- $_ := set $logging "sentry_environment" .sentryEnvironment -}}{{- end -}}
{{- end -}}
{{- $_ := set $cfg "logging" $logging -}}

{{- with .Values.shell.concurrency -}}
{{-   $concurrency := deepCopy . -}}
{{-   include "gitlab.keysToSnakeCase" $concurrency -}}
{{-   $_ := set $cfg "concurrency" $concurrency -}}
{{- end -}}

{{- if .Values.packObjectsCache.enabled -}}
{{-   $poc := dict "enabled" true -}}
{{-   with .Values.packObjectsCache -}}
{{-     if .dir -}}{{- $_ := set $poc "dir" .dir -}}{{- end -}}
{{-     if .max_age -}}{{- $_ := set $poc "max_age" .max_age -}}{{- end -}}
{{-     if .min_occurrences -}}{{- $_ := set $poc "min_occurrences" .min_occurrences -}}{{- end -}}
{{-   end -}}
{{-   $_ := set $cfg "pack_objects_cache" $poc -}}
{{- end -}}

{{- if .Values.backup.goCloudUrl -}}
{{-   $_ := set $cfg "backup" (dict "go_cloud_url" .Values.backup.goCloudUrl) -}}
{{- end -}}

{{- with .Values.timeout -}}
{{-   $timeout := dict -}}
{{-   if .uploadPackNegotiation -}}{{- $_ := set $timeout "upload_pack_negotiation" .uploadPackNegotiation -}}{{- end -}}
{{-   if .uploadArchiveNegotiation -}}{{- $_ := set $timeout "upload_archive_negotiation" .uploadArchiveNegotiation -}}{{- end -}}
{{-   $_ := set $cfg "timeout" $timeout -}}
{{- end -}}

{{- with .Values.dailyMaintenance -}}
{{-   $dm := dict -}}
{{-   if hasKey . "disabled" -}}{{- $_ := set $dm "disabled" (eq .disabled true) -}}{{- end -}}
{{-   if hasKey . "startHour" -}}{{- $_ := set $dm "start_hour" (int .startHour) -}}{{- end -}}
{{-   if hasKey . "startMinute" -}}{{- $_ := set $dm "start_minute" (int .startMinute) -}}{{- end -}}
{{-   if .duration -}}{{- $_ := set $dm "duration" .duration -}}{{- end -}}
{{-   if and .storages (kindIs "slice" .storages) -}}{{- $_ := set $dm "storages" .storages -}}{{- end -}}
{{-   $_ := set $cfg "daily_maintenance" $dm -}}
{{- end -}}

{{- with .Values.bundleUri -}}
{{-   if .goCloudUrl -}}
{{-     $_ := set $cfg "bundle_uri" (dict "go_cloud_url" .goCloudUrl) -}}
{{-   end -}}
{{- end -}}

{{- /*
Any keys supplied in .Values.configuration take precedence, as this will be the
new method going forward.
*/ -}}
{{- $cfg = mergeOverwrite $cfg (deepCopy (.Values.configuration | default dict)) -}}

{{- $cfg | toYaml -}}
{{- end -}}

{{- /*
gitlab.gitaly.configuration.static invokes gitlab.gitaly.configuration and
returns the subset that can be encoded at Chart-render time. It removes some
keys that must be hand-written in the ConfigMap:

- storage: this cannot be customised by the user and is derived from the pod's
  hostname
- auth: this is read from the mounted secret
- cgroups: this is read from the pod's cgroup path
- prometheus: the grpc_latency_buckets value is a pre-formatted string
- tls_listen_addr, tls, prometheus_listen_addr: the TLS and metrics listen
  settings, which must stay in sync with the Service ports and mounted
  certificates the chart wires up
*/ -}}
{{- define "gitlab.gitaly.configuration.static" -}}
{{- $cfg := include "gitlab.gitaly.configuration" . | fromYaml -}}
{{- $_ := unset $cfg "storage" -}}
{{- $_ := unset $cfg "auth" -}}
{{- $_ := unset $cfg "cgroups" -}}
{{- $_ := unset $cfg "prometheus" -}}
{{- $_ := unset $cfg "tls_listen_addr" -}}
{{- $_ := unset $cfg "tls" -}}
{{- $_ := unset $cfg "prometheus_listen_addr" -}}
{{- $cfg | toYaml -}}
{{- end -}}


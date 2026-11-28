{{/*
Return the string 'PROXY'

The string 'PROXY' ensures the use of ProxyProtocol decoding in a TCP service.
This string is exactly compared with the string 'PROXY' when using nginx-ingress (in capital letters).
See: https://kubernetes.github.io/ingress-nginx/user-guide/exposing-tcp-udp-services/
*/}}
{{- define "gitlab.shell.tcp.proxyProtocol" -}}
{{- $inbound := "" -}}
{{- if .Values.global.shell.tcp.proxyProtocol -}}
{{-   $inbound = "PROXY" -}}
{{- end -}}
{{- $outbound := "" -}}
{{- if eq "true" (include "gitlab.shell.proxyProtocol.outbound" .) -}}
{{-   $outbound = "PROXY" -}}
{{- end -}}
:{{ $inbound }}:{{ $outbound }}
{{- end -}}

{{/*
Returns "true" if GitLab Shell can accept PROXY headers on the outbound leg
(the proxy-to-Shell connection). Used by the NGINX/HAProxy TCP configs and the
Envoy BackendTrafficPolicy. Independent of global.shell.tcp.proxyProtocol, which
controls the inbound leg.
*/}}
{{- define "gitlab.shell.proxyProtocol.outbound" -}}
{{- $proxySupported := not (eq .Values.config.proxyPolicy "reject") -}}
{{- if or .Values.config.proxyProtocol (and $proxySupported (eq .Values.sshDaemon "gitlab-sshd")) -}}
true
{{- else -}}
false
{{- end -}}
{{- end -}}

{{/*
Returns "true" when opensshd.supplemental_config sets HostKey directives. sshd
adds up HostKey directives, so the chart renders its own only without these, as
sshd used its defaults only without these before.
*/}}
{{- define "gitlab.shell.sshd.userHostKeys" -}}
{{- $config := dig "supplemental_config" "" (default (dict) .Values.opensshd) -}}
{{- if regexMatch "(?mi)^\\s*HostKey\\s" $config -}}
true
{{- end -}}
{{- end -}}

{{/*
Returns opensshd.supplemental_config, with HostKey directives for the keys of the
host keys Secret pointed at its mount. sshd rejects the copies of the keys in
/etc/ssh, and the former path /etc/gitlab-secrets/ssh no longer exists. For example:
  HostKey /etc/ssh/ssh_host_ed25519_key -> HostKey /etc/gitlab-ssh-host-keys/ssh_host_ed25519_key
Other directives, and keys with other names, stay unchanged.
*/}}
{{- define "gitlab.shell.sshd.supplementalConfig" -}}
{{- $config := dig "supplemental_config" "" (default (dict) .Values.opensshd) -}}
{{- $hostKey := "(?mi)^(\\s*HostKey\\s+)/etc/(?:ssh|gitlab-secrets/ssh)/(ssh_host_[^\\s/]*_key)\\s*$" -}}
{{- regexReplaceAll $hostKey $config "${1}/etc/gitlab-ssh-host-keys/${2}" -}}
{{- end -}}

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

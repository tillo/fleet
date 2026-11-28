{{/*
Returns name of the Gateway class. Consumed by chart managed Gateway and GatewayClass.
*/}}
{{- define "gitlab.gatewayApi.class.name" -}}
{{- .Values.gatewayApiResources.class.name -}}
{{- end -}}

{{/*
Returns the name of the EnvoyProxy resource.
*/}}
{{- define "gitlab.gatewayApi.envoy.config.name" -}}
{{- printf "%s-envoy-proxy" .Release.Name -}}
{{- end -}}

{{- define "gitlab.gatewayApi.gateway.name.default" -}}
{{- printf "%s-gw" .Release.Name -}}
{{- end -}}

{{/*
Returns a target ref to the Gateway resource without namespace and sectionName
for usage in Envoy policy custom resources.
*/}}
{{- define "gitlab.gatewayApi.gatewayRef.local" -}}
- group: gateway.networking.k8s.io
  kind: Gateway
  name: {{ coalesce (.Values.gatewayRoute).gatewayName .Values.global.gatewayApi.gatewayRef.name (include "gitlab.gatewayApi.gateway.name.default" .) | quote }}
{{- end -}}

{{/*
Checks if Envoy Gateway's Gateway API resources (GatewayClass, EnvoyProxy,
ClientTrafficPolicy, BackendTrafficPolicy, SecurityPolicy) should be configured. Mirrors
global.gatewayApi.installEnvoy, but global.gatewayApi.configureEnvoy takes precedence when
set explicitly. This allows the chart to manage the GatewayClass, EnvoyProxy, and policy
resources for an externally managed Envoy Gateway, without installing the bundled Envoy
Gateway subchart itself.
*/}}
{{- define "gitlab.gatewayApi.configureEnvoy" -}}
{{- if not (eq nil .Values.global.gatewayApi.configureEnvoy) -}}
{{-   .Values.global.gatewayApi.configureEnvoy -}}
{{- else -}}
{{-   .Values.global.gatewayApi.installEnvoy -}}
{{- end -}}
{{- end -}}

{{/*
Checks if the chart-managed GatewayClass should be rendered. Mirrors
gitlab.gatewayApi.configureEnvoy, but gatewayApiResources.class.enabled takes precedence
when set explicitly. Use this to keep the policy resources managed by the chart while
providing your own GatewayClass. The EnvoyProxy resource is only rendered when the
GatewayClass is also chart-managed; see gitlab.gatewayApi.envoy.proxy.enabled.
*/}}
{{- define "gitlab.gatewayApi.class.enabled" -}}
{{- if not (eq nil .Values.gatewayApiResources.class.enabled) -}}
{{-   .Values.gatewayApiResources.class.enabled -}}
{{- else -}}
{{-   include "gitlab.gatewayApi.configureEnvoy" . -}}
{{- end -}}
{{- end -}}

{{/*
Checks if the chart-managed EnvoyProxy resource should be rendered. The EnvoyProxy is only
useful when the chart also manages the GatewayClass referencing it through parametersRef,
so this is true when both Envoy Gateway's Gateway API resources are configured
(gitlab.gatewayApi.configureEnvoy) and the GatewayClass is chart-managed
(gitlab.gatewayApi.class.enabled).
*/}}
{{- define "gitlab.gatewayApi.envoy.proxy.enabled" -}}
{{- $configureEnvoy := eq "true" (include "gitlab.gatewayApi.configureEnvoy" .) -}}
{{- $classEnabled := eq "true" (include "gitlab.gatewayApi.class.enabled" .) -}}
{{- if and $configureEnvoy $classEnabled -}}
true
{{- else -}}
false
{{- end -}}
{{- end -}}

{{/*
Returns true if the Gateway targeted by gatewayRef is in the same namespace as this release.
Policies that target the Gateway directly (ClientTrafficPolicy, SecurityPolicy) do so through
a LocalPolicyTargetReference, which has no namespace field: a targetRef always resolves to a
Gateway in the same namespace as the policy. Any template rendering one of these must check
this first, or it silently targets the wrong (local) Gateway, or none at all, whenever
gatewayRef points at a Gateway in another namespace.
*/}}
{{- define "gitlab.gatewayApi.gateway.sameNamespace" -}}
{{- $gatewayNamespace := (include "gitlab.gatewayApi.gatewayRef" . | fromYamlArray | first).namespace -}}
{{- eq .Release.Namespace $gatewayNamespace -}}
{{- end -}}

{{/*
Returns true if envoy policies targeting the Gateway should be installed (ClientTrafficPolicy,
SecurityPolicy). Installed if Envoy Gateway is configured (see gitlab.gatewayApi.configureEnvoy)
and if the Gateway is in the same namespace as this release; see
gitlab.gatewayApi.gateway.sameNamespace. Do not use this for policies that target a
chart-managed HTTPRoute or TCPRoute instead of the Gateway; see
gitlab.gatewayApi.envoy.installRoutePolicies for those.
*/}}
{{- define "gitlab.gatewayApi.envoy.installPolicies" -}}
{{- $configureEnvoy := and .Values.global.gatewayApi.enabled (eq "true" (include "gitlab.gatewayApi.configureEnvoy" .)) -}}
{{- $gatewayInSameNamespace := eq "true" (include "gitlab.gatewayApi.gateway.sameNamespace" .) -}}
{{- if and $configureEnvoy $gatewayInSameNamespace -}}
true
{{- else -}}
false
{{- end -}}
{{- end -}}

{{/*
Returns true if envoy policies targeting a chart-managed HTTPRoute or TCPRoute should be
installed (BackendTrafficPolicy). Installed if Envoy Gateway is configured (see
gitlab.gatewayApi.configureEnvoy). Unlike gitlab.gatewayApi.envoy.installPolicies, this does
not require the Gateway to be in the same namespace: these policies target a route, which is
always chart-managed and lives in the release namespace regardless of where the Gateway
itself lives, so the Gateway's namespace has no bearing on them.
*/}}
{{- define "gitlab.gatewayApi.envoy.installRoutePolicies" -}}
{{- if and .Values.global.gatewayApi.enabled (eq "true" (include "gitlab.gatewayApi.configureEnvoy" .)) -}}
true
{{- else -}}
false
{{- end -}}
{{- end -}}

{{/*
Returns a target refs to the Gateway resource with a namespace and optionally a section name.
*/}}
{{- define "gitlab.gatewayApi.gatewayRef" -}}
{{- include "gitlab.gatewayApi.gatewayRef.section" (dict "context" . "sectionName" (.Values.gatewayRoute).sectionName) }}
{{- end }}

{{/*
Same as gitlab.gatewayApi.gatewayRef, for a listener other than gatewayRoute.sectionName. Takes a dict
with "context" and "sectionName".
*/}}
{{- define "gitlab.gatewayApi.gatewayRef.section" -}}
{{- $ := .context -}}
{{- template "gitlab.gatewayApi.gatewayRef.local" $ }}
  namespace: {{ coalesce ($.Values.gatewayRoute).gatewayNamespace $.Values.global.gatewayApi.gatewayRef.namespace $.Release.Namespace | quote }}
{{- with .sectionName }}
  sectionName: {{ . | quote }}
{{- end }}
{{- end }}

{{/*
Renders a single listener configuration for the managed Gateway resource.

Input parameters:
local:
  name: Name of the listener
  protocol: Protocol type (HTTPS, HTTP, TCP, or nil)
  hostname: Domain name (e.g., example.com)
  tls:
    mode: TLS termination mode
    certificateRefs: List of certificate references
root:
  protocol: Default protocol (HTTPS or HTTP)
  port: Port for listeners that inherit the root protocol (optional)
  tls:
    secretName: Single Secret to serve from every TLS-terminating listener

The root protocol serves as a default when no local protocol is specified,
enabling centralized protocol configuration for all HTTP(S) workloads and
listeners through a single setting. The root tls.secretName works the same way
for certificates: when set, it replaces the certificateRefs of every listener
that terminates TLS, so a single certificate covering all hostnames does not
have to be repeated per listener.

Port assignment is automatically determined based on the selected protocol. Note: `context`
(the root template context) must be supplied when the resolved protocol is TCP, since it is
needed to resolve `gitlab.shell.port`.
*/}}
{{- define "gitlab.gatewayApi.gateway.listener" -}}
{{- $name := .local.name }}
{{- $protocol := .local.protocol | default .root.protocol | upper }}
{{- $port := 443 }}
{{- if eq "HTTP" $protocol }}
{{-   $port = 80 }}
{{- end }}
{{- if and (not .local.protocol) .root.port }}
{{-   $port = .root.port | int }}
{{- end }}
{{- if eq "TCP" $protocol }}
{{-   $port = (include "gitlab.shell.port" .context | int) }}
{{- end }}
- name: {{ $name }}
  protocol: {{ $protocol }}
  port: {{ $port }}
  allowedRoutes:
    namespaces:
      from: Same
{{- with .local.hostname }}
  hostname: {{ . | quote }}
{{- end }}
{{- if or (eq "HTTPS" $protocol) (eq "TLS" $protocol) }}
{{-   $tls := .local.tls }}
{{-   $sharedSecret := dig "tls" "secretName" "" .root }}
{{-   if and $tls $sharedSecret }}
{{-     $tls = set (deepCopy $tls) "certificateRefs" (list (dict "name" $sharedSecret)) }}
{{-   end }}
{{-   with $tls }}
  tls:
{{-     toYaml . | nindent 4 }}
{{-   end }}
{{- end }}
{{- end -}}

{{/*
Returns true if TLS is terminated by a load balancer in front of the Gateway. Only reads
globals, so subcharts can use it.
*/}}
{{- define "gitlab.gatewayApi.externalTlsTermination" -}}
{{- if and .Values.global.gatewayApi.enabled .Values.global.gatewayApi.externalTlsTermination -}}
true
{{- else -}}
false
{{- end -}}
{{- end -}}

{{/*
Renders the filters of a chart-managed HTTPRoute rule. With external TLS termination, Envoy
receives plain HTTP and would send `X-Forwarded-Proto: http`, so the header is set to `https`.
Gateway API allows one RequestHeaderModifier per rule, so the header is added to an existing
one unless it already sets or adds X-Forwarded-Proto. Envoy Gateway ignores a `set` entry
for a header that the same filter adds, so none is rendered in that case.

Input: dict "filters" (list) "context" (root context)
*/}}
{{- define "gitlab.gatewayApi.routeFilters" -}}
{{- $filters := deepCopy (.filters | default list) -}}
{{- if eq "true" (include "gitlab.gatewayApi.externalTlsTermination" .context) -}}
{{-   $header := dict "name" "X-Forwarded-Proto" "value" "https" -}}
{{-   $modifierFound := false -}}
{{-   range $filter := $filters -}}
{{-     if eq $filter.type "RequestHeaderModifier" -}}
{{-       $modifierFound = true -}}
{{-       $modifier := $filter.requestHeaderModifier | default dict -}}
{{-       $set := $modifier.set | default list -}}
{{-       $headerSet := false -}}
{{-       range concat $set ($modifier.add | default list) -}}
{{-         if eq (lower .name) "x-forwarded-proto" -}}
{{-           $headerSet = true -}}
{{-         end -}}
{{-       end -}}
{{-       if not $headerSet -}}
{{-         $_ := set $modifier "set" (append $set $header) -}}
{{-       end -}}
{{-       $_ := set $filter "requestHeaderModifier" $modifier -}}
{{-     end -}}
{{-   end -}}
{{-   if not $modifierFound -}}
{{-     $filters = prepend $filters (dict "type" "RequestHeaderModifier" "requestHeaderModifier" (dict "set" (list $header))) -}}
{{-   end -}}
{{- end -}}
{{- if $filters -}}
{{-   $filters | toYaml -}}
{{- end -}}
{{- end -}}

{{/*
Returns true if the http-default listener should be included on the managed Gateway.
Enabled when either configureCertmanager or httpToHttpsRedirect (with HTTPS protocol or
external TLS termination) is active, and the Gateway is managed (no external gatewayRef).
*/}}
{{- define "gitlab.gatewayApi.httpDefault.enabled" -}}
{{- $managed := and .Values.global.gatewayApi.enabled (not .Values.global.gatewayApi.gatewayRef) -}}
{{- $certmanager := eq "true" (include "gitlab.gatewayApi.configureCertmanager" .) -}}
{{- $https := or (eq (upper .Values.gatewayApiResources.gateway.protocol) "HTTPS") (eq "true" (include "gitlab.gatewayApi.externalTlsTermination" .)) -}}
{{- $redirect := and .Values.global.gatewayApi.httpToHttpsRedirect $https -}}
{{- if and $managed (or $certmanager $redirect) -}}
true
{{- end -}}
{{- end -}}

{{/*
Returns true if the HTTP-to-HTTPS redirect HTTPRoute should be rendered.
Enabled when gatewayApi is enabled, httpToHttpsRedirect is true, protocol
is HTTPS or TLS is terminated externally, and the Gateway is managed (no external gatewayRef).
*/}}
{{- define "gitlab.gatewayApi.httpRedirect.enabled" -}}
{{- $enabled := and .Values.global.gatewayApi.enabled .Values.global.gatewayApi.httpToHttpsRedirect -}}
{{- $httpsProtocol := or (eq (upper .Values.gatewayApiResources.gateway.protocol) "HTTPS") (eq "true" (include "gitlab.gatewayApi.externalTlsTermination" .)) -}}
{{- $managedGateway := not .Values.global.gatewayApi.gatewayRef -}}
{{- if and $enabled $httpsProtocol $managedGateway -}}
true
{{- end -}}
{{- end -}}

{{/*
Checks if a Route should be enabled. Defaults to global GatewayAPI toggle but can be 
configured per Route by setting true/false explicitly.
*/}}
{{- define "gitlab.gatewayApi.route.enabled" -}}
{{- if not (eq nil .Values.gatewayRoute.enabled) -}}
{{-   .Values.gatewayRoute.enabled -}}
{{- else }}
{{-   .Values.global.gatewayApi.enabled -}}
{{- end -}}
{{- end -}}

{{/*
Checks if cert-manager should be wired into the Gateway API routing path. Requires both
global.gatewayApi.enabled and global.gatewayApi.configureCertmanager: the Issuer this
produces solves HTTP01 challenges through a gatewayHTTPRoute, so it is inert without a
Gateway to attach to. Skipped with external TLS termination.
*/}}
{{- define "gitlab.gatewayApi.configureCertmanager" -}}
{{- $externalTls := eq "true" (include "gitlab.gatewayApi.externalTlsTermination" .) -}}
{{- if and .Values.global.gatewayApi.enabled .Values.global.gatewayApi.configureCertmanager (not $externalTls) -}}
true
{{- else -}}
false
{{- end -}}
{{- end -}}

{{/*
Renders the name of the HTTP01 Issuer for managing certificates used by GatewayAPI.
Different from the Issuer used for Ingresses.
*/}}
{{- define "gitlab.gatewayApi.certmanager.issuer" -}}
{{- printf "%s-gw-issuer" .Release.Name -}}
{{- end -}}

{{/*
Renders certmanager annotations for the Gateway resource.
https://cert-manager.io/docs/usage/gateway/
*/}}
{{- define "gitlab.gatewayApi.certmanager.annotations" -}}
{{- if eq "true" (include "gitlab.gatewayApi.configureCertmanager" .) -}}
cert-manager.io/issuer: {{ include "gitlab.gatewayApi.certmanager.issuer" . }}
{{- end -}}
{{- end -}}

{{/*
Returns the effective hostname for the gitlab-web Gateway listener and the
webservice HTTPRoute. When global.hosts.gitlab.hostnameOverride is set it
takes precedence; otherwise the standard gitlab hostname is used.

Both the Gateway listener and the HTTPRoute must match on the same hostname
for the route to attach, so a single template keeps the two call sites in
sync if the override logic ever changes.
*/}}
{{- define "gitlab.gitlab.gatewayHostname" -}}
{{- .Values.global.hosts.gitlab.hostnameOverride | default (include "gitlab.gitlab.hostname" .) -}}
{{- end -}}

{{/*
Renders true if Gateway resources should be configured for Geo traffic.
*/}}
{{- define "gitlab.gatewayApi.gateway.geo.configure" -}}
{{ if and .Values.global.geo.enabled .Values.global.geo.gatewayApi.additionalHostname .Values.global.gatewayApi.enabled -}}
true
{{- else -}}
false
{{- end -}}
{{- end -}}

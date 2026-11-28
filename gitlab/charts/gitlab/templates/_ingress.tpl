
{{/* ######### ingress templates */}}

{{/*
Return the appropriate apiVersion for Ingress.

It expects a dictionary with three entries:
  - `global` which contains global ingress settings, e.g. .Values.global.ingress
  - `local` which contains local ingress settings, e.g. .Values.ingress
  - `context` which is the parent context (either `.` or `$`)

Example usage:
{{- $ingressCfg := dict "global" .Values.global.ingress "local" .Values.ingress "context" . -}}
kubernetes.io/ingress.provider: "{{ template "gitlab.ingress.provider" $ingressCfg }}"
*/}}
{{- define "gitlab.ingress.apiVersion" -}}
{{-   if .local.apiVersion -}}
{{-     .local.apiVersion -}}
{{-   else if .global.apiVersion -}}
{{-     .global.apiVersion -}}
{{-   else -}}
{{-     print "networking.k8s.io/v1" -}}
{{-   end -}}
{{- end -}}

{{/*
Renders the `pathType` and `backend` of a `networking.k8s.io/v1` Ingress path.
`gitlab.checkConfig.ingress.apiVersion` rejects any other Ingress apiVersion.

It expects a dictionary with three entries:
  - `serviceName` the name of the backend Service
  - `servicePort` the port number of the backend Service
  - `pathType` the pathType of the path

Example usage:
{{- include "gitlab.ingress.pathBackend" (dict "serviceName" $name "servicePort" $port "pathType" "Prefix") | nindent 12 }}
*/}}
{{- define "gitlab.ingress.pathBackend" -}}
pathType: {{ .pathType }}
backend:
  service:
    name: {{ .serviceName }}
    port:
      number: {{ .servicePort }}
{{- end -}}

{{/*
Returns the ingress provider

It expects a dictionary with two entries:
  - `global` which contains global ingress settings, e.g. .Values.global.ingress
  - `local` which contains local ingress settings, e.g. .Values.ingress
*/}}
{{- define "gitlab.ingress.provider" -}}
{{- default .global.provider .local.provider -}}
{{- end -}}

{{/*
Overrides the ingress-nginx template to make sure gitlab-shell name matches
*/}}
{{- define "ingress-nginx.tcp-configmap" -}}
{{ .Release.Name}}-nginx-ingress-tcp
{{- end -}}

{{/*
Calculates the IngressClass name.

It expects either:
  - a dictionary with two entries:
    - `global` which contains global ingress settings, e.g. .Values.global.ingress
    - `context` which is the parent context (either `.` or `$`)
  - the parent context ($ from caller)
    - This detected by access to both `.Capabilities` and `.Release`

If the value is not set or is set to nil, then it provides a default.
Otherwise, it will use the given value (even an empty string "").
*/}}
{{- define "ingress.class.name" -}}
{{-   $here := dict }}
{{-   if and (hasKey $ "Release") (hasKey $ "Capabilities") -}}
{{-     $here = dict "global" $.Values.global.ingress "context" $ -}}
{{-   else -}}
{{-     $here = . -}}
{{-   end -}}
{{-   if kindIs "invalid" $here.global.class -}}
{{-     printf "%s-nginx" $here.context.Release.Name -}}
{{-   else -}}
{{-     $here.global.class -}}
{{-   end -}}
{{- end -}}

{{/*
Sets `ingressClassName`, unless the class is `none`.

It expects a dictionary with two entries:
  - `global` which contains global ingress settings, e.g. .Values.global.ingress
  - `context` which is the parent context (either `.` or `$`)
*/}}
{{- define "ingress.class.field" -}}
{{-   if not (eq (default "" .global.class) "none" ) -}}
ingressClassName: {{ include "ingress.class.name" . | quote }}
{{-   end -}}
{{- end -}}

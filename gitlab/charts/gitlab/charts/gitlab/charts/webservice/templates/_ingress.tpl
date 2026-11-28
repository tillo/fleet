{{/*
Renders a Ingress for the webservice

It expects a dictionary with three entries:
  - `name` the Ingress name to use
  - `root` the root context
  - `deployment` the context of the deployment to render the Ingress for
  - `host` the host to use in the Ingress rule and TLS config
  - `tlsSecret` the tls secret to use
*/}}
{{- define "webservice.ingress.template" -}}
{{- $global := .root.Values.global }}
{{- if .ingressCfg.local.path }}
---
apiVersion: {{ template "gitlab.ingress.apiVersion" .ingressCfg }}
kind: Ingress
metadata:
  name: {{ .name }}
  namespace: {{ .root.Release.Namespace }}
  labels:
    {{- include "gitlab.standardLabels" .root | nindent 4 }}
    {{- include "webservice.labels" .deployment | nindent 4 }}
    {{- include "webservice.commonLabels" .deployment | nindent 4 }}
  annotations:
    kubernetes.io/ingress.provider: "{{ template "gitlab.ingress.provider" .ingressCfg }}"
    {{- include "gitlab.certmanager_annotations" .root | nindent 4 }}
  {{- range $key, $value := merge .ingressCfg.local.annotations $global.ingress.annotations (include "webservice.ingress.nginx.annotations" . | fromYaml)}}
    {{ $key }}: {{ $value | quote }}
  {{- end }}
spec:
  {{- if .ingressCfg.local.useGeoClass }}
    {{- include "webservice.geo.ingress.class.field" .ingressCfg | nindent 2 }}
  {{- else }}
    {{- include "ingress.class.field" .ingressCfg | nindent 2 }}
  {{- end }}
  rules:
    - host: {{ .host }}
      http:
        paths:
          - path: {{ $global.appConfig.relativeUrlRoot }}{{ .deployment.ingress.path }}
            {{- include "gitlab.ingress.pathBackend" (dict "serviceName" (include "webservice.fullname.withSuffix" .deployment) "servicePort" .root.Values.service.workhorseExternalPort "pathType" (default .deployment.ingress.pathType $global.ingress.pathType)) | nindent 12 }}
  {{- if (and .tlsSecret (eq (include "gitlab.ingress.tls.enabled" .root) "true" )) }}
  tls:
    - hosts:
      - {{ .host }}
      secretName: {{ .tlsSecret }}
  {{- else }}
  tls: []
  {{- end }}
{{- end }}
{{- end -}}

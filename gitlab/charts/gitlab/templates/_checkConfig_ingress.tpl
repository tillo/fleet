{{- define "gitlab.checkConfig.ingress.alternatives" -}}
{{-   if and (index $.Values "nginx-ingress").enabled $.Values.haproxy.install -}}
ingress:
  HAProxy is also enabled via `haproxy.install=true`.
  Please disable NGINX via `nginx-ingress.enabled=false`.
{{-   end -}}
{{-   if and (index $.Values "nginx-ingress").enabled $.Values.traefik.install -}}
ingress:
  Traefik is also enabled via `traefik.install=true`.
  Please disable NGINX via `nginx-ingress.enabled=false`.
{{-   end -}}
{{- end -}}

{{- define "gitlab.checkConfig.ingress.class" -}}
{{-   if $.Values.traefik.install -}}
{{-     if ne $.Values.global.ingress.class "traefik" -}}
ingress:
  Traefik is enabled.
  Please set `global.ingress.class=traefik`.
{{-     end -}}
{{-   end -}}
{{-   if $.Values.haproxy.install -}}
{{-     if ne $.Values.global.ingress.class "haproxy" -}}
ingress:
  HAProxy is enabled.
  Please set `global.ingress.class=haproxy`.
{{-     end -}}
{{-   end -}}
{{- end -}}

{{/*
Ensure that every Ingress apiVersion override is supported by the chart.
Legacy Ingress apiVersions were removed in Kubernetes 1.22 and the chart
only renders `networking.k8s.io/v1` objects.
This check runs whether or not Ingress is enabled.
*/}}
{{- define "gitlab.checkConfig.ingress.apiVersion" -}}
{{-   $supported := list "networking.k8s.io/v1" -}}
{{-   $sources := dict "global.ingress.apiVersion" $.Values.global.ingress.apiVersion -}}
{{-   $_ := set $sources "registry.ingress.apiVersion" (dig "ingress" "apiVersion" "" $.Values.registry) -}}
{{-   range $chart := list "webservice" "kas" "gitlab-pages" -}}
{{-     $values := index $.Values.gitlab $chart | default dict -}}
{{-     $_ := set $sources (printf "gitlab.%s.ingress.apiVersion" $chart) (dig "ingress" "apiVersion" "" $values) -}}
{{-   end -}}
{{-   $webservice := $.Values.gitlab.webservice | default dict -}}
{{-   $_ := set $sources "gitlab.webservice.extraIngress.apiVersion" (dig "extraIngress" "apiVersion" "" $webservice) -}}
{{-   range $name, $deployment := (dig "deployments" dict $webservice) -}}
{{-     range $key := list "ingress" "extraIngress" -}}
{{-       $value := dig $key "apiVersion" "" ($deployment | default dict) -}}
{{/*      Skip values that webservice.datamodel.prepare copied from the chart-level setting */}}
{{-       if ne (toString $value) (toString (dig $key "apiVersion" "" $webservice)) -}}
{{-         $_ := set $sources (printf "gitlab.webservice.deployments.%s.%s.apiVersion" $name $key) $value -}}
{{-       end -}}
{{-     end -}}
{{-   end -}}
{{-   $invalid := list -}}
{{-   range $key := keys $sources | sortAlpha -}}
{{-     $value := index $sources $key -}}
{{-     if and $value (not (has $value $supported)) -}}
{{-       $invalid = append $invalid (printf "`%s: %s`" $key $value) -}}
{{-     end -}}
{{-   end -}}
{{-   if $invalid -}}
ingress:
  Ingress objects support only: {{ join ", " $supported }}.
  Remove or update the following settings: {{ join ", " $invalid }}
{{-   end -}}
{{- end -}}
{{/* END gitlab.checkConfig.ingress.apiVersion */}}

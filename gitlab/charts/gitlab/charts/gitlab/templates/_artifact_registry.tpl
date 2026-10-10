{{/* ######### Artifact Registry related templates */}}

{{/*
Return the Artifact Registry service token secret.

The chart does not create this secret. The token is shared with the Artifact
Registry service, which validates requests against its own copy, so a value the
chart generated would authenticate against nothing. An operator supplies the
secret and names it here; leaving it unset mounts nothing.
*/}}

{{- define "gitlab.appConfig.artifactRegistry.authToken.secret" -}}
{{- ((.Values.global.appConfig.artifactRegistry).authToken).secret | quote -}}
{{- end -}}

{{- define "gitlab.appConfig.artifactRegistry.authToken.key" -}}
{{- default "token" ((.Values.global.appConfig.artifactRegistry).authToken).key | quote -}}
{{- end -}}

{{/*
Mount secret for the Artifact Registry service token
*/}}
{{- define "gitlab.appConfig.artifactRegistry.mountSecrets" -}}
{{- if and .Values.global.appConfig.artifactRegistry.enabled ((.Values.global.appConfig.artifactRegistry).authToken).secret -}}
# mount secret for artifact registry service token
- secret:
    name: {{ template "gitlab.appConfig.artifactRegistry.authToken.secret" . }}
    items:
      - key: {{ template "gitlab.appConfig.artifactRegistry.authToken.key" . }}
        path: artifact-registry/.gitlab_artifact_registry_secret
{{- end -}}
{{- end -}}

{{/*
Ensure Registry object store secret is configured.
*/}}
{{- define "gitlab.checkConfig.objectStorage.registry.configured" -}}
{{-   with $.Values.registry -}}
{{-     if and .enabled (not .storage.secret) }}
Registry Object Storage:
  The chart provides no longer bundled object storage solution. Please
  prepare an external object storage solution for the Registry by following 
  https://docs.gitlab.com/charts/advanced/external-object-storage/#registry-configuration
{{-     end -}}
{{-   end -}}
{{- end -}}

{{/*
Ensure Pages object store secret is configured.

Only required when consolidated object storage is disabled. When consolidated
object storage is enabled, Pages inherits the shared `object_store.connection`,
so a per-Pages connection is neither needed nor rendered.
*/}}
{{- define "gitlab.checkConfig.objectStorage.pages.configured" -}}
{{-   with $.Values.global.pages -}}
{{-     if and .enabled .objectStore.enabled (empty .objectStore.connection) (not $.Values.global.appConfig.object_store.enabled) }}
Pages Object Storage:
  The chart provides no longer bundled object storage solution. Please
  prepare an external object storage solution for Pages by following
  https://docs.gitlab.com/charts/advanced/external-object-storage/
{{-     end -}}
{{-   end -}}
{{- end -}}

{{/*
Ensure consolidate and type-specific object store configuration are not mixed.
*/}}
{{- define "gitlab.checkConfig.objectStorage.consolidatedConfig" -}}
{{-   if $.Values.global.appConfig.object_store.enabled -}}
{{-     $problematicTypes := list -}}
{{-     range $objectTypes := list "artifacts" "lfs" "uploads" "packages" "externalDiffs" "terraformState" "dependencyProxy" -}}
{{-       if hasKey $.Values.global.appConfig . -}}
{{-         $objectProps := index $.Values.global.appConfig . -}}
{{-         if (and (index $objectProps "enabled") (or (not (empty (index $objectProps "connection"))) (empty (index $objectProps "bucket")))) -}}
{{-           $problematicTypes = append $problematicTypes . -}}
{{-         end -}}
{{-       end -}}
{{-     end -}}
{{-     if not (empty $problematicTypes) }}
Object Storage:
  When consolidated object storage is enabled, for each item `bucket` must be specified and the `connection` must be empty. Check the following object storage configuration(s): {{ join "," $problematicTypes }}
{{-     end -}}
{{-   end -}}
{{- end -}}
{{/* END gitlab.checkConfig.objectStorage.consolidatedConfig */}}

{{- define "gitlab.checkConfig.objectStorage.typeSpecificConfig" -}}
{{-   if not $.Values.global.appConfig.object_store.enabled -}}
{{-     $problematicTypes := list -}}
{{-     range $objectTypes := list "artifacts" "lfs" "uploads" "packages" "externalDiffs" "terraformState" "dependencyProxy" "ciSecureFiles" -}}
{{-       if hasKey $.Values.global.appConfig . -}}
{{-         $objectProps := index $.Values.global.appConfig . -}}
{{-         if and (index $objectProps "enabled") (empty (index $objectProps "connection")) -}}
{{-           $problematicTypes = append $problematicTypes . -}}
{{-         end -}}
{{-       end -}}
{{-     end -}}
{{-     if not (empty $problematicTypes) }}
Object Storage:
  When type-specific object storage is enabled the `connection` property can not be empty. Check the following object storage configuration(s): {{ join "," $problematicTypes }}
{{-     end -}}
{{-   end -}}
{{- end -}}
{{/* END gitlab.checkConfig.objectStorage.typeSpecificConfig */}}

{{- define "gitlab.checkConfig.objectStorage.allowedDownloadModes" -}}
{{-   $validModes := list "proxy" "direct" -}}
{{-   $invalidTypes := list -}}
{{-   $allTypes := list "object_store" "artifacts" "lfs" "uploads" "packages" "externalDiffs" "terraformState" "dependencyProxy" "ciSecureFiles" -}}
{{-   range $type := $allTypes -}}
{{-     if hasKey $.Values.global.appConfig $type -}}
{{-       $config := index $.Values.global.appConfig $type -}}
{{-       if hasKey $config "allowed_download_modes" -}}
{{-         $modes := index $config "allowed_download_modes" -}}
{{-         if kindIs "slice" $modes -}}
{{-           range $mode := $modes -}}
{{-             if not (has $mode $validModes) -}}
{{-               $invalidTypes = append $invalidTypes $type -}}
{{-             end -}}
{{-           end -}}
{{-         else if not (empty $modes) -}}
{{-           $invalidTypes = append $invalidTypes $type -}}
{{-         end -}}
{{-       end -}}
{{-     end -}}
{{-   end -}}
{{-   $invalidTypes = uniq $invalidTypes -}}
{{-   if not (empty $invalidTypes) }}
Object Storage:
  `allowed_download_modes` contains invalid mode(s). Valid modes are: {{ join ", " $validModes }}. Check the following object storage configuration(s): {{ join ", " $invalidTypes }}
{{-   end -}}
{{- end -}}
{{/* END gitlab.checkConfig.objectStorage.allowedDownloadModes */}}

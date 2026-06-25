{{/*
Chart name+version, used in the helm.sh/chart label.
*/}}
{{- define "priva-cloud.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Install namespace: namespaceOverride, else the release namespace.
*/}}
{{- define "priva-cloud.namespace" -}}
{{- default .Release.Namespace .Values.namespaceOverride -}}
{{- end -}}

{{/*
Common labels added to every resource's metadata.labels.
NOTE: never put these on a Deployment/Service `selector` — the operator code and the
InferencePool select runner pods by the literal `app:` label, so selectors stay verbatim.
*/}}
{{- define "priva-cloud.labels" -}}
helm.sh/chart: {{ include "priva-cloud.chart" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: priva-cloud
{{- with .Chart.AppVersion }}
app.kubernetes.io/version: {{ . | quote }}
{{- end }}
{{- end -}}

{{/*
Resolve a service image ref from a (svc, root) pair.
  {{ include "priva-cloud.image" (dict "svc" .Values.services.operator "root" .) }}
*/}}
{{- define "priva-cloud.image" -}}
{{- $reg := .root.Values.image.registry -}}
{{- $repo := .svc.repository -}}
{{- $tag := .svc.tag | default .root.Values.image.tag -}}
{{- if $reg -}}{{ printf "%s/%s:%s" $reg $repo $tag }}{{- else -}}{{ printf "%s:%s" $repo $tag }}{{- end -}}
{{- end -}}

{{/*
The per-account agent-runner image ref published to the operator (ConfigMap RUNNER_IMAGE).
*/}}
{{- define "priva-cloud.runnerImage" -}}
{{- include "priva-cloud.image" (dict "svc" .Values.services.agentRunner "root" .) -}}
{{- end -}}

{{/*
The dev nfs-xfs image ref.
*/}}
{{- define "priva-cloud.devStorageImage" -}}
{{- $reg := .Values.image.registry -}}
{{- $repo := .Values.devStorage.image.repository -}}
{{- $tag := .Values.devStorage.image.tag | default .Values.image.tag -}}
{{- if $reg -}}{{ printf "%s/%s:%s" $reg $repo $tag }}{{- else -}}{{ printf "%s:%s" $repo $tag }}{{- end -}}
{{- end -}}

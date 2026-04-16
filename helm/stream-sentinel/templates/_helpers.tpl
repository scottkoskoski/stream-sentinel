{{/*
Expand the name of the chart.
*/}}
{{- define "stream-sentinel.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "stream-sentinel.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "stream-sentinel.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels applied to every resource.
*/}}
{{- define "stream-sentinel.labels" -}}
helm.sh/chart: {{ include "stream-sentinel.chart" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: stream-sentinel
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{ include "stream-sentinel.selectorLabels" . }}
{{- end }}

{{/*
Selector labels (subset used in matchLabels).
*/}}
{{- define "stream-sentinel.selectorLabels" -}}
app.kubernetes.io/name: {{ include "stream-sentinel.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
ServiceAccount name.
*/}}
{{- define "stream-sentinel.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "stream-sentinel.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Namespace to deploy into.
*/}}
{{- define "stream-sentinel.namespace" -}}
{{- .Values.global.namespace.name | default "stream-sentinel" }}
{{- end }}

{{/*
Consumer labels for a specific consumer.
Arguments: dict with "root" (top-level context) and "consumerName" (string).
*/}}
{{- define "stream-sentinel.consumerLabels" -}}
{{ include "stream-sentinel.labels" .root }}
app.kubernetes.io/component: consumer
app.kubernetes.io/name: {{ .consumerName }}
{{- end }}

{{/*
Consumer selector labels.
*/}}
{{- define "stream-sentinel.consumerSelectorLabels" -}}
app.kubernetes.io/name: {{ .consumerName }}
app.kubernetes.io/component: consumer
app.kubernetes.io/instance: {{ .root.Release.Name }}
{{- end }}

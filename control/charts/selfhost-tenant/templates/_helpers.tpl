{{/* Common labels stamped on every tenant resource. */}}
{{- define "selfhost-tenant.labels" -}}
app.kubernetes.io/managed-by: selfhost-k8s
selfhost.sh/tenant-name: {{ .Values.tenant_name | quote }}
selfhost.sh/pack: {{ .Values.pack | quote }}
{{- end -}}

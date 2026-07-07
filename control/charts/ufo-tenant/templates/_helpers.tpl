{{/* Common labels stamped on every tenant resource. */}}
{{- define "ufo-tenant.labels" -}}
app.kubernetes.io/managed-by: ufo-control
flyingobject.ai/tenant-name: {{ .Values.tenant_name | quote }}
flyingobject.ai/pack: {{ .Values.pack | quote }}
{{- end -}}

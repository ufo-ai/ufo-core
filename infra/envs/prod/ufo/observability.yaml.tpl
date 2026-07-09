# The shared in-cluster OpenTelemetry collector: every tenant's serve process exports OTLP here and
# the collector forwards traces/metrics/logs to Datadog with the contrib image's native datadog
# exporter. The Datadog API key arrives via External Secrets (the datadog-api-key Secret, synced from
# the api-keys Secrets Manager entry) and MUST be populated out-of-band before deploy — an empty key
# fails the datadog exporter at startup (fail loud). The collector base its OTLP/HTTP endpoint feeds
# is templated into every tenant's [o11y] otlp_endpoint by the reconciler (ufo.tf platform_config).
apiVersion: v1
kind: ConfigMap
metadata:
  name: otel-collector-config
  namespace: ${namespace}
data:
  otel-collector-config.yaml: |
    receivers:
      otlp:
        protocols:
          # Bind all interfaces: the collector defaults the OTLP endpoint to localhost, which silently
          # drops every cross-pod export (tenant serve pods -> collector Service).
          grpc:
            endpoint: 0.0.0.0:4317
          http:
            endpoint: 0.0.0.0:4318
    extensions:
      health_check:
        endpoint: 0.0.0.0:13133
    processors:
      batch: {}
      # Tenant exporters don't set deployment.environment; the collector stamps its cluster's env
      # on everything it forwards so Datadog's env tag separates prod from testing telemetry.
      resource:
        attributes:
          - {key: deployment.environment, value: "${dd_env}", action: upsert}
    connectors:
      datadog/connector: {}
    exporters:
      datadog:
        api:
          key: $${env:DD_API_KEY}
          site: $${env:DD_SITE}
    service:
      extensions: [health_check]
      pipelines:
        traces:
          receivers: [otlp]
          processors: [resource, batch]
          exporters: [datadog/connector, datadog]
        metrics:
          receivers: [otlp, datadog/connector]
          processors: [resource, batch]
          exporters: [datadog]
        logs:
          receivers: [otlp]
          processors: [resource, batch]
          exporters: [datadog]
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: otel-collector
  namespace: ${namespace}
  labels: {app.kubernetes.io/name: otel-collector}
spec:
  replicas: 1
  selector:
    matchLabels: {app.kubernetes.io/name: otel-collector}
  template:
    metadata:
      labels: {app.kubernetes.io/name: otel-collector}
    spec:
      containers:
        - name: collector
          image: ${collector_image}
          args: [--config=/conf/otel-collector-config.yaml]
          env:
            - name: DD_API_KEY
              valueFrom:
                secretKeyRef: {name: datadog-api-key, key: DD_API_KEY}
            - {name: DD_SITE, value: "${dd_site}"}
          ports:
            - {name: otlp-grpc, containerPort: 4317}
            - {name: otlp-http, containerPort: 4318}
            - {name: health, containerPort: 13133}
          readinessProbe:
            httpGet: {path: /, port: health}
          livenessProbe:
            httpGet: {path: /, port: health}
          resources:
            requests: {cpu: 100m, memory: 256Mi}
            limits: {cpu: 500m, memory: 1Gi}
          volumeMounts:
            - {name: config, mountPath: /conf, readOnly: true}
      volumes:
        - name: config
          configMap: {name: otel-collector-config}
---
apiVersion: v1
kind: Service
metadata:
  name: otel-collector
  namespace: ${namespace}
spec:
  selector: {app.kubernetes.io/name: otel-collector}
  ports:
    - {name: otlp-grpc, port: 4317, targetPort: otlp-grpc}
    - {name: otlp-http, port: 4318, targetPort: otlp-http}
---
# Operator-only in ufo-system (NOT the replicated ufo-platform-secrets, so it never reaches a tenant
# pod): the collector reads DD_API_KEY, tenants never do. Reuses the ClusterSecretStore that
# cluster-services.yaml.tpl defines. The value arrives empty from api-keys until set out-of-band.
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: datadog-api-key
  namespace: ${namespace}
spec:
  refreshInterval: 1h
  secretStoreRef: {name: ufo-aws-sm, kind: ClusterSecretStore}
  target: {name: datadog-api-key}
  data:
    - {secretKey: DD_API_KEY, remoteRef: {key: ${secret_api_keys}, property: datadog-api-key}}
---
# Cross-namespace ingress to the collector on the OTLP ports. Tenant namespaces carry the operator's
# label (ufo_control.platform.TENANT_NAMESPACE_LABEL = flyingobject.ai/tenant); same-namespace senders
# (the apex workspace, control plane) reach it too. Selecting the collector pod flips it to
# default-deny ingress, so this rule is the only path in. Egress stays open (Datadog on 443).
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: otel-collector-ingress
  namespace: ${namespace}
spec:
  podSelector:
    matchLabels: {app.kubernetes.io/name: otel-collector}
  policyTypes: [Ingress]
  ingress:
    - from:
        - namespaceSelector:
            matchLabels: {flyingobject.ai/tenant: "true"}
        - podSelector: {}
      ports:
        - {protocol: TCP, port: 4317}
        - {protocol: TCP, port: 4318}

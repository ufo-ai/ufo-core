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
---
# Node-level log agent: a DaemonSet on every node tails the containerd pod logs under /var/log/pods
# and forwards them over OTLP to the gateway collector above, which holds the sole Datadog key. This
# captures container stdout/stderr the OTLP SDK never sees — crashes, panics, pre-init output — which
# the app-level OTLP logs miss entirely. Only ufo's own namespaces are collected (ufo-system + every
# tenant, all sharing the ufo- prefix); third-party add-ons (kube-system, cert-manager, ingress-nginx)
# and the collectors' own pods are excluded so the pipeline can't feed itself.
apiVersion: v1
kind: ConfigMap
metadata:
  name: otel-logs-agent-config
  namespace: ${namespace}
data:
  otel-logs-agent-config.yaml: |
    receivers:
      filelog:
        # Pod dirs are <namespace>_<pod>_<uid>; the ufo- prefix scopes to system + tenant namespaces.
        include: [/var/log/pods/ufo-*_*/*/*.log]
        # Never tail the collectors' own pods (this agent + the gateway) — that is the feedback loop.
        exclude: [/var/log/pods/${namespace}_otel-*/*/*.log]
        # Only new lines: a rollout must not replay a node's entire log history into Datadog.
        start_at: end
        storage: file_storage
        include_file_path: true
        operators:
          # Parses the containerd/CRI line format and lifts k8s.pod.name / k8s.namespace.name /
          # k8s.container.name / k8s.pod.uid off the file path onto the resource.
          - {type: container, id: container-parser, max_log_size: 1MiB}
    extensions:
      health_check:
        endpoint: 0.0.0.0:13133
      file_storage:
        directory: /var/lib/otel-logs-agent/file_storage
        create_directory: true
    processors:
      batch: {}
      # Enriches each record with the workload it came from (deployment/daemonset/statefulset + node),
      # associated by the pod uid the container parser already set. Backed by the ClusterRole below.
      k8sattributes:
        auth_type: serviceAccount
        filter: {node_from_env_var: KUBE_NODE_NAME}
        extract:
          metadata:
            - k8s.namespace.name
            - k8s.pod.name
            - k8s.pod.uid
            - k8s.deployment.name
            - k8s.daemonset.name
            - k8s.statefulset.name
            - k8s.node.name
        pod_association:
          - sources: [{from: resource_attribute, name: k8s.pod.uid}]
          - sources:
              - {from: resource_attribute, name: k8s.namespace.name}
              - {from: resource_attribute, name: k8s.pod.name}
    exporters:
      # Forward to the gateway Service (same namespace, admitted by its NetworkPolicy); the gateway
      # stamps deployment.environment and owns the only DD_API_KEY. In-cluster hop, so plaintext OTLP.
      otlp/gateway:
        endpoint: otel-collector.${namespace}.svc.cluster.local:4317
        tls: {insecure: true}
    service:
      extensions: [health_check, file_storage]
      pipelines:
        logs:
          receivers: [filelog]
          processors: [k8sattributes, batch]
          exporters: [otlp/gateway]
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: otel-logs-agent
  namespace: ${namespace}
---
# k8sattributes reads pod/namespace/node facts and resolves deployment names via the owning
# ReplicaSet, so the agent needs cluster-wide read on exactly those — no write, no secrets.
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: otel-logs-agent
rules:
  - {apiGroups: [""], resources: [pods, namespaces, nodes], verbs: [get, list, watch]}
  - {apiGroups: [apps], resources: [replicasets], verbs: [get, list, watch]}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: otel-logs-agent
roleRef: {apiGroup: rbac.authorization.k8s.io, kind: ClusterRole, name: otel-logs-agent}
subjects:
  - {kind: ServiceAccount, name: otel-logs-agent, namespace: ${namespace}}
---
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: otel-logs-agent
  namespace: ${namespace}
  labels: {app.kubernetes.io/name: otel-logs-agent}
spec:
  selector:
    matchLabels: {app.kubernetes.io/name: otel-logs-agent}
  template:
    metadata:
      labels: {app.kubernetes.io/name: otel-logs-agent}
    spec:
      serviceAccountName: otel-logs-agent
      # Land on every node, including any tainted tenant node groups.
      tolerations:
        - {operator: Exists}
      containers:
        - name: collector
          image: ${collector_image}
          args: [--config=/conf/otel-logs-agent-config.yaml]
          env:
            - name: KUBE_NODE_NAME
              valueFrom:
                fieldRef: {fieldPath: spec.nodeName}
          # /var/log/pods files are root-owned on the node; read them as root, read-only.
          securityContext: {runAsUser: 0}
          ports:
            - {name: health, containerPort: 13133}
          readinessProbe:
            httpGet: {path: /, port: health}
          livenessProbe:
            httpGet: {path: /, port: health}
          resources:
            requests: {cpu: 100m, memory: 192Mi}
            limits: {cpu: 500m, memory: 512Mi}
          volumeMounts:
            - {name: config, mountPath: /conf, readOnly: true}
            - {name: storage, mountPath: /var/lib/otel-logs-agent/file_storage}
            - {name: varlogpods, mountPath: /var/log/pods, readOnly: true}
      volumes:
        - name: config
          configMap: {name: otel-logs-agent-config}
        - name: storage
          hostPath: {path: /var/lib/otel-logs-agent/file_storage, type: DirectoryOrCreate}
        - name: varlogpods
          hostPath: {path: /var/log/pods}

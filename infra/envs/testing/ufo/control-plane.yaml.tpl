# The ufo control plane — the operator ServiceAccount + a broad ClusterRole (it manages tenant
# namespaces and every resource the tenant chart applies via helm), the deploy API, and the
# leader-elected operator. Templated from control/deploy/control-plane.yaml, adapted to the prod
# image (ECR ufo-control by tag) and the renamed group/namespace. The ufo-system Namespace, the
# platform ConfigMap, and the ufo-control-secrets / ufo-platform-secrets Secrets are applied by
# terraform (ufo.tf); this file assumes they exist.
apiVersion: v1
kind: ServiceAccount
metadata:
  name: ufo-operator
  namespace: ${namespace}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: ufo-operator
rules:
  # The Tenant kind + its status.
  - apiGroups: [flyingobject.ai]
    resources: [tenants]
    verbs: [get, list, watch, create, update, patch]
  - apiGroups: [flyingobject.ai]
    resources: [tenants/status]
    verbs: [get, update, patch]
  # Tenant namespaces and the per-tenant Secret (config + minted keys).
  - apiGroups: [""]
    resources: [namespaces]
    verbs: [get, list, create, update, patch]
  - apiGroups: [""]
    resources: [secrets, services, serviceaccounts, resourcequotas, limitranges, configmaps]
    verbs: [get, list, create, update, patch, delete]
  # The tenant workload the chart applies via helm. statefulsets/watch is here (not just for the
  # workload) because the sandbox-manager Role grants it and the operator must hold every verb it
  # grants (RBAC escalation guard).
  - apiGroups: [apps]
    resources: [deployments, statefulsets]
    verbs: [get, list, watch, create, update, patch, delete]
  - apiGroups: [batch]
    resources: [jobs]
    verbs: [get, list, create, update, patch, delete]
  - apiGroups: [autoscaling]
    resources: [horizontalpodautoscalers]
    verbs: [get, list, create, update, patch, delete]
  - apiGroups: [networking.k8s.io]
    resources: [ingresses, networkpolicies]
    verbs: [get, list, create, update, patch, delete]
  # The sandbox-manager Role (charts/ufo-tenant/templates/rbac.yaml) grants the pod carrier pods,
  # pods/exec, and PVCs; K8s escalation-prevention rejects Role creation unless the operator already
  # holds every verb the Role grants.
  - apiGroups: [""]
    resources: [pods]
    verbs: [get, list]
  - apiGroups: [""]
    resources: [pods/exec]
    verbs: [create]
  - apiGroups: [""]
    resources: [persistentvolumeclaims]
    verbs: [create, get, delete]
  # The operator creates the sandbox-manager Role + RoleBinding in each tenant namespace.
  - apiGroups: [rbac.authorization.k8s.io]
    resources: [roles, rolebindings]
    verbs: [get, list, create, update, patch, delete]
  - apiGroups: [coordination.k8s.io]
    resources: [leases]
    verbs: [get, list, create, update, patch]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: ufo-operator
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: ufo-operator
subjects:
  - kind: ServiceAccount
    name: ufo-operator
    namespace: ${namespace}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ufo-api
  namespace: ${namespace}
  labels: {app: ufo-api}
spec:
  replicas: 2
  selector:
    matchLabels: {app: ufo-api}
  template:
    metadata:
      labels: {app: ufo-api}
    spec:
      serviceAccountName: ufo-operator
      containers:
        - name: api
          image: ${registry}/ufo-control:${image_tag}
          args: [api]
          ports:
            - {name: http, containerPort: 8080}
          readinessProbe:
            httpGet: {path: /healthz, port: http}
          livenessProbe:
            httpGet: {path: /healthz, port: http}
---
apiVersion: v1
kind: Service
metadata:
  name: ufo-api
  namespace: ${namespace}
spec:
  selector: {app: ufo-api}
  ports:
    - {name: http, port: 80, targetPort: http}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ufo-operator
  namespace: ${namespace}
  labels: {app: ufo-operator}
spec:
  # One replica + Recreate is the mutual-exclusion guarantee; the Lease coordinates the rollout gap.
  replicas: 1
  strategy: {type: Recreate}
  selector:
    matchLabels: {app: ufo-operator}
  template:
    metadata:
      labels: {app: ufo-operator}
    spec:
      serviceAccountName: ufo-operator
      containers:
        - name: operator
          image: ${registry}/ufo-control:${image_tag}
          args: [operator]
          ports:
            - {name: http, containerPort: 8080}
          env:
            - name: POD_NAME
              valueFrom:
                fieldRef: {fieldPath: metadata.name}
            - name: UFO_CONTROL_CONFIG
              value: /config/platform.toml
            - name: UFO_CONTROL_POSTGRES_ADMIN_DSN
              valueFrom:
                secretKeyRef: {name: ufo-control-secrets, key: postgres-admin-dsn}
            - name: UFO_CONTROL_PG_ROLE_SEED
              valueFrom:
                secretKeyRef: {name: ufo-control-secrets, key: pg-role-seed}
          readinessProbe:
            httpGet: {path: /healthz, port: http}
          livenessProbe:
            httpGet: {path: /healthz, port: http}
          volumeMounts:
            - {name: config, mountPath: /config}
      volumes:
        - name: config
          configMap: {name: ufo-control-platform}

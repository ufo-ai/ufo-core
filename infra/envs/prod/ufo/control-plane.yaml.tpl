# The ufo control plane — the operator ServiceAccount + a broad ClusterRole (it manages tenant
# namespaces and every resource the tenant chart applies via helm), the deploy API, and the
# leader-elected operator. Templated from control/deploy/control-plane.yaml, adapted to the prod
# image (ECR ufo-control by tag) and the renamed group/namespace. The ufo-system Namespace, the
# platform ConfigMap, and the ufo-control-secrets / ufo-platform-secrets Secrets are applied by
# terraform (ufo.tf); this file assumes they exist.
#
# Run-once-per-rollout schema migration + RLS bootstrap, as the RLS-bypassing owner. Named by the
# image tag so a new bundle applies a FRESH Job (Jobs are immutable) and terraform destroys the
# prior tag's Job — so every rollout brings the shared schema to head before serve reads it, instead
# of drifting. `ufoctl migrate` (bundle image) reads UFO_OWNER_DSN + the baked [pack]; then
# `ufo-control rls-bootstrap` (control image) (re)creates the workspace RLS policies — both as the
# owner from ufo-control-secrets.
apiVersion: batch/v1
kind: Job
metadata:
  name: ufo-migrate-${image_tag}
  namespace: ${namespace}
  labels: {app: ufo-migrate}
spec:
  backoffLimit: 4
  template:
    metadata:
      labels: {app: ufo-migrate}
    spec:
      restartPolicy: OnFailure
      initContainers:
        - name: migrate
          image: ${bundle_image}
          args: [migrate]
          env:
            - name: UFO_OWNER_DSN
              valueFrom:
                secretKeyRef: {name: ufo-control-secrets, key: postgres-admin-dsn}
      containers:
        - name: rls-bootstrap
          image: ${registry}/ufo-control:${image_tag}
          args: [rls-bootstrap]
          env:
            - {name: UFO_CONTROL_OTLP_ENDPOINT, value: "${otlp_endpoint}"}
            - name: UFO_CONTROL_POSTGRES_OWNER_DSN
              valueFrom:
                secretKeyRef: {name: ufo-control-secrets, key: postgres-admin-dsn}
---
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
          env:
            - {name: UFO_CONTROL_OTLP_ENDPOINT, value: "${otlp_endpoint}"}
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
            - {name: UFO_CONTROL_OTLP_ENDPOINT, value: "${otlp_endpoint}"}
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
---
# The shared egress proxy — one standalone service fronting every tenant's sandbox egress. It runs
# from the ufo bundle image (`ufoctl proxy`), opens the RLS-bypassing owner DSN and scopes every
# rule query by the run token's own workspace_id, signs sandbox leaves from a stable platform CA
# (UFO_EGRESS_CA_*), and injects only the platform model-provider key. An off-cluster sandbox (e2b)
# dials it through the internet-facing NLB below; the old per-tenant proxy LoadBalancer is gone.
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ufo-sandbox-proxy
  namespace: ${namespace}
  labels: {app: ufo-sandbox-proxy}
spec:
  replicas: 2
  selector:
    matchLabels: {app: ufo-sandbox-proxy}
  template:
    metadata:
      labels: {app: ufo-sandbox-proxy}
    spec:
      enableServiceLinks: false
      containers:
        - name: proxy
          image: ${bundle_image}
          # ENTRYPOINT ["ufoctl"] is baked in; `proxy` reads the bundle's own baked /app/ufo.toml
          # (the config serve starts from) and takes its owner DSN, CA, and model keys from env below.
          args: [proxy]
          ports:
            - {name: proxy, containerPort: 8888}
          env:
            - {name: UFO_OTLP_ENDPOINT, value: "${otlp_endpoint}"}
            # The RLS-bypassing owner DSN (password-bearing → a Secret, never a ConfigMap).
            - name: UFO_OWNER_DSN
              valueFrom:
                secretKeyRef: {name: ufo-control-secrets, key: postgres-admin-dsn}
            # The stable platform CA the proxy signs every per-host sandbox leaf from.
            - name: UFO_EGRESS_CA_CERT
              valueFrom:
                secretKeyRef: {name: ufo-egress-ca, key: UFO_EGRESS_CA_CERT}
            - name: UFO_EGRESS_CA_KEY
              valueFrom:
                secretKeyRef: {name: ufo-egress-ca, key: UFO_EGRESS_CA_KEY}
            # The platform model-provider keys the proxy swaps onto the wire for sandbox egress.
            - name: ANTHROPIC_API_KEY
              valueFrom:
                secretKeyRef: {name: ufo-platform-secrets, key: ANTHROPIC_API_KEY}
            - name: OPENAI_API_KEY
              valueFrom:
                secretKeyRef: {name: ufo-platform-secrets, key: OPENAI_API_KEY}
          # No /healthz on the raw CONNECT proxy; a TCP probe confirms the bind after fail-loud boot.
          readinessProbe:
            tcpSocket: {port: proxy}
            initialDelaySeconds: 10
            periodSeconds: 10
          livenessProbe:
            tcpSocket: {port: proxy}
            initialDelaySeconds: 30
            periodSeconds: 20
---
# Internet-facing NLB the off-cluster sandbox (e2b) dials. DNS-only (grey-cloud): a raw TCP CONNECT
# proxy Cloudflare's HTTP proxy cannot front, and e2b connects from its own IPs, so it is NOT
# source-restricted — reachability is bounded by the proxy's run-token authorization (every CONNECT
# carries a token that must resolve to a running turn; an unauthorized or keyed-host CONNECT fails).
apiVersion: v1
kind: Service
metadata:
  name: ufo-sandbox-proxy
  namespace: ${namespace}
  labels: {app: ufo-sandbox-proxy}
  annotations:
    external-dns.alpha.kubernetes.io/hostname: sandbox-proxy.${base_domain}
    external-dns.alpha.kubernetes.io/cloudflare-proxied: "false"
    service.beta.kubernetes.io/aws-load-balancer-type: external
    service.beta.kubernetes.io/aws-load-balancer-nlb-target-type: ip
    service.beta.kubernetes.io/aws-load-balancer-scheme: internet-facing
spec:
  type: LoadBalancer
  selector: {app: ufo-sandbox-proxy}
  ports:
    - {name: proxy, port: 8888, targetPort: proxy}
---
# The shared serve fleet (hosted tier) — ONE Deployment serving turns for every workspace. Runs the
# bundle image (`ufoctl serve`) reading its baked /app/ufo.toml + platform env, connects as the
# RLS-SUBJECT role ufo_serve (serve-dsn), and scopes each request/turn to its workspace per
# transaction (the app.workspace_id GUC). Additive: the per-tenant tenant serve (enterprise tier) is
# unchanged. replicas:0 — it cannot serve until the sibling wiring lands (serve reads UFO_SERVE_DSN,
# resolves current_workspace per request, and a production [sandbox]e2b+proxy_public_url / [blob]s3
# config replaces the baked dev config). Flip to 2 + add the shared host/ingress once wired.
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ufo-serve
  namespace: ${namespace}
  labels: {app: ufo-serve}
spec:
  replicas: 0
  selector:
    matchLabels: {app: ufo-serve}
  template:
    metadata:
      labels: {app: ufo-serve}
    spec:
      enableServiceLinks: false
      containers:
        - name: serve
          image: ${bundle_image}
          # ENTRYPOINT ["ufoctl"] is baked in; `serve` runs the shared fleet.
          args: [serve]
          ports:
            - {name: http, containerPort: 8710}
          # Platform service keys as the tenant serve gets them: ANTHROPIC_API_KEY, OPENAI_API_KEY,
          # EXA_API, TURBOPUFFER_API_KEY, E2B_API_KEY, UFO_E2B_TEMPLATE, UFO_TOKEN_SECRET, and — once
          # provisioned there (option-a platform Fernet key) — UFO_CREDENTIAL_KEY / UFO_ARTIFACT_TOKEN_SECRET.
          envFrom:
            - secretRef: {name: ufo-platform-secrets}
          env:
            # The shared RLS-SUBJECT role DSN (never the RLS-bypassing owner). serve must read this
            # to override the baked config's database.url; every transaction sets app.workspace_id.
            - name: UFO_SERVE_DSN
              valueFrom:
                secretKeyRef: {name: ufo-control-secrets, key: serve-dsn}
          readinessProbe:
            tcpSocket: {port: http}
            initialDelaySeconds: 10
            periodSeconds: 10
          livenessProbe:
            tcpSocket: {port: http}
            initialDelaySeconds: 30
            periodSeconds: 20
---
apiVersion: v1
kind: Service
metadata:
  name: ufo-serve
  namespace: ${namespace}
  labels: {app: ufo-serve}
spec:
  selector: {app: ufo-serve}
  ports:
    - {name: http, port: 80, targetPort: http}

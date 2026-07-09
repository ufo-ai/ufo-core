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
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ufo-gateway
  namespace: ${namespace}
  labels: {app: ufo-gateway}
spec:
  replicas: 2
  selector:
    matchLabels: {app: ufo-gateway}
  template:
    metadata:
      labels: {app: ufo-gateway}
    spec:
      serviceAccountName: ufo-operator
      enableServiceLinks: false
      containers:
        - name: gateway
          image: ${registry}/ufo-control:${image_tag}
          args: [gateway]
          ports:
            - {name: http, containerPort: 8080}
          env:
            - {name: UFO_CONTROL_OTLP_ENDPOINT, value: "${otlp_endpoint}"}
            - {name: UFO_PUBLIC_BASE_URL, value: "https://${apex_host}"}
            # The workspace serve host the member's `ufo` surface talks to (has the `/surface` route),
            # distinct from the onboarding apex above — signed in, the member's turns go here.
            - {name: UFO_WORKSPACE_BASE_URL, value: "https://${shared_host}"}
            - {name: UFO_BASE_DOMAIN, value: "${base_domain}"}
            - {name: UFO_BUNDLE_IMAGE, value: "${bundle_image}"}
            - {name: UFO_GATEWAY_EMAIL_BACKEND, value: "logging"}
            - name: UFO_CONTROL_POSTGRES_OWNER_DSN
              valueFrom:
                secretKeyRef: {name: ufo-control-secrets, key: postgres-admin-dsn}
            - name: UFO_TOKEN_SECRET
              valueFrom:
                secretKeyRef: {name: ufo-platform-secrets, key: UFO_TOKEN_SECRET}
            # Shared-tier onboarding writes the workspace + owner rows as the RLS-subject serve role
            # (password-bearing → a Secret); the same DSN the shared serve fleet dials.
            - name: UFO_CONTROL_SERVE_DSN
              valueFrom:
                secretKeyRef: {name: ufo-serve, key: UFO_CONTROL_SERVE_DSN}
          readinessProbe:
            httpGet: {path: /healthz, port: http}
          livenessProbe:
            httpGet: {path: /healthz, port: http}
---
apiVersion: v1
kind: Service
metadata:
  name: ufo-gateway
  namespace: ${namespace}
spec:
  selector: {app: ufo-gateway}
  ports:
    - {name: http, port: 80, targetPort: http}
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ufo-gateway
  namespace: ${namespace}
  annotations:
    cert-manager.io/cluster-issuer: ${cluster_issuer}
    external-dns.alpha.kubernetes.io/hostname: ${apex_host}
    external-dns.alpha.kubernetes.io/cloudflare-proxied: "true"
spec:
  ingressClassName: ${ingress_class}
  tls:
    - hosts: [${apex_host}]
      secretName: ufo-gateway-tls
  rules:
    - host: ${apex_host}
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: ufo-gateway
                port: {name: http}
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
# bundle image (`ufoctl serve`) over the ufo-serve Secret's ufo.toml (mounted over the image's baked
# dev config): [serve] shared_workspace=true, the RLS-SUBJECT ufo_serve DSN, and the hosted
# assistant_hosted backends (s3 blob, e2b sandbox behind the shared proxy, redis hub, turbopuffer +
# exa). It connects as ufo_serve and scopes each request/turn to its workspace per transaction (the
# app.workspace_id GUC). Additive: the per-tenant tenant serve (enterprise tier) is unchanged.
apiVersion: v1
kind: ServiceAccount
metadata:
  name: ufo-serve
  namespace: ${namespace}
  annotations:
    # IRSA: the pod's boto3 assumes this role for blob-bucket access and the sandbox-fs mount mint —
    # the role a tenant serve SA carries too (the app_s3 trust admits ufo-serve in any ufo-* namespace).
    eks.amazonaws.com/role-arn: ${serve_role_arn}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ufo-serve
  namespace: ${namespace}
  labels: {app: ufo-serve}
spec:
  replicas: 2
  selector:
    matchLabels: {app: ufo-serve}
  template:
    metadata:
      labels: {app: ufo-serve}
    spec:
      serviceAccountName: ufo-serve
      enableServiceLinks: false
      containers:
        - name: serve
          image: ${bundle_image}
          # ENTRYPOINT ["ufoctl"] is baked in; `serve` runs the shared fleet.
          args: [serve]
          ports:
            - {name: http, containerPort: 8710}
          # Model/provider keys the fleet shares across workspaces (ANTHROPIC/OPENAI/OPENROUTER, EXA,
          # TURBOPUFFER, E2B + UFO_E2B_TEMPLATE, COMPOSIO, UFO_TOKEN_SECRET) plus the shared egress
          # proxy's CA (UFO_EGRESS_CA_CERT) the sandbox trusts.
          envFrom:
            - secretRef: {name: ufo-platform-secrets}
          env:
            # The fleet's platform Fernet key (seals hosted credential rows), session-signing secret,
            # and artifact-delivery secret — minted for the fleet, in the ufo-serve Secret.
            - name: UFO_CREDENTIAL_KEY
              valueFrom:
                secretKeyRef: {name: ufo-serve, key: UFO_CREDENTIAL_KEY}
            - name: UFO_SESSION_SECRET
              valueFrom:
                secretKeyRef: {name: ufo-serve, key: UFO_SESSION_SECRET}
            - name: UFO_ARTIFACT_TOKEN_SECRET
              valueFrom:
                secretKeyRef: {name: ufo-serve, key: UFO_ARTIFACT_TOKEN_SECRET}
            # The RLS-bypassing owner DSN owner_tx enumerates every workspace through for the
            # fleet-wide job sweeps — the same secret the migrate Job + shared proxy open. Without it
            # owner_tx falls back to the RLS-subject engine and the enumeration reads an unset
            # app.workspace_id GUC. Password-bearing → a Secret, never the ConfigMap.
            - name: UFO_OWNER_DSN
              valueFrom:
                secretKeyRef: {name: ufo-control-secrets, key: postgres-admin-dsn}
          volumeMounts:
            # The rendered shared-fleet config replaces the image's baked dev ufo.toml.
            - {name: config, mountPath: /app/ufo.toml, subPath: ufo.toml}
          # No /healthz in core; a TCP probe confirms uvicorn is bound after fail-loud boot.
          readinessProbe:
            tcpSocket: {port: http}
            initialDelaySeconds: 10
            periodSeconds: 10
          livenessProbe:
            tcpSocket: {port: http}
            initialDelaySeconds: 30
            periodSeconds: 20
      volumes:
        - name: config
          secret:
            secretName: ufo-serve
            items:
              - {key: ufo.toml, path: ufo.toml}
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
---
# The one member-facing host for every hosted workspace (no per-workspace subdomain — RFC 0011),
# published alongside the onboarding gateway at the apex. cert-manager issues TLS; ExternalDNS
# publishes the record Cloudflare-proxied, so the shared NLB (Cloudflare-only) is reachable only
# through the proxy. `[connect] public_base_url = https://${shared_host}` matches this host.
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ufo-serve
  namespace: ${namespace}
  annotations:
    cert-manager.io/cluster-issuer: ${cluster_issuer}
    external-dns.alpha.kubernetes.io/hostname: ${shared_host}
    external-dns.alpha.kubernetes.io/cloudflare-proxied: "true"
spec:
  ingressClassName: ${ingress_class}
  tls:
    - hosts: [${shared_host}]
      secretName: ufo-serve-tls
  rules:
    - host: ${shared_host}
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: ufo-serve
                port: {name: http}

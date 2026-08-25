# The shared hosted service: schema bootstrap, onboarding gateway, sandbox proxy, and serve fleet.
apiVersion: batch/v1
kind: Job
metadata:
  name: ufo-migrate-${image_tag}
  namespace: ${namespace}
  labels: {app: ufo-migrate}
spec:
  backoffLimit: 0
  activeDeadlineSeconds: 540
  template:
    metadata:
      labels: {app: ufo-migrate}
    spec:
      restartPolicy: Never
      initContainers:
        - name: migrate
          image: ${bundle_image}
          args: [migrate]
          env:
            - name: UFO_OWNER_DSN
              valueFrom:
                secretKeyRef: {name: ufo-control-secrets, key: postgres-admin-dsn}
        # The gateway ledgers live in the `ufo_control` schema, outside core's revision graph, and
        # are shaped here so no replica ever issues DDL: initContainers run to completion in order,
        # so this lands before rls-bootstrap and the whole Job before any gateway pod starts.
        - name: control-schema
          image: ${registry}/ufo-control:${image_tag}
          args: [migrate]
          env:
            - name: UFO_CONTROL_POSTGRES_OWNER_DSN
              valueFrom:
                secretKeyRef: {name: ufo-control-secrets, key: postgres-admin-dsn}
      containers:
        - name: rls-bootstrap
          image: ${registry}/ufo-control:${image_tag}
          args: [rls-bootstrap]
          env:
            - name: UFO_CONTROL_POSTGRES_OWNER_DSN
              valueFrom:
                secretKeyRef: {name: ufo-control-secrets, key: postgres-admin-dsn}
            - name: UFO_CONTROL_PG_ROLE_SEED
              valueFrom:
                secretKeyRef: {name: ufo-control-secrets, key: pg-role-seed}
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: ufo-gateway
  namespace: ${namespace}
  annotations:
    # IRSA: the gateway exchanges this pod's projected web identity for ses:SendEmail credentials
    # (the pod identity webhook injects AWS_ROLE_ARN / AWS_WEB_IDENTITY_TOKEN_FILE from this).
    eks.amazonaws.com/role-arn: ${gateway_ses_role_arn}
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
      annotations: {flyingobject.ai/deployment-id: "${deployment_id}"}
    spec:
%{ if workload_ha }
      affinity:
        podAntiAffinity:
          preferredDuringSchedulingIgnoredDuringExecution:
            - weight: 100
              podAffinityTerm:
                labelSelector:
                  matchLabels: {app: ufo-gateway}
                topologyKey: kubernetes.io/hostname
      topologySpreadConstraints:
        - labelSelector:
            matchLabels: {app: ufo-gateway}
          maxSkew: 1
          matchLabelKeys: [pod-template-hash]
          nodeTaintsPolicy: Honor
          topologyKey: topology.kubernetes.io/zone
          whenUnsatisfiable: DoNotSchedule
%{ endif }
      serviceAccountName: ufo-gateway
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
            # The terminal client version this deploy serves — a stale x-ufo-script gets `install`.
            - {name: UFO_CLIENT_VERSION, value: "${client_version}"}
            - {name: UFO_SES_SENDER, value: "${ses_sender}"}
            - {name: UFO_SES_REGION, value: "${ses_region}"}
            # The gateway's own role: granted the `ufo_control` schema and no privilege on any
            # table in `public`, so this pod cannot read a tenant's rows even by mistake. The owner
            # DSN reaches the migrate and rls-bootstrap Jobs above, never here.
            - name: UFO_CONTROL_GATEWAY_DSN
              valueFrom:
                secretKeyRef: {name: ufo-control-secrets, key: control-dsn}
            - name: UFO_TOKEN_SECRET
              valueFrom:
                secretKeyRef: {name: ufo-platform-secrets, key: UFO_TOKEN_SECRET}
            # Every read and write of a core table goes to serve over the onboarding RPC.
            - {name: UFO_CONTROL_SERVE_INTERNAL_URL, value: "http://ufo-serve:8710"}
            - name: UFO_ONBOARD_CONTROL_TOKEN
              valueFrom:
                secretKeyRef: {name: ufo-platform-secrets, key: UFO_ONBOARD_CONTROL_TOKEN}
            # WorkOS verifies the member's email — Magic Auth for the email step both surfaces
            # collect, a Google OAuth hop for the browser's Continue-with-Google. The gateway
            # refuses to start without all three. The redirect URI is the app
            # host's own callback, where the ingress routes /v1/onboard to this pod, and the same
            # string is registered in this deploy's WorkOS environment.
            - {name: WORKOS_REDIRECT_URI, value: "https://${shared_host}/v1/onboard/auth/callback"}
            - name: WORKOS_API_KEY
              valueFrom:
                secretKeyRef: {name: ufo-gateway-workos, key: WORKOS_API_KEY}
            - name: WORKOS_CLIENT_ID
              valueFrom:
                secretKeyRef: {name: ufo-gateway-workos, key: WORKOS_CLIENT_ID}
            # The signup Slack Connect inviter: UFO's own operator-workspace app, reached only from
            # this pod. Enabled, the gateway refuses to start without both the token and the team it
            # must belong to.
            - {name: UFO_CONTROL_SLACK_CONNECT_ENABLED, value: "${slack_connect_enabled}"}
            - {name: UFO_CONTROL_SLACK_CONNECT_TEAM_ID, value: "${slack_connect_team_id}"}
            - name: UFO_CONTROL_SLACK_CONNECT_BOT_TOKEN
              valueFrom:
                secretKeyRef: {name: ufo-gateway-slack-connect, key: bot-token}
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
%{ if workload_ha }
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: ufo-gateway
  namespace: ${namespace}
spec:
  minAvailable: 1
  unhealthyPodEvictionPolicy: AlwaysAllow
  selector:
    matchLabels: {app: ufo-gateway}
%{ endif }
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ufo-gateway
  namespace: ${namespace}
  annotations:
    external-dns.alpha.kubernetes.io/hostname: ${apex_host},${gateway_origin_host}
    external-dns.alpha.kubernetes.io/cloudflare-proxied: "true"
spec:
  ingressClassName: ${ingress_class}
  tls:
    - hosts: [${apex_host}]
      secretName: ufo-gateway-tls
    - hosts: [${gateway_origin_host}]
      secretName: ufo-ingress-tls
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
    - host: ${gateway_origin_host}
      http:
        paths:
          - path: /v1/onboard
            pathType: Prefix
            backend:
              service:
                name: ufo-gateway
                port: {name: http}
          - path: /ufo
            pathType: Exact
            backend:
              service:
                name: ufo-gateway
                port: {name: http}
          - path: /ufo/bin
            pathType: Prefix
            backend:
              service:
                name: ufo-gateway
                port: {name: http}
          - path: /fleet
            pathType: Exact
            backend:
              service:
                name: ufo-gateway
                port: {name: http}
---
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: ufo-gateway-tls
  namespace: ${namespace}
spec:
  secretName: ufo-gateway-tls
  issuerRef: {name: ${cluster_issuer}, kind: ClusterIssuer}
  dnsNames: [${apex_host}]
---
# The shared egress proxy meters every workspace sandbox through one service. It runs the standalone
# ufo-egress binary (RFC 0035) — a thin data plane holding no keys and no database: it verifies the
# run token locally to scope caps, then calls serve's internal egress-control RPC
# (UFO_EGRESS_CONTROL_URL, bearer UFO_EGRESS_CONTROL_TOKEN) for every resolve/authorize/forward/meter
# decision, and signs sandbox leaves from a stable platform CA (UFO_EGRESS_CA_*). An off-cluster
# sandbox (e2b) dials it through the internet-facing TLS service managed by the environment.
apiVersion: v1
kind: ServiceAccount
metadata:
  name: ufo-sandbox-proxy
  namespace: ${namespace}
%{ if cache_enabled }
  # The cache sidecar assumes this IRSA role to reach its durable S3 tier; the proxy itself needs no
  # AWS, so the grant rides only when the cache is on.
  annotations:
    eks.amazonaws.com/role-arn: ${cache_s3_role_arn}
%{ endif }
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ufo-sandbox-proxy
  namespace: ${namespace}
  labels: {app: ufo-sandbox-proxy}
spec:
  replicas: 2
  strategy:
    rollingUpdate:
      maxSurge: 100%
      maxUnavailable: 0
  selector:
    matchLabels: {app: ufo-sandbox-proxy}
  template:
    metadata:
      labels: {app: ufo-sandbox-proxy}
      annotations: {flyingobject.ai/deployment-id: "${deployment_id}"}
    spec:
%{ if workload_ha }
      affinity:
        podAntiAffinity:
          preferredDuringSchedulingIgnoredDuringExecution:
            - weight: 100
              podAffinityTerm:
                labelSelector:
                  matchLabels: {app: ufo-sandbox-proxy}
                topologyKey: kubernetes.io/hostname
      topologySpreadConstraints:
        - labelSelector:
            matchLabels: {app: ufo-sandbox-proxy}
          maxSkew: 1
          matchLabelKeys: [pod-template-hash]
          nodeTaintsPolicy: Honor
          topologyKey: topology.kubernetes.io/zone
          whenUnsatisfiable: DoNotSchedule
%{ endif }
      terminationGracePeriodSeconds: ${termination_grace_period_seconds}
      serviceAccountName: ufo-sandbox-proxy
      enableServiceLinks: false
      containers:
        - name: proxy
          image: ${registry}/ufo-egress:${image_tag}
          # The ufo-egress binary is the image entrypoint and reads its whole configuration from env.
          # Endpoint/NLB-target deregistration propagates for a beat after the pod turns
          # Terminating; keep the listener accepting until it lands, then SIGTERM starts the drain.
          lifecycle:
            preStop:
              exec:
                command: [sleep, "${prestop_seconds}"]
          ports:
            - {name: proxy, containerPort: 8888}
          env:
            # Drain live tunnels on SIGTERM for as long as serve drains its own turns, so a rollout
            # never cuts sandbox egress mid-stream; bounded above by terminationGracePeriodSeconds.
            - {name: UFO_EGRESS_GRACEFUL_SHUTDOWN_SECONDS, value: "${graceful_shutdown_seconds}"}
            - {name: UFO_EGRESS_PORT, value: "8888"}
            # serve's internal egress-control RPC: the data plane resolves, authorizes, forwards, and
            # meters through it, so it holds no keys and no database of its own.
            - {name: UFO_EGRESS_CONTROL_URL, value: "http://ufo-serve.${namespace}.svc.cluster.local:8710"}
            # Verifies the run token locally to scope caps before it calls the control RPC.
            - name: UFO_TOKEN_SECRET
              valueFrom:
                secretKeyRef: {name: ufo-platform-secrets, key: UFO_TOKEN_SECRET}
            # The bearer serve's egress-control RPC requires; serve reads the same key via envFrom.
            - name: UFO_EGRESS_CONTROL_TOKEN
              valueFrom:
                secretKeyRef: {name: ufo-platform-secrets, key: UFO_EGRESS_CONTROL_TOKEN}
            # The stable platform CA the proxy signs every per-host sandbox leaf from.
            - name: UFO_EGRESS_CA_CERT
              valueFrom:
                secretKeyRef: {name: ufo-egress-ca, key: UFO_EGRESS_CA_CERT}
            - name: UFO_EGRESS_CA_KEY
              valueFrom:
                secretKeyRef: {name: ufo-egress-ca, key: UFO_EGRESS_CA_KEY}
%{ if cache_enabled }
            # The local cache daemon a Service rule relays to (RFC 0032); the daemon binds loopback.
            - {name: UFO_EGRESS_CACHE_DAEMON, value: "127.0.0.1:9110"}
%{ endif }
%{ if preview_enabled }
            # The preview service a Service rule relays to (RFC 0037): its own Deployment, reached by
            # cluster DNS rather than loopback, so the proxy resolves the address per connect.
            - {name: UFO_EGRESS_PREVIEW_DAEMON, value: "ufo-preview.${namespace}.svc.cluster.local:8930"}
%{ endif }
          resources:
            requests: {cpu: 250m, memory: 384Mi}
            limits: {cpu: "2", memory: 768Mi}
          # No /healthz on the raw CONNECT proxy; a TCP probe confirms the bind.
          readinessProbe:
            tcpSocket: {port: proxy}
            initialDelaySeconds: 10
            periodSeconds: 10
          livenessProbe:
            tcpSocket: {port: proxy}
            initialDelaySeconds: 30
            periodSeconds: 20
%{ if cache_enabled }
        # The sandbox cache daemon: git mirrors + npm/PyPI caching for internet-holding sandboxes,
        # sharing loopback with the proxy. For a git upstream it needs authentication for, it phones
        # serve's `/internal/git-credential` route, which resolves the workspace's own credential;
        # that route carries its own cache token, so the cache never reaches the egress secrets tier.
        # Bound to loopback, so its health probe execs against 127.0.0.1 rather than the pod IP.
        - name: cache
          image: ${registry}/ufo-cache:${image_tag}
          env:
            - {name: UFO_CACHE_LISTEN, value: "127.0.0.1:9110"}
            - {name: UFO_CACHE_STATE, value: /var/cache/ufo}
            - {name: UFO_CACHE_CONTROL_URL, value: "http://ufo-serve.${namespace}.svc.cluster.local:8710"}
            - name: UFO_CACHE_CONTROL_TOKEN
              valueFrom:
                secretKeyRef: {name: ufo-platform-secrets, key: UFO_CACHE_CONTROL_TOKEN}
%{ if cache_s3_bucket != "" }
            - {name: UFO_CACHE_S3_BUCKET, value: "${cache_s3_bucket}"}
            - {name: AWS_REGION, value: "${region}"}
%{ endif }
          resources:
            requests: {cpu: 250m, memory: 512Mi}
            limits: {cpu: "2", memory: 3Gi}
          volumeMounts:
            - {name: cache, mountPath: /var/cache/ufo}
          # Liveness only — never readiness. The cache is an optimization co-located with the proxy;
          # gating pod readiness on it would drop a healthy egress proxy (and all sandbox egress)
          # out of the Service on a cache blip. A dead cache is restarted; the proxy keeps serving,
          # falling through to origin.
          livenessProbe:
            exec: {command: [curl, -sf, "http://127.0.0.1:9110/_health"]}
            initialDelaySeconds: 15
            periodSeconds: 20
%{ endif }
%{ if cache_enabled }
      volumes:
        # The cache's hot tier: node-local scratch, wiped on pod roll. Durability is the S3 tier the
        # daemon restores from on a cold start, not this volume. Sized above the daemon's ceilings —
        # UFO_CACHE_DISK_LIMIT_BYTES (default 4 GiB of git mirrors), UFO_CACHE_PKG_DISK_LIMIT_BYTES
        # (default 8 GiB of package artifacts), UFO_CACHE_PACK_CACHE_MB (default 4 GiB of upload-pack
        # replays), and UFO_CACHE_LFS_CACHE_MB (default 4 GiB of LFS objects) — to leave headroom for
        # in-progress clones and downloads the sweep counts but cannot evict.
        - name: cache
          emptyDir: {sizeLimit: 24Gi}
%{ endif }
%{ if workload_ha }
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: ufo-sandbox-proxy
  namespace: ${namespace}
spec:
  minAvailable: 1
  unhealthyPodEvictionPolicy: AlwaysAllow
  selector:
    matchLabels: {app: ufo-sandbox-proxy}
%{ endif }
%{ if preview_enabled }
---
# The preview service (RFC 0037): renders shared files and document reads. Share-nothing — no
# database, AWS credentials, or state. The sandbox reaches it through the egress proxy. tini is the
# image entrypoint, reaping the soffice children a timed-out render leaves behind.
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ufo-preview
  namespace: ${namespace}
  labels: {app: ufo-preview}
spec:
  replicas: 2
  strategy:
    rollingUpdate:
      maxSurge: 100%
      maxUnavailable: 0
  selector:
    matchLabels: {app: ufo-preview}
  template:
    metadata:
      labels: {app: ufo-preview}
      annotations: {flyingobject.ai/deployment-id: "${deployment_id}"}
    spec:
      enableServiceLinks: false
      containers:
        - name: preview
          image: ${registry}/ufo-preview:${image_tag}
          ports:
            - {name: http, containerPort: 8930}
          env:
            - {name: UFO_PREVIEW_LISTEN, value: "0.0.0.0:8930"}
            - {name: UFO_PREVIEW_SITE_HOST, value: "${site_host}"}
            # Gates byte-returning sinks and site capture; file `put_url` carries its own capability.
            - name: UFO_PREVIEW_TOKEN
              valueFrom:
                secretKeyRef: {name: ufo-platform-secrets, key: UFO_PREVIEW_TOKEN}
          resources:
            requests: {cpu: 250m, memory: 512Mi}
            limits: {cpu: "2", memory: 2Gi}
          readinessProbe:
            httpGet: {path: /_health, port: http}
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet: {path: /_health, port: http}
            initialDelaySeconds: 15
            periodSeconds: 20
---
apiVersion: v1
kind: Service
metadata:
  name: ufo-preview
  namespace: ${namespace}
  labels: {app: ufo-preview}
spec:
  selector: {app: ufo-preview}
  ports:
    - {name: http, port: 8930, targetPort: http}
%{ endif }
---
# The sandbox ingress is the inbound twin of the egress proxy: a generic token-gated reverse proxy
# from a public hostname to a conversation's live sandbox port. It runs from the ufo bundle image
# (`ufoctl ingress`), opens the RLS-bypassing owner DSN to resolve `conversation.sandbox_handle`,
# and dials the sandbox through the carrier seam. High-traffic site bytes land here, off the serve
# event loop.
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ufo-ingress
  namespace: ${namespace}
  labels: {app: ufo-ingress}
spec:
  replicas: 2
  strategy:
    rollingUpdate:
      maxSurge: 100%
      maxUnavailable: 0
  selector:
    matchLabels: {app: ufo-ingress}
  template:
    metadata:
      labels: {app: ufo-ingress}
      annotations: {flyingobject.ai/deployment-id: "${deployment_id}"}
    spec:
%{ if workload_ha }
      affinity:
        podAntiAffinity:
          preferredDuringSchedulingIgnoredDuringExecution:
            - weight: 100
              podAffinityTerm:
                labelSelector:
                  matchLabels: {app: ufo-ingress}
                topologyKey: kubernetes.io/hostname
      topologySpreadConstraints:
        - labelSelector:
            matchLabels: {app: ufo-ingress}
          maxSkew: 1
          matchLabelKeys: [pod-template-hash]
          nodeTaintsPolicy: Honor
          topologyKey: topology.kubernetes.io/zone
          whenUnsatisfiable: DoNotSchedule
%{ endif }
      terminationGracePeriodSeconds: ${termination_grace_period_seconds}
      enableServiceLinks: false
      serviceAccountName: ufo-ingress
      containers:
        - name: ingress
          image: ${bundle_image}
          # ENTRYPOINT ["ufoctl"] is baked in; `ingress` reads the shared fleet config mounted below.
          args: [ingress]
          # Endpoint/NLB-target deregistration propagates for a beat after the pod turns
          # Terminating; keep the listener accepting until it lands, then SIGTERM starts the drain.
          lifecycle:
            preStop:
              exec:
                command: [sleep, "${prestop_seconds}"]
          ports:
            - {name: ingress, containerPort: 8100}
          env:
            - {name: UFO_OTLP_ENDPOINT, value: "${otlp_endpoint}"}
            # The RLS-bypassing owner DSN (password-bearing → a Secret, never a ConfigMap).
            - name: UFO_OWNER_DSN
              valueFrom:
                secretKeyRef: {name: ufo-control-secrets, key: postgres-admin-dsn}
            - name: UFO_TOKEN_SECRET
              valueFrom:
                secretKeyRef: {name: ufo-platform-secrets, key: UFO_TOKEN_SECRET}
            - name: E2B_API_KEY
              valueFrom:
                secretKeyRef: {name: ufo-platform-secrets, key: E2B_API_KEY}
            - {name: E2B_TEMPLATES, value: "${e2b_templates}"}
          resources:
            requests: {cpu: 250m, memory: 384Mi}
            limits: {cpu: "2", memory: 768Mi}
          volumeMounts:
            - {name: config, mountPath: /app/ufo.toml, subPath: ufo.toml}
          # No /healthz on the raw reverse proxy; a TCP probe confirms the bind.
          readinessProbe:
            tcpSocket: {port: ingress}
            initialDelaySeconds: 10
            periodSeconds: 10
          livenessProbe:
            tcpSocket: {port: ingress}
            initialDelaySeconds: 30
            periodSeconds: 20
      volumes:
        - name: config
          secret:
            secretName: ufo-serve
            items:
              - {key: ufo.toml, path: ufo.toml}
%{ if workload_ha }
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: ufo-ingress
  namespace: ${namespace}
spec:
  minAvailable: 1
  unhealthyPodEvictionPolicy: AlwaysAllow
  selector:
    matchLabels: {app: ufo-ingress}
%{ endif }
---
apiVersion: v1
kind: Service
metadata:
  name: ufo-ingress
  namespace: ${namespace}
  labels: {app: ufo-ingress}
spec:
  selector: {app: ufo-ingress}
  ports:
    - {name: ingress, port: 8100, targetPort: ingress}
---
# Every hosted site answers at its own subdomain of ${apex_host}, so one wildcard record and one
# wildcard certificate cover all of them and each site is its own browser origin — which is what
# keeps one site's cookies and storage away from the next. A site is one label deep, not two, and
# that depth is load-bearing: a wildcard SAN matches exactly one label, so the zone's edge
# certificate (`*.${apex_host}`, measured) covers `<label>.${apex_host}` and covers nothing under a
# `sites.` prefix. Proxying is not optional here — the NLB admits only Cloudflare's ranges
# (`loadBalancerSourceRanges`), so a DNS-only site address resolves and then drops every connection.
# The wildcard rule is the last resort for this apex: nginx matches an exact server name ahead of a
# wildcard, and any unclaimed name lands here and is refused for naming no site. The certificate
# needs no proxied record —
# cert-manager issues it over DNS-01.
# Every proxied response carries `Cache-Control: private, no-store` from the ingress, since a shared
# cache that stored a site's bytes would answer later requests without the cookie check.
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ufo-ingress
  namespace: ${namespace}
  annotations:
    external-dns.alpha.kubernetes.io/hostname: "*.${apex_host}"
    external-dns.alpha.kubernetes.io/cloudflare-proxied: "true"
spec:
  ingressClassName: ${ingress_class}
  tls:
    - hosts: ["*.${apex_host}"]
      secretName: ufo-ingress-tls
  rules:
    - host: "*.${apex_host}"
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: ufo-ingress
                port: {name: ingress}
---
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: ufo-ingress-tls
  namespace: ${namespace}
spec:
  secretName: ufo-ingress-tls
  issuerRef: {name: ${cluster_issuer}, kind: ClusterIssuer}
  dnsNames: ["*.${apex_host}"]
---
# The shared serve fleet is one Deployment serving turns for every workspace. It runs the
# bundle image (`ufoctl serve`) over the ufo-serve Secret's ufo.toml (mounted over the image's baked
# dev config): the RLS-SUBJECT ufo_serve DSN and the hosted assistant_hosted backends (s3 blob, e2b
# sandbox behind the shared proxy, redis hub and terminal transport, Turbopuffer + Perplexity). It connects
# as ufo_serve and
# scopes each request/turn to its workspace per transaction (the app.workspace_id GUC).
apiVersion: v1
kind: ServiceAccount
metadata:
  name: ufo-serve
  namespace: ${namespace}
  annotations:
    eks.amazonaws.com/role-arn: ${serve_role_arn}
---
# The ingress reads the same blob bucket to answer a stored site, so the namespace holds two blob
# identities: serve's is read-write and promotes a deploy into the store, this one only reads.
apiVersion: v1
kind: ServiceAccount
metadata:
  name: ufo-ingress
  namespace: ${namespace}
  annotations:
    eks.amazonaws.com/role-arn: ${ingress_role_arn}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ufo-serve
  namespace: ${namespace}
  labels: {app: ufo-serve}
spec:
  replicas: 2
  strategy:
    rollingUpdate:
      maxSurge: 100%
      maxUnavailable: 0
  selector:
    matchLabels: {app: ufo-serve}
  template:
    metadata:
      labels: {app: ufo-serve}
      annotations: {flyingobject.ai/deployment-id: "${deployment_id}"}
    spec:
%{ if workload_ha }
      affinity:
        podAntiAffinity:
          preferredDuringSchedulingIgnoredDuringExecution:
            - weight: 100
              podAffinityTerm:
                labelSelector:
                  matchLabels: {app: ufo-serve}
                topologyKey: kubernetes.io/hostname
      topologySpreadConstraints:
        - labelSelector:
            matchLabels: {app: ufo-serve}
          maxSkew: 1
          matchLabelKeys: [pod-template-hash]
          nodeTaintsPolicy: Honor
          topologyKey: topology.kubernetes.io/zone
          whenUnsatisfiable: DoNotSchedule
%{ endif }
      terminationGracePeriodSeconds: ${termination_grace_period_seconds}
      serviceAccountName: ufo-serve
      enableServiceLinks: false
      containers:
        - name: serve
          image: ${bundle_image}
          # ENTRYPOINT ["ufoctl"] is baked in; `serve` runs the shared fleet.
          args: [serve]
          # Endpoint/NLB-target deregistration propagates for a beat after the pod turns
          # Terminating; keep the listener accepting until it lands, then SIGTERM starts the drain.
          lifecycle:
            preStop:
              exec:
                command: [sleep, "${prestop_seconds}"]
          ports:
            - {name: http, containerPort: 8710}
          # Model/provider keys the fleet shares across workspaces (ANTHROPIC/OPENAI/OPENROUTER, PERPLEXITY,
          # TURBOPUFFER, E2B, BROWSERBASE, COMPOSIO, PIPEDREAM, UFO_TOKEN_SECRET), the billing settings the
          # metronome extension reads host-side (METRONOME_BEARER_TOKEN,
          # STRIPE_SECRET_KEY/STRIPE_BILLING_PORTAL_CONFIGURATION_ID), this deploy's one Slack app's
          # secrets (SLACK_CLIENT_ID/SLACK_CLIENT_SECRET/SLACK_SIGNING_SECRET, read in-process for the
          # OAuth install and event verification) plus the shared egress proxy's CA
          # (UFO_EGRESS_CA_CERT) the sandbox trusts.
          envFrom:
            - secretRef: {name: ufo-platform-secrets}
          env:
            - {name: AWS_REGION, value: "${region}"}
            - {name: E2B_TEMPLATES, value: "${e2b_templates}"}
            # The terminal client version this deploy serves — the ufo surface tells a stale
            # x-ufo-script to install.
            - {name: UFO_CLIENT_VERSION, value: "${client_version}"}
%{ if rum_recording }
            # The portal's session recording (Datadog RUM). A recording is made in the member's
            # browser, so the page has to name the application it records into — and one image
            # serves every deploy, so it reads that name here rather than from its own bundle. A
            # deploy that records nothing sets `rum_recording` false and the page carries none.
            #
            # The application and its token are read from the Secret rather than written into this
            # manifest: Datadog mints both, so a template carrying them is unknown until apply, and
            # the manifest keys this set is applied under would be unknown with it. The client token
            # authorizes writes into that one application and reads nothing, which is why the page
            # may carry it.
            - name: UFO_WEB_RUM_APPLICATION_ID
              valueFrom:
                secretKeyRef: {name: ufo-serve, key: UFO_WEB_RUM_APPLICATION_ID}
            - name: UFO_WEB_RUM_CLIENT_TOKEN
              valueFrom:
                secretKeyRef: {name: ufo-serve, key: UFO_WEB_RUM_CLIENT_TOKEN}
            - {name: UFO_WEB_RUM_SITE, value: "${rum_site}"}
            - {name: UFO_WEB_RUM_ENV, value: "${rum_env}"}
            - {name: UFO_WEB_RUM_VERSION, value: "${image_tag}"}
%{ endif }
%{ if preview_enabled }
            # The preview service the render-previews job calls directly (RFC 0037): serve reaches its
            # ClusterIP, not the proxy-relayed host the sandbox uses. Set only when preview is enabled,
            # so the job registers only where the service runs.
            - {name: UFO_PREVIEW_URL, value: "http://ufo-preview.${namespace}.svc.cluster.local:8930"}
%{ endif }
            # The fleet's platform Fernet key (seals hosted credential rows) and artifact-delivery
            # secret — minted for the fleet, in the ufo-serve Secret.
            - name: UFO_CREDENTIAL_KEY
              valueFrom:
                secretKeyRef: {name: ufo-serve, key: UFO_CREDENTIAL_KEY}
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
            # The Datadog key the source-sync job submits the `ufo.source_sync` service check with.
            # OTLP defines no service check, so the collector this pod exports every metric and log
            # through cannot carry one — the submission goes to Datadog's own intake, and it reads the
            # same Secret the collector does rather than a second copy of the key.
            - name: DD_API_KEY
              valueFrom:
                secretKeyRef: {name: datadog-api-key, key: DD_API_KEY}
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
%{ if workload_ha }
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: ufo-serve
  namespace: ${namespace}
spec:
  minAvailable: 1
  unhealthyPodEvictionPolicy: AlwaysAllow
  selector:
    matchLabels: {app: ufo-serve}
%{ endif }
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
    # `http` (80) is the member-facing port the shared_host ingress routes to; `internal` (8710) is
    # the same app on its own port, reached only in-cluster — the ufo-egress data plane dials serve's
    # egress-control RPC here (UFO_EGRESS_CONTROL_URL), off the ingress path.
    - {name: http, port: 80, targetPort: http}
    - {name: internal, port: 8710, targetPort: http}
---
# The one authenticated host for every hosted workspace (no per-workspace subdomain — RFC 0011):
# the whole browser sign-in flow is same-origin here, so the host-only `ufo_session` cookie is set
# and read on this one host. The gateway's browser-facing login endpoints (`/login`, `/logout`, the
# web onboarding wire, the `/ufo` install script) are routed here to `ufo-gateway`, while `/` and
# every `/surface/*` product route stay on `ufo-serve`; nginx's longest-prefix match makes the split
# unambiguous. cert-manager issues TLS; ExternalDNS publishes the record Cloudflare-proxied, so the
# shared NLB (Cloudflare-only) is reachable only through the proxy. `[connect] public_base_url =
# https://${shared_host}` matches this host.
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ufo-serve
  namespace: ${namespace}
  annotations:
    external-dns.alpha.kubernetes.io/hostname: ${shared_host}
    external-dns.alpha.kubernetes.io/cloudflare-proxied: "true"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "90"
spec:
  ingressClassName: ${ingress_class}
  tls:
    - hosts: [${shared_host}]
      secretName: ufo-serve-tls
  rules:
    - host: ${shared_host}
      http:
        paths:
          - path: /login
            pathType: Prefix
            backend:
              service:
                name: ufo-gateway
                port: {name: http}
          - path: /logout
            pathType: Prefix
            backend:
              service:
                name: ufo-gateway
                port: {name: http}
          - path: /v1/onboard
            pathType: Prefix
            backend:
              service:
                name: ufo-gateway
                port: {name: http}
          - path: /ufo
            pathType: Prefix
            backend:
              service:
                name: ufo-gateway
                port: {name: http}
          - path: /
            pathType: Prefix
            backend:
              service:
                name: ufo-serve
                port: {name: http}
---
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: ufo-serve-tls
  namespace: ${namespace}
spec:
  secretName: ufo-serve-tls
  issuerRef: {name: ${cluster_issuer}, kind: ClusterIssuer}
  dnsNames: [${shared_host}]

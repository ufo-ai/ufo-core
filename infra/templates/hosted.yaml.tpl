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
    spec:
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
            - {name: UFO_SES_SENDER, value: "${ses_sender}"}
            - {name: UFO_SES_REGION, value: "${ses_region}"}
            - name: UFO_CONTROL_POSTGRES_OWNER_DSN
              valueFrom:
                secretKeyRef: {name: ufo-control-secrets, key: postgres-admin-dsn}
            - name: UFO_TOKEN_SECRET
              valueFrom:
                secretKeyRef: {name: ufo-platform-secrets, key: UFO_TOKEN_SECRET}
            # Onboarding writes workspace rows through the RLS-subject serve role.
            - name: UFO_CONTROL_SERVE_DSN
              valueFrom:
                secretKeyRef: {name: ufo-serve, key: UFO_CONTROL_SERVE_DSN}
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
# The shared egress proxy meters every workspace sandbox through one service. It runs
# from the ufo bundle image (`ufoctl proxy`), opens the RLS-bypassing owner DSN and scopes every
# rule query by the run token's own workspace_id, signs sandbox leaves from a stable platform CA
# (UFO_EGRESS_CA_*), and injects only the platform model-provider key. An off-cluster sandbox (e2b)
# dials it through the internet-facing TLS service managed by the environment.
apiVersion: v1
kind: ServiceAccount
metadata:
  name: ufo-sandbox-proxy
  namespace: ${namespace}
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
    spec:
      terminationGracePeriodSeconds: ${termination_grace_period_seconds}
      serviceAccountName: ufo-sandbox-proxy
      enableServiceLinks: false
      containers:
        - name: proxy
          image: ${bundle_image}
          # ENTRYPOINT ["ufoctl"] is baked in; `proxy` reads the shared fleet config mounted below.
          args: [proxy]
          # Endpoint/NLB-target deregistration propagates for a beat after the pod turns
          # Terminating; keep the listener accepting until it lands, then SIGTERM starts the drain.
          lifecycle:
            preStop:
              exec:
                command: [sleep, "${prestop_seconds}"]
          ports:
            - {name: proxy, containerPort: 8888}
          env:
            - {name: UFO_OTLP_ENDPOINT, value: "${otlp_endpoint}"}
            # The RLS-bypassing owner DSN (password-bearing → a Secret, never a ConfigMap).
            - name: UFO_OWNER_DSN
              valueFrom:
                secretKeyRef: {name: ufo-control-secrets, key: postgres-admin-dsn}
            - name: UFO_TOKEN_SECRET
              valueFrom:
                secretKeyRef: {name: ufo-platform-secrets, key: UFO_TOKEN_SECRET}
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
            # The broker key the proxy forwards sentinel CLI requests with (Composio proxy-execute).
            - name: COMPOSIO_API_KEY
              valueFrom:
                secretKeyRef: {name: ufo-platform-secrets, key: COMPOSIO_API_KEY}
            # The GitHub App source mints each workspace's installation token in this process.
            # The coding manifest treats the registration as all-or-none, so the proxy receives
            # the same four values serve uses even though minting consumes only the id and PEM.
            - name: GITHUB_APP_ID
              valueFrom:
                secretKeyRef: {name: ufo-platform-secrets, key: GITHUB_APP_ID}
            - name: GITHUB_APP_CLIENT_ID
              valueFrom:
                secretKeyRef: {name: ufo-platform-secrets, key: GITHUB_APP_CLIENT_ID}
            - name: GITHUB_APP_CLIENT_SECRET
              valueFrom:
                secretKeyRef: {name: ufo-platform-secrets, key: GITHUB_APP_CLIENT_SECRET}
            - name: GITHUB_APP_PRIVATE_KEY
              valueFrom:
                secretKeyRef: {name: ufo-platform-secrets, key: GITHUB_APP_PRIVATE_KEY}
            # The one Fernet the fleet stores workspace credentials under: the proxy decrypts each
            # workspace's keyed-provider secrets per turn to swap them onto the wire. A pack with a
            # keyed provider fails loud without it, so proxy and serve read the same secret.
            - name: UFO_CREDENTIAL_KEY
              valueFrom:
                secretKeyRef: {name: ufo-serve, key: UFO_CREDENTIAL_KEY}
          resources:
            requests: {cpu: 250m, memory: 384Mi}
            limits: {cpu: "2", memory: 768Mi}
          volumeMounts:
            - {name: config, mountPath: /app/ufo.toml, subPath: ufo.toml}
          # No /healthz on the raw CONNECT proxy; a TCP probe confirms the bind after fail-loud boot.
          readinessProbe:
            tcpSocket: {port: proxy}
            initialDelaySeconds: 10
            periodSeconds: 10
          livenessProbe:
            tcpSocket: {port: proxy}
            initialDelaySeconds: 30
            periodSeconds: 20
      volumes:
        - name: config
          secret:
            secretName: ufo-serve
            items:
              - {key: ufo.toml, path: ufo.toml}
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
    spec:
      terminationGracePeriodSeconds: ${termination_grace_period_seconds}
      enableServiceLinks: false
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
            - {name: E2B_TEMPLATE, value: "${e2b_template}"}
          resources:
            requests: {cpu: 250m, memory: 384Mi}
            limits: {cpu: "2", memory: 768Mi}
          volumeMounts:
            - {name: config, mountPath: /app/ufo.toml, subPath: ufo.toml}
          # No /healthz on the raw reverse proxy; a TCP probe confirms the bind after fail-loud boot.
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
# wildcard, so ${shared_host} and the apex itself keep their own rules, and any other unclaimed name
# lands here and is refused for naming no site. Our own certificate needs no proxied record —
# cert-manager issues it over DNS-01.
# Every proxied response carries `Cache-Control: private, no-store` from the ingress, since a shared
# cache that stored a site's bytes would answer later requests without the cookie check.
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ufo-ingress
  namespace: ${namespace}
  annotations:
    cert-manager.io/cluster-issuer: ${cluster_issuer}
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
# The shared serve fleet is one Deployment serving turns for every workspace. It runs the
# bundle image (`ufoctl serve`) over the ufo-serve Secret's ufo.toml (mounted over the image's baked
# dev config): the RLS-SUBJECT ufo_serve DSN and the hosted assistant_hosted backends (s3 blob, e2b
# sandbox behind the shared proxy, redis hub, turbopuffer + exa). It connects as ufo_serve and
# scopes each request/turn to its workspace per transaction (the app.workspace_id GUC).
apiVersion: v1
kind: ServiceAccount
metadata:
  name: ufo-serve
  namespace: ${namespace}
  annotations:
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
  strategy:
    rollingUpdate:
      maxSurge: 100%
      maxUnavailable: 0
  selector:
    matchLabels: {app: ufo-serve}
  template:
    metadata:
      labels: {app: ufo-serve}
    spec:
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
          # Model/provider keys the fleet shares across workspaces (ANTHROPIC/OPENAI/OPENROUTER, EXA,
          # TURBOPUFFER, E2B, BROWSERBASE, COMPOSIO, PIPEDREAM, UFO_TOKEN_SECRET), the billing settings the
          # metronome extension reads host-side (METRONOME_BEARER_TOKEN/METRONOME_PACKAGE_ALIAS,
          # STRIPE_SECRET_KEY/STRIPE_BILLING_PORTAL_CONFIGURATION_ID), this deploy's one Slack app's
          # secrets (SLACK_CLIENT_ID/SLACK_CLIENT_SECRET/SLACK_SIGNING_SECRET, read in-process for the
          # OAuth install and event verification) plus the shared egress proxy's CA
          # (UFO_EGRESS_CA_CERT) the sandbox trusts.
          envFrom:
            - secretRef: {name: ufo-platform-secrets}
          env:
            - {name: AWS_REGION, value: "${region}"}
            - {name: E2B_TEMPLATE, value: "${e2b_template}"}
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
# The one authenticated host for every hosted workspace (no per-workspace subdomain — RFC 0011):
# the whole browser sign-in flow is same-origin here, so the host-only `ufo_session` cookie is set
# and read on this one host. The gateway's browser-facing login endpoints (`/login`, the web
# onboarding wire, the `/ufo` install script) are routed here to `ufo-gateway`, while `/` and every
# `/surface/*` product route stay on `ufo-serve`; nginx's longest-prefix match makes the split
# unambiguous. cert-manager issues TLS; ExternalDNS publishes the record Cloudflare-proxied, so the
# shared NLB (Cloudflare-only) is reachable only through the proxy. `[connect] public_base_url =
# https://${shared_host}` matches this host.
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
          - path: /login
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

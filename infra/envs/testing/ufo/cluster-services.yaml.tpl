# cert-manager ClusterIssuer (DNS-01 via Cloudflare — proxying breaks HTTP-01), the External Secrets
# ClusterSecretStore, and the two ExternalSecrets the control plane + tenant pods consume:
#   ufo-control-secrets  — Postgres admin DSN + role seed; read only by the operator (NOT the
#                          platform secret, so it is never replicated into tenant namespaces).
#   ufo-platform-secrets — model/provider keys + UFO_TOKEN_SECRET the tenant chart pushes to every
#                          pod via envFrom (each key IS the env var name); replicated by the operator.
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt
spec:
  acme:
    server: ${acme_server}
    email: ${acme_email}
    privateKeySecretRef:
      name: letsencrypt-account-key
    solvers:
      - dns01:
          cloudflare:
            # The token Secret cert-manager reads lives in its own namespace (addons.tf).
            apiTokenSecretRef:
              name: cloudflare-api-token
              key: cloudflare_api_token
        selector:
          dnsZones: [${dns_zone}]
---
apiVersion: external-secrets.io/v1beta1
kind: ClusterSecretStore
metadata:
  name: ufo-aws-sm
spec:
  provider:
    aws:
      service: SecretsManager
      region: ${region}
      auth:
        jwt:
          serviceAccountRef:
            name: external-secrets
            namespace: external-secrets
---
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: ufo-control-secrets
  namespace: ${namespace}
spec:
  refreshInterval: 1h
  secretStoreRef: {name: ufo-aws-sm, kind: ClusterSecretStore}
  target: {name: ufo-control-secrets}
  data:
    - {secretKey: postgres-admin-dsn, remoteRef: {key: ${secret_postgres}, property: postgres-admin-dsn}}
    - {secretKey: pg-role-seed, remoteRef: {key: ${secret_postgres}, property: pg-role-seed}}
---
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: ufo-platform-secrets
  namespace: ${namespace}
spec:
  refreshInterval: 1h
  secretStoreRef: {name: ufo-aws-sm, kind: ClusterSecretStore}
  target: {name: ufo-platform-secrets}
  # secretKey == the env var the tenant pod receives (deployment.yaml envFrom). Model keys arrive
  # empty from api-keys until set out-of-band; the ExternalSecret still syncs (they exist as keys).
  data:
    - {secretKey: ANTHROPIC_API_KEY, remoteRef: {key: ${secret_api_keys}, property: anthropic-api-key}}
    - {secretKey: OPENAI_API_KEY, remoteRef: {key: ${secret_api_keys}, property: openai-api-key}}
    - {secretKey: OPENROUTER_API_KEY, remoteRef: {key: ${secret_api_keys}, property: openrouter-api-key}}
    - {secretKey: COMPOSIO_API_KEY, remoteRef: {key: ${secret_api_keys}, property: composio-api-key}}
    - {secretKey: E2B_API_KEY, remoteRef: {key: ${secret_api_keys}, property: e2b-api-key}}
    - {secretKey: UFO_E2B_API_KEY, remoteRef: {key: ${secret_api_keys}, property: e2b-api-key}}
    - {secretKey: TURBOPUFFER_API_KEY, remoteRef: {key: ${secret_api_keys}, property: turbopuffer-api-key}}
    - {secretKey: EXA_API, remoteRef: {key: ${secret_api_keys}, property: exa-api-key}}
    - {secretKey: UFO_TOKEN_SECRET, remoteRef: {key: ${secret_platform}, property: ufo-token-secret}}
    - {secretKey: UFO_E2B_TEMPLATE, remoteRef: {key: ${secret_platform}, property: e2b-sandbox-template}}
    - {secretKey: SES_SENDER, remoteRef: {key: ${secret_platform}, property: ses-sender}}

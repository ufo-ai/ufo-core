# Certificates and the Secrets Manager projections used by the hosted processes.
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
  # Model keys arrive empty until populated out-of-band.
  data:
    - {secretKey: ANTHROPIC_API_KEY, remoteRef: {key: ${secret_api_keys}, property: anthropic-api-key}}
    - {secretKey: OPENAI_API_KEY, remoteRef: {key: ${secret_api_keys}, property: openai-api-key}}
    - {secretKey: OPENROUTER_API_KEY, remoteRef: {key: ${secret_api_keys}, property: openrouter-api-key}}
    - {secretKey: COMPOSIO_API_KEY, remoteRef: {key: ${secret_api_keys}, property: composio-api-key}}
    - {secretKey: PIPEDREAM_CLIENT_ID, remoteRef: {key: ${secret_api_keys}, property: pipedream-client-id}}
    - {secretKey: PIPEDREAM_CLIENT_SECRET, remoteRef: {key: ${secret_api_keys}, property: pipedream-client-secret}}
    - {secretKey: PIPEDREAM_PROJECT_ID, remoteRef: {key: ${secret_api_keys}, property: pipedream-project-id}}
    - {secretKey: PIPEDREAM_GMAIL_OAUTH_APP_ID, remoteRef: {key: ${secret_api_keys}, property: pipedream-gmail-oauth-app-id}}
    - {secretKey: E2B_API_KEY, remoteRef: {key: ${secret_api_keys}, property: e2b-api-key}}
    - {secretKey: TURBOPUFFER_API_KEY, remoteRef: {key: ${secret_api_keys}, property: turbopuffer-api-key}}
    - {secretKey: EXA_API, remoteRef: {key: ${secret_api_keys}, property: exa-api-key}}
    # Serve trusts the proxy certificate; only the proxy receives the key.
    - {secretKey: UFO_EGRESS_CA_CERT, remoteRef: {key: ${secret_platform}, property: egress-ca-cert}}
    - {secretKey: UFO_TOKEN_SECRET, remoteRef: {key: ${secret_platform}, property: ufo-token-secret}}
---
# The shared egress proxy's stable CA certificate and key.
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: ufo-egress-ca
  namespace: ${namespace}
spec:
  refreshInterval: 1h
  secretStoreRef: {name: ufo-aws-sm, kind: ClusterSecretStore}
  target: {name: ufo-egress-ca}
  data:
    - {secretKey: UFO_EGRESS_CA_CERT, remoteRef: {key: ${secret_platform}, property: egress-ca-cert}}
    - {secretKey: UFO_EGRESS_CA_KEY, remoteRef: {key: ${secret_platform}, property: egress-ca-key}}

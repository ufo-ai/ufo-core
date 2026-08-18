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
    # The gateway's own role. The keys here are enumerated, so a Deployment reading a key this
    # list omits stops at CreateContainerConfigError rather than failing anything earlier.
    - {secretKey: control-dsn, remoteRef: {key: ${secret_postgres}, property: control-dsn}}
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
  data:
    - {secretKey: ANTHROPIC_API_KEY, remoteRef: {key: ${secret_api_keys}, property: anthropic-api-key}}
    - {secretKey: AWS_BEARER_TOKEN_BEDROCK, remoteRef: {key: ${secret_api_keys}, property: bedrock-api-key}}
    - {secretKey: OPENAI_API_KEY, remoteRef: {key: ${secret_api_keys}, property: openai-api-key}}
    - {secretKey: OPENROUTER_API_KEY, remoteRef: {key: ${secret_api_keys}, property: openrouter-api-key}}
    - {secretKey: COMPOSIO_API_KEY, remoteRef: {key: ${secret_api_keys}, property: composio-api-key}}
    - {secretKey: PIPEDREAM_CLIENT_ID, remoteRef: {key: ${secret_api_keys}, property: pipedream-client-id}}
    - {secretKey: PIPEDREAM_CLIENT_SECRET, remoteRef: {key: ${secret_api_keys}, property: pipedream-client-secret}}
    - {secretKey: PIPEDREAM_PROJECT_ID, remoteRef: {key: ${secret_api_keys}, property: pipedream-project-id}}
    - {secretKey: PIPEDREAM_GMAIL_OAUTH_APP_ID, remoteRef: {key: ${secret_api_keys}, property: pipedream-gmail-oauth-app-id}}
    - {secretKey: E2B_API_KEY, remoteRef: {key: ${secret_api_keys}, property: e2b-api-key}}
    # The hosted browser every browser subagent run drives over CDP; read in-process by serve to
    # mint and release that run's session, never injected at the proxy.
    - {secretKey: BROWSERBASE_API_KEY, remoteRef: {key: ${secret_api_keys}, property: browserbase-api-key}}
    - {secretKey: TURBOPUFFER_API_KEY, remoteRef: {key: ${secret_api_keys}, property: turbopuffer-api-key}}
    - {secretKey: PERPLEXITY_API_KEY, remoteRef: {key: ${secret_api_keys}, property: perplexity-api-key}}
    - {secretKey: METRONOME_BEARER_TOKEN, remoteRef: {key: ${secret_api_keys}, property: metronome-bearer-token}}
    # Billing: the Stripe key mints the customer and the portal sessions, and the portal
    # configuration is what keeps subscription mutation out of the member's hands.
    - {secretKey: STRIPE_SECRET_KEY, remoteRef: {key: ${secret_api_keys}, property: stripe-secret-key}}
    - {secretKey: STRIPE_BILLING_PORTAL_CONFIGURATION_ID, remoteRef: {key: ${secret_api_keys}, property: stripe-billing-portal-configuration-id}}
    # This deploy's one Slack app: client id/secret run the OAuth install exchange, the signing
    # secret verifies every inbound event — all read in-process by serve, never injected at the proxy.
    - {secretKey: SLACK_CLIENT_ID, remoteRef: {key: ${secret_api_keys}, property: slack-client-id}}
    - {secretKey: SLACK_CLIENT_SECRET, remoteRef: {key: ${secret_api_keys}, property: slack-client-secret}}
    - {secretKey: SLACK_SIGNING_SECRET, remoteRef: {key: ${secret_api_keys}, property: slack-signing-secret}}
    - {secretKey: SPECTRUM_PROJECT_ID, remoteRef: {key: ${secret_api_keys}, property: spectrum-project-id}}
    - {secretKey: SPECTRUM_PROJECT_SECRET, remoteRef: {key: ${secret_api_keys}, property: spectrum-project-secret}}
    # This deploy's GitHub App: id and client id name it, the client secret completes the install
    # OAuth exchange, and the PEM signs the JWT that mints an installation token per turn. All four
    # or none — serve fails loud on a half-set registration. The PEM is the value, not a path, so it
    # rides the same secret projection as every other credential and needs no mounted file.
    - {secretKey: GITHUB_APP_ID, remoteRef: {key: ${secret_api_keys}, property: github-app-id}}
    - {secretKey: GITHUB_APP_CLIENT_ID, remoteRef: {key: ${secret_api_keys}, property: github-app-client-id}}
    - {secretKey: GITHUB_APP_CLIENT_SECRET, remoteRef: {key: ${secret_api_keys}, property: github-app-client-secret}}
    - {secretKey: GITHUB_APP_PRIVATE_KEY, remoteRef: {key: ${secret_api_keys}, property: github-app-private-key}}
    # Serve trusts the proxy certificate; only the proxy receives the key.
    - {secretKey: UFO_EGRESS_CA_CERT, remoteRef: {key: ${secret_platform}, property: egress-ca-cert}}
    - {secretKey: UFO_TOKEN_SECRET, remoteRef: {key: ${secret_platform}, property: ufo-token-secret}}
    # The bearer the standalone ufo-egress data plane presents to serve's egress-control RPC. serve
    # reads it whole through envFrom to gate the RPC; the proxy pod references the same key.
    - {secretKey: UFO_EGRESS_CONTROL_TOKEN, remoteRef: {key: ${secret_platform}, property: egress-control-token}}
    - {secretKey: UFO_ONBOARD_CONTROL_TOKEN, remoteRef: {key: ${secret_platform}, property: onboard-control-token}}
    # The sandbox cache daemon's callback token (RFC 0032); unused until the cache is enabled.
    - {secretKey: UFO_CACHE_CONTROL_TOKEN, remoteRef: {key: ${secret_platform}, property: ufo-cache-control-token}}
---
# The signup Slack Connect bot token. Its own Secret, read by the ufo-gateway pod through an explicit
# secretKeyRef — never part of ufo-platform-secrets, so no envFrom can hand it to serve or a sandbox.
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: ufo-gateway-slack-connect
  namespace: ${namespace}
spec:
  refreshInterval: 1h
  secretStoreRef: {name: ufo-aws-sm, kind: ClusterSecretStore}
  target: {name: ufo-gateway-slack-connect}
  data:
    - {secretKey: bot-token, remoteRef: {key: ${secret_gateway_slack_connect}, property: bot-token}}
---
# The WorkOS credentials the gateway verifies a member's email with. Its own Secret, read by the
# ufo-gateway pod through an explicit secretKeyRef — serve mounts ufo-platform-secrets whole.
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: ufo-gateway-workos
  namespace: ${namespace}
spec:
  refreshInterval: 1h
  secretStoreRef: {name: ufo-aws-sm, kind: ClusterSecretStore}
  target: {name: ufo-gateway-workos}
  data:
    - {secretKey: WORKOS_API_KEY, remoteRef: {key: ${secret_gateway_workos}, property: api-key}}
    - {secretKey: WORKOS_CLIENT_ID, remoteRef: {key: ${secret_gateway_workos}, property: client-id}}
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

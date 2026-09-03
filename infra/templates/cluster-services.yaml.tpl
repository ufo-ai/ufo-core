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
          dnsZones: ${dns_zones}
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
    # The deploy's own GitHub OAuth client, registered with Pipedream: consent rides it so Pipedream
    # releases each connected account's token, which the egress proxy swaps in for the sandbox's
    # GH_TOKEN sentinel on clone, push, and gh.
    - {secretKey: PIPEDREAM_GITHUB_OAUTH_APP_ID, remoteRef: {key: ${secret_api_keys}, property: pipedream-github-oauth-app-id}}
    - {secretKey: E2B_API_KEY, remoteRef: {key: ${secret_api_keys}, property: e2b-api-key}}
    # The hosted browser every browser subagent run drives over CDP; read in-process by serve to
    # mint and release that run's session, never injected at the proxy.
    - {secretKey: BROWSERBASE_API_KEY, remoteRef: {key: ${secret_api_keys}, property: browserbase-api-key}}
    - {secretKey: TURBOPUFFER_API_KEY, remoteRef: {key: ${secret_api_keys}, property: turbopuffer-api-key}}
    - {secretKey: PERPLEXITY_API_KEY, remoteRef: {key: ${secret_api_keys}, property: perplexity-api-key}}
    # Sign-up enrichment reads this key in-process to look a member's company up. An unseeded key
    # builds no provider: the extension then registers neither the action nor the job, the first run
    # offers no website step, and the member states their business themselves.
    - {secretKey: PEOPLE_DATA_LABS_API_KEY, remoteRef: {key: ${secret_api_keys}, property: people-data-labs-api-key}}
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
    # The Flagship app this deploy reads feature flags from ([flags] backend): the app id and the
    # account name it, the token carries the Flagship Evaluate permission. All three or none — serve
    # builds no flag provider without them and every flag resolves to the default its call site
    # passes. Today's flags withhold shipped screens, so an unseeded key leaves the product whole
    # rather than dark: only a flag service answering false takes a screen away.
    - {secretKey: CLOUDFLARE_FLAGSHIP_APP_ID, remoteRef: {key: ${secret_api_keys}, property: cloudflare-flagship-app-id}}
    - {secretKey: CLOUDFLARE_ACCOUNT_ID, remoteRef: {key: ${secret_api_keys}, property: cloudflare-account-id}}
    - {secretKey: CLOUDFLARE_FLAGSHIP_TOKEN, remoteRef: {key: ${secret_api_keys}, property: cloudflare-flagship-token}}
    # The token `ufoctl flags set` writes a flag's served value with, scoped to this
    # environment's Flagship app alone. Serve never reads it: it is projected because the
    # operator surface here is a verb run in one of the fleet's own pods, the way
    # `ufoctl balance credit` is, so flipping a flag asks nobody to hold a credential.
    - {secretKey: CLOUDFLARE_FLAGSHIP_WRITE_TOKEN, remoteRef: {key: ${secret_api_keys}, property: cloudflare-flagship-write-token}}
    # Serve trusts the proxy certificate; only the proxy receives the key.
    - {secretKey: UFO_EGRESS_CA_CERT, remoteRef: {key: ${secret_platform}, property: egress-ca-cert}}
    - {secretKey: UFO_TOKEN_SECRET, remoteRef: {key: ${secret_platform}, property: ufo-token-secret}}
    # The bearer the standalone ufo-egress data plane presents to serve's egress-control RPC. serve
    # reads it whole through envFrom to gate the RPC; the proxy pod references the same key.
    - {secretKey: UFO_EGRESS_CONTROL_TOKEN, remoteRef: {key: ${secret_platform}, property: egress-control-token}}
    - {secretKey: UFO_ONBOARD_CONTROL_TOKEN, remoteRef: {key: ${secret_platform}, property: onboard-control-token}}
    # The sandbox cache daemon's callback token (RFC 0032); unused until the cache is enabled.
    - {secretKey: UFO_CACHE_CONTROL_TOKEN, remoteRef: {key: ${secret_platform}, property: ufo-cache-control-token}}
    # The preview service's bearer (RFC 0037); gates byte-returning sinks.
    - {secretKey: UFO_PREVIEW_TOKEN, remoteRef: {key: ${secret_platform}, property: ufo-preview-token}}
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

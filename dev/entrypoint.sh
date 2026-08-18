#!/usr/bin/env bash
# One entrypoint, three roles (the compose command picks): `init` brings the schema and roles to
# head once, `gateway` and `serve` are the long-running surfaces. The RLS-subject ufo_serve DSN is
# derived here from UFO_CONTROL_PG_ROLE_SEED (the config toml carries no env interpolation) and
# rendered into the serve config + exported for the gateway. The pack is rendered the same way:
# `assistant` is the local default, and UFO_DEV_PACK selects a wider bundle (assistant_billing
# adds Metronome so the billing chain can be driven locally).
set -euo pipefail

PG_HOST="${PG_HOST:-postgres:5432}"
APP_DB="${APP_DB:-ufo}"
DEV_PACK="${UFO_DEV_PACK:-assistant}"
PUBLIC_BASE_URL="${UFO_PUBLIC_BASE_URL:-http://localhost:8710}"
RENDERED_CONFIG="/tmp/ufo.toml"
# The one egress CA the local rig shares: serve hands the sandbox its cert (the trust anchor) and the
# ufo-egress container signs leaves with its key. Minted once into the shared volume; both read it.
EGRESS_CA_DIR="/egress-ca"
# A fixed dev bearer the ufo-egress container presents to serve's egress-control RPC. Local only.
DEV_EGRESS_CONTROL_TOKEN="ufo-local-dev-egress-token"

mint_egress_ca() {
  if [ -f "${EGRESS_CA_DIR}/ca.crt" ]; then
    return
  fi
  mkdir -p "${EGRESS_CA_DIR}"
  python - "${EGRESS_CA_DIR}" <<'PY'
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

out = Path(sys.argv[1])
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ufo-egress-local")])
cert = (
    x509.CertificateBuilder()
    .subject_name(name)
    .issuer_name(name)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(datetime.now(UTC))
    .not_valid_after(datetime.now(UTC) + timedelta(days=3650))
    .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
    .sign(key, hashes.SHA256())
)
(out / "ca.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
(out / "ca.key").write_bytes(
    key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
)
PY
}

render_config() {
  local dsn
  dsn="$(ufo-control serve-dsn "${PG_HOST}" "${APP_DB}")"
  sed -e "s#__SERVE_DSN__#${dsn}#g" -e "s#__PACK__#${DEV_PACK}#g" \
    -e "s#__PUBLIC_BASE_URL__#${PUBLIC_BASE_URL}#g" \
    /app/dev/ufo.toml > "$RENDERED_CONFIG"
  export UFO_CONFIG="$RENDERED_CONFIG"
}

case "${1:-}" in
  init)
    render_config
    echo "[dev] minting the shared local egress CA (serve trusts it; ufo-egress signs with it) …"
    mint_egress_ca
    echo "[dev] ensuring the ufo_owner role exists (RDS provisions it in prod) …"
    psql "$UFO_CONTROL_POSTGRES_OWNER_DSN" -v ON_ERROR_STOP=1 \
      -c "do \$\$ begin if not exists (select from pg_roles where rolname = 'ufo_owner') then create role ufo_owner; end if; end \$\$;"
    echo "[dev] migrating the schema to head …"
    UFO_OWNER_DSN="$UFO_CONTROL_POSTGRES_OWNER_DSN" ufoctl migrate
    echo "[dev] shaping the ufo_control gateway ledgers …"
    ufo-control migrate
    echo "[dev] bootstrapping the ufo_serve role, ufo_dbos database, and RLS policies …"
    ufo-control rls-bootstrap
    echo "[dev] init complete."
    ;;
  gateway)
    exec ufo-control gateway
    ;;
  serve)
    render_config
    # serve mounts the egress-control RPC and hands the sandbox the shared CA the ufo-egress
    # container signs with; the proxy runs as its own compose service in serve's network namespace.
    export UFO_EGRESS_CA_CERT="$(cat "${EGRESS_CA_DIR}/ca.crt")"
    export UFO_EGRESS_CONTROL_TOKEN="${UFO_EGRESS_CONTROL_TOKEN:-$DEV_EGRESS_CONTROL_TOKEN}"
    exec ufoctl serve
    ;;
  *)
    echo "usage: entrypoint.sh {init|gateway|serve}" >&2
    exit 2
    ;;
esac

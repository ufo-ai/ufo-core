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
RENDERED_CONFIG="/tmp/ufo.toml"

render_config() {
  local dsn
  dsn="$(python - <<PY
from ufo_control.rls import serve_dsn
print(serve_dsn("${PG_HOST}", "${APP_DB}"))
PY
)"
  sed -e "s#__SERVE_DSN__#${dsn}#g" -e "s#__PACK__#${DEV_PACK}#g" \
    /app/dev/ufo.toml > "$RENDERED_CONFIG"
  export UFO_CONFIG="$RENDERED_CONFIG"
  export UFO_CONTROL_SERVE_DSN="$dsn"
}

case "${1:-}" in
  init)
    render_config
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
    exec ufoctl serve
    ;;
  *)
    echo "usage: entrypoint.sh {init|gateway|serve}" >&2
    exit 2
    ;;
esac

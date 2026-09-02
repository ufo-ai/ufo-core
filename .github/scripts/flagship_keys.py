"""What each Flagship app holds right now, as JSON on stdout.

The doctrine sweep's `flag-keys` class reads this. Every other class it runs enumerates its
surfaces by grepping the repo; this one cannot, because the far end of a flag key is a record in
Cloudflare's flag service. So this prints the one fact grep cannot reach — the live key set per
app — and the sweep does the repo-side comparison itself against `infra/envs/edge/flags.tf` and
`infra/flag_tombstones.json`.

The account and the apps are read where terraform reads them: the account off the `ufo.ai` zone
(`data.cloudflare_zone.ufo_ai`), the app ids off the `flagship_apps` locals. Nothing is passed in
that the deploy does not already derive, so this cannot aim at an app the deploy never applies.

Each environment answers under its own token, and a token that fails is reported as a fault rather
than as an empty app: read a refusal as "this app holds nothing" and the sweep concludes every
declared flag is missing and every tombstone is finished.
"""

import json
import os
import pathlib
import re
import sys

import httpx
from ufo_ext_flagship import API_BASE_URL, FlagshipAdmin

FLAGS_TF = pathlib.Path("infra/envs/edge/flags.tf")
ZONE_NAME = "ufo.ai"
APP_TOKEN_ENV = {"testing": "FLAGSHIP_TESTING_API_TOKEN", "prod": "FLAGSHIP_PROD_API_TOKEN"}
APP_ID_PATTERN = re.compile(r"^\s*([a-z]+)\s*=\s*\"([0-9a-f-]{36})\"", re.MULTILINE)
CLOUDFLARE_TOKEN_ENV = "CLOUDFLARE_API_TOKEN"
REQUEST_TIMEOUT_SECONDS = 30.0


def flagship_apps(source: str) -> dict[str, str]:
    """The `flagship_apps` locals, environment name to app id."""
    block = re.search(r"flagship_apps\s*=\s*\{(.*?)\n  \}", source, re.DOTALL)
    if block is None:
        raise SystemExit(f"{FLAGS_TF} declares no flagship_apps map")
    return dict(APP_ID_PATTERN.findall(block.group(1)))


def account_id(token: str) -> str:
    """The account the `ufo.ai` zone belongs to — the same lookup terraform's zone data does, so
    this cannot drift onto another account by holding its own copy of the id."""
    answer = httpx.get(
        f"{API_BASE_URL}/zones",
        params={"name": ZONE_NAME},
        headers={"authorization": f"Bearer {token}"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    body = answer.json() if answer.content else {}
    zones = body.get("result") or []
    if answer.status_code >= 400 or not body.get("success", False) or not zones:
        raise SystemExit(f"cloudflare GET /zones?name={ZONE_NAME}: {answer.status_code} {body}")
    return str(zones[0]["account"]["id"])


def main() -> int:
    cloudflare_token = os.environ.get(CLOUDFLARE_TOKEN_ENV, "")
    if not cloudflare_token:
        print(f"{CLOUDFLARE_TOKEN_ENV} is required to resolve the account", file=sys.stderr)
        return 1
    account = account_id(cloudflare_token)
    apps: dict[str, dict[str, object]] = {}
    faults = []
    for environment, app in sorted(flagship_apps(FLAGS_TF.read_text()).items()):
        token = os.environ.get(APP_TOKEN_ENV.get(environment, ""), "")
        if not token:
            faults.append(f"{environment}: no token in {APP_TOKEN_ENV.get(environment)!r}")
            continue
        try:
            keys = FlagshipAdmin(account_id=account, app_id=app, token=token).list()
        except RuntimeError as error:
            faults.append(f"{environment}: {error}")
            continue
        apps[environment] = {"app_id": app, "keys": list(keys)}
    print(json.dumps({"account_id": account, "apps": apps}, indent=2))
    for fault in faults:
        print(fault, file=sys.stderr)
    return 1 if faults else 0


if __name__ == "__main__":
    raise SystemExit(main())

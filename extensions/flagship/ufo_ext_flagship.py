"""Cloudflare Flagship as the deploy's feature-flag backend, over the OpenFeature seam.

Flagship is OpenFeature-native, so the extension registers the vendor's own provider at the
`flag_providers` point and core reads every flag through `ufo.flags.flag_enabled` — nothing here is
called per flag. The Python SDK evaluates over HTTP (the Workers binding is TypeScript-only), so a
read is one request to the deploy's Flagship app, answered from the provider's response cache for
`[flags] cache_ttl_seconds`.

The deploy carries three environment keys: the app and account that name its Flagship app, and a
Cloudflare API token with the Flagship Evaluate permission. They are deploy keys rather than
workspace credentials because a flag decides what the product offers, not what one workspace's
account may reach: no member fills them, and `ufoctl init` reports each one missing.

Which flags exist is terraform's (`infra/envs/edge/flags.tf`), applied by the deploy. What one
serves is not: the value a workspace is answered lives in the flag service, so a member's screen
changes without a deploy. `FlagshipAdmin` is that write, reached by `ufoctl flags set` on a token
scoped to one Flagship app — never the token serve reads with, which may only evaluate.

A deploy missing any of the three gets no provider, so every flag resolves to its code default.
That is the same answer a failed request gives — `REQUEST_TIMEOUT_SECONDS` with no retry, well
inside the ceiling `flag_enabled` holds a caller to — so an unreachable Flagship costs a turn a
bounded wait and the closed state, never an error.
"""

from dataclasses import dataclass, field

import httpx
from flagship import FlagshipServerProvider
from openfeature.provider import FeatureProvider

from ufo.sdk.credentials import deploy_env
from ufo.sdk.manifest import FlagProviderSpec, Manifest
from ufo.sdk.o11y import warn

NAME = "flagship"
VERSION = "0.1.0"
FLAG_BACKEND = "flagship"
APP_ID_ENV = "CLOUDFLARE_FLAGSHIP_APP_ID"
ACCOUNT_ID_ENV = "CLOUDFLARE_ACCOUNT_ID"
AUTH_TOKEN_ENV = "CLOUDFLARE_FLAGSHIP_TOKEN"
WRITE_TOKEN_ENV = "CLOUDFLARE_FLAGSHIP_WRITE_TOKEN"
REQUEST_TIMEOUT_SECONDS = 1.0
REQUEST_RETRIES = 0
WRITE_TIMEOUT_SECONDS = 10.0
API_BASE_URL = "https://api.cloudflare.com/client/v4"
ON_VARIATION = "on"
OFF_VARIATION = "off"
# The flag's own record of who last moved it: answered on a read, refused on a write.
ANSWERED_ONLY_FIELDS = frozenset({"updated_at", "updated_by"})


def build(cache_ttl_seconds: float) -> FeatureProvider | None:
    """The provider core binds to the OpenFeature API at boot, or None when the deploy carries no
    Flagship app, account, or token — the deploy then reads every flag as its code default."""
    app_id = deploy_env(APP_ID_ENV)
    account_id = deploy_env(ACCOUNT_ID_ENV)
    auth_token = deploy_env(AUTH_TOKEN_ENV)
    if not (app_id and account_id and auth_token):
        warn(
            "flagship.unkeyed",
            app_id_set=bool(app_id),
            account_id_set=bool(account_id),
            auth_token_set=bool(auth_token),
        )
        return None
    return FlagshipServerProvider(
        app_id=app_id,
        account_id=account_id,
        auth_token=auth_token,
        timeout=REQUEST_TIMEOUT_SECONDS,
        retries=REQUEST_RETRIES,
        cache_ttl=cache_ttl_seconds or None,
    )


@dataclass(frozen=True)
class FlagshipAdmin:
    """One flag's served value, written where terraform does not reach.

    Terraform creates each flag and then ignores `default_variation`, so this is the only thing that
    moves it — and it moves nothing else. The API's `PUT` takes the whole flag, so the body is what
    the app answered with that one field swapped, every other field carried back as read rather than
    named here: the rules a rollout was built from, and the `type` and `flag_key` terraform sets. A
    body naming its own fields would drop those two, and the next plan would want to replace the
    flag — which the destructive-change guard refuses, so no deploy lands until somebody repairs the
    state by hand."""

    account_id: str
    app_id: str
    token: str
    client: httpx.Client = field(
        default_factory=lambda: httpx.Client(timeout=WRITE_TIMEOUT_SECONDS)
    )

    def serve(self, key: str, *, on: bool) -> None:
        held = self._call("GET", f"/{key}").get("result")
        if not isinstance(held, dict):
            raise RuntimeError(f"flagship holds no readable flag {key!r}")
        wanted = ON_VARIATION if on else OFF_VARIATION
        if wanted not in held["variations"]:
            raise RuntimeError(f"flagship flag {key!r} has no {wanted!r} variation to serve")
        carried = {name: value for name, value in held.items() if name not in ANSWERED_ONLY_FIELDS}
        self._call("PUT", f"/{key}", body={**carried, "default_variation": wanted})

    def list(self) -> tuple[str, ...]:
        """Every flag key this app holds, sorted.

        Terraform states which flags should exist and the flags gate states which the code reads.
        This is the third answer, and the only one taken from the service itself: a key terraform
        never created, or one it destroyed in one environment and not the other, is visible from
        here and from nowhere else."""
        held = self._call("GET", "").get("result")
        if not isinstance(held, list):
            raise RuntimeError("flagship answered no readable flag list")
        # The collection names a flag `key` alone; only the single-flag read carries `flag_key`
        # beside it, which is the field `serve` writes back.
        return tuple(sorted(str(flag["key"]) for flag in held))

    def _call(
        self, method: str, path: str, body: dict[str, object] | None = None
    ) -> dict[str, object]:
        """One request, its refusal read out of the body Cloudflare answers with. This runs in
        `ufoctl`, off the event loop and once per verb, so the client is synchronous."""
        response = self.client.request(
            method,
            f"{API_BASE_URL}/accounts/{self.account_id}/flagship/apps/{self.app_id}/flags{path}",
            headers={"authorization": f"Bearer {self.token}"},
            json=body,
        )
        answered = response.json() if response.content else {}
        if response.status_code >= 400 or not answered.get("success", False):
            errors = answered.get("errors") or answered.get("error") or response.text
            raise RuntimeError(f"flagship {method} {path}: {response.status_code} {errors}")
        return answered


def build_admin() -> FlagshipAdmin:
    """The client `ufoctl flags set` writes through. It fails loud on a missing key: an operator who
    ran a write verb is owed the reason it wrote nothing, not a silent pass."""
    account_id = deploy_env(ACCOUNT_ID_ENV) or ""
    app_id = deploy_env(APP_ID_ENV) or ""
    token = deploy_env(WRITE_TOKEN_ENV) or ""
    missing = [
        name
        for name, value in (
            (ACCOUNT_ID_ENV, account_id),
            (APP_ID_ENV, app_id),
            (WRITE_TOKEN_ENV, token),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"flagship writes need {', '.join(missing)}")
    return FlagshipAdmin(account_id=account_id, app_id=app_id, token=token)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        deploy_keys=(APP_ID_ENV, ACCOUNT_ID_ENV, AUTH_TOKEN_ENV),
        flag_providers=(FlagProviderSpec(backend=FLAG_BACKEND, build=build),),
    )

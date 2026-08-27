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
from ufo.sdk.manifest import FlagAdmin, FlagAdminSpec, FlagProviderSpec, FlagState, Manifest
from ufo.sdk.o11y import warn

NAME = "flagship"
VERSION = "0.1.0"
FLAG_BACKEND = "flagship"
APP_ID_ENV = "CLOUDFLARE_FLAGSHIP_APP_ID"
ACCOUNT_ID_ENV = "CLOUDFLARE_ACCOUNT_ID"
AUTH_TOKEN_ENV = "CLOUDFLARE_FLAGSHIP_TOKEN"
ADMIN_TOKEN_ENV = "CLOUDFLARE_FLAGSHIP_ADMIN_TOKEN"
REQUEST_TIMEOUT_SECONDS = 1.0
REQUEST_RETRIES = 0
ADMIN_TIMEOUT_SECONDS = 10.0
API_BASE_URL = "https://api.cloudflare.com/client/v4"
ON_VARIATION = "on"
OFF_VARIATION = "off"
VARIATIONS = {ON_VARIATION: True, OFF_VARIATION: False}
PAGE_SIZE = 100


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
    """The deploy's Flagship app as an operator writes it, over the same REST API the SDK reads.

    A flag this creates is one boolean with two variations and no targeting rules, so
    `default_variation` — what the app serves where no rule matches — is every evaluation's answer.

    A flag it writes is one the app already holds, and the API's `PUT` takes the whole flag: the
    body is built from what the app answers, with `default_variation` swapped and its rules, its
    variations and its `enabled` state carried back verbatim. Sending a fresh body would delete
    every rule the flag holds, which is a rollout someone built and nothing here could restore.

    The write token is its own deploy key. Serve's token may evaluate and nothing else, so the
    credential that can turn a feature on for every workspace is one an operator holds, never one a
    pod carries."""

    account_id: str
    app_id: str
    token: str
    client: httpx.Client = field(
        default_factory=lambda: httpx.Client(timeout=ADMIN_TIMEOUT_SECONDS)
    )

    def listing(self) -> tuple[FlagState, ...]:
        flags: list[FlagState] = []
        after = ""
        while True:
            page = self._call(
                "GET",
                "",
                params={"per_page": str(PAGE_SIZE), **({"after": after} if after else {})},
            )
            held = page.get("result") or []
            if not isinstance(held, list):
                raise RuntimeError("flagship listed flags in a shape this cannot read")
            flags.extend(
                FlagState(
                    key=str(flag["key"]),
                    on=flag["default_variation"] == ON_VARIATION,
                    targeted=bool(flag.get("rules")),
                )
                for flag in held
            )
            info = page.get("result_info")
            after = str(info.get("after") or "") if isinstance(info, dict) else ""
            if not after:
                return tuple(flags)

    def create(self, key: str, *, on: bool) -> None:
        self._call(
            "POST",
            "",
            body={
                "key": key,
                "enabled": True,
                "default_variation": ON_VARIATION if on else OFF_VARIATION,
                "variations": VARIATIONS,
                "rules": [],
            },
        )

    def set(self, key: str, *, on: bool) -> None:
        held = self._call("GET", f"/{key}").get("result")
        if not isinstance(held, dict):
            raise RuntimeError(f"flagship holds no readable flag {key!r}")
        wanted = ON_VARIATION if on else OFF_VARIATION
        if wanted not in held["variations"]:
            raise RuntimeError(f"flagship flag {key!r} has no {wanted!r} variation to serve")
        self._call(
            "PUT",
            f"/{key}",
            body={
                "key": key,
                "enabled": held["enabled"],
                "default_variation": wanted,
                "variations": held["variations"],
                "rules": held["rules"],
            },
        )

    def _call(
        self,
        method: str,
        path: str,
        body: dict[str, object] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, object]:
        """One request, its refusal read out of the body Cloudflare answers with.

        This runs in `ufoctl`, off the event loop and once per verb, so it is a synchronous client:
        the operator waits for exactly one answer and there is nothing else in the process to
        stall."""
        response = self.client.request(
            method,
            f"{API_BASE_URL}/accounts/{self.account_id}/flagship/apps/{self.app_id}/flags{path}",
            headers={"authorization": f"Bearer {self.token}"},
            json=body,
            params=params,
        )
        answered = response.json() if response.content else {}
        if response.status_code >= 400 or not answered.get("success", False):
            errors = answered.get("errors") or answered.get("error") or response.text
            raise RuntimeError(
                f"flagship {method} {path or '/flags'}: {response.status_code} {errors}"
            )
        return answered


def build_admin() -> FlagAdmin:
    """The administration client `ufoctl flags` writes through. It fails loud on a missing key: an
    operator who ran a write verb is owed the reason it wrote nothing, not a silent pass."""
    account_id = deploy_env(ACCOUNT_ID_ENV) or ""
    app_id = deploy_env(APP_ID_ENV) or ""
    token = deploy_env(ADMIN_TOKEN_ENV) or ""
    missing = [
        name
        for name, value in (
            (ACCOUNT_ID_ENV, account_id),
            (APP_ID_ENV, app_id),
            (ADMIN_TOKEN_ENV, token),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"flagship administration needs {', '.join(missing)}")
    return FlagshipAdmin(account_id=account_id, app_id=app_id, token=token)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        deploy_keys=(APP_ID_ENV, ACCOUNT_ID_ENV, AUTH_TOKEN_ENV),
        flag_providers=(FlagProviderSpec(backend=FLAG_BACKEND, build=build),),
        flag_admins=(FlagAdminSpec(backend=FLAG_BACKEND, build=build_admin),),
    )

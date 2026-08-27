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
REQUEST_TIMEOUT_SECONDS = 1.0
REQUEST_RETRIES = 0


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


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        deploy_keys=(APP_ID_ENV, ACCOUNT_ID_ENV, AUTH_TOKEN_ENV),
        flag_providers=(FlagProviderSpec(backend=FLAG_BACKEND, build=build),),
    )

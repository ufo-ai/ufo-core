import hashlib
import html
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from ufo_ext_sites.manifest import manifest as sites_manifest
from ufo_ext_sites.store import HostedSites, Visibility
from ufo_ext_sites.surface import (
    FRAME_PATH,
    GENERIC_SHARE_TITLE,
    NO_BROWSER_SIGN_IN,
    NO_PORTAL_PAGE,
    NOT_SIGNED_IN_PAGE,
    SHARE_CARD,
    SHARE_CARD_DIGEST,
    SITE_CARD_PATH,
    shipped_homepage_url,
    site_token,
)
from ufo_testsupport.surfaces import (
    EMPTY_SKILL_REGISTRY,
    UNREACHED_AMBIENT_REPLY,
    no_member_skills,
)

from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.config import SitesConfig
from ufo.db import workspace_tx
from ufo.harness.auth.bearer import UFO_TOKEN_SECRET_ENV
from ufo.harness.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import ProxyEndpoint
from ufo.runtime.hub import InProcessHub
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.sdk.audience import SHARED_AUDIENCE
from ufo.serve import DEFAULT_SITES, _mount_shared_surfaces

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

PUBLIC_BASE_URL = "https://ufo.example.test"
INGRESS_HOST = "sites.example.test"
INGRESS_BASE_URL = f"https://{INGRESS_HOST}"
SITE = "dashboard"
SITE_PORT = 40000
COMPOSED_CARD = b"\xff\xd8\xff composed card"
COMPOSED_CARD_KEY = "artifacts/card/share_card.jpg"
CONFIGURED_CARD = "https://cdn.example.test/share/og-site.jpg"


class _NoAdmission:
    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        raise AssertionError("the sites frame admits nothing")


@dataclass(frozen=True)
class _Deploy:
    signs_in: AsyncClient
    signs_none_in: AsyncClient
    baseless: AsyncClient
    blob: WorkspaceBlobStore


def _mounted(
    tmp_path: Path,
    blob: WorkspaceBlobStore,
    public_base_url: str | None,
    sign_in_path: str | None,
    sites: SitesConfig = DEFAULT_SITES,
) -> AsyncClient:
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (sites_manifest(),),
        None,
        blob,
        ConversationSandbox(
            carrier=LocalCarrier(),
            backend="local",
            off_cluster=False,
            image_ref=SANDBOX_IMAGE_REF,
            proxy=ProxyEndpoint(port=0, ca_cert="test-ca"),
            workspace_root=tmp_path / "workspaces",
        ),
        InProcessHub(),
        _NoAdmission(),
        "",
        public_base_url,
        INGRESS_BASE_URL,
        ("auto",),
        ambient_reply=UNREACHED_AMBIENT_REPLY,
        skills=EMPTY_SKILL_REGISTRY,
        member_skill_listing=no_member_skills,
        sign_in_path=sign_in_path,
        sites=sites,
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url=PUBLIC_BASE_URL)


@pytest.fixture
async def deploy(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[_Deploy]:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, "sites-surface-secret")
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path / "blob"))
    async with (
        _mounted(tmp_path, blob, PUBLIC_BASE_URL, "/login") as signs_in,
        _mounted(tmp_path, blob, PUBLIC_BASE_URL, None) as signs_none_in,
        _mounted(tmp_path, blob, None, None) as baseless,
    ):
        yield _Deploy(signs_in=signs_in, signs_none_in=signs_none_in, baseless=baseless, blob=blob)


async def _hosted(visibility: Visibility) -> tuple[UUID, UUID, str]:
    workspace_id, member_id, conversation_id = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="owner@example.com",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    with ws(workspace_id):
        await HostedSites(workspace_id, workspace_tx).register(
            conversation_id,
            SITE,
            SITE_PORT,
            member_id,
            visibility,
            SHARED_AUDIENCE,
            True,
            manifest=None,
        )
    return workspace_id, conversation_id, site_token(workspace_id, conversation_id, SITE)


def _og_image(page: str) -> str | None:
    marker = '<meta property=og:image content="'
    if marker not in page:
        return None
    return page.split(marker, 1)[1].split('"', 1)[0]


@pytest.mark.parametrize("visibility", ["private", "workspace"])
async def test_a_non_public_site_says_only_public_sites_open_where_no_browser_signs_in(
    deploy: _Deploy, visibility: Visibility
) -> None:
    _workspace_id, _conversation_id, token = await _hosted(visibility)

    unsigned = await deploy.signs_none_in.get(f"{FRAME_PATH}/{token}")
    signed = await deploy.signs_in.get(f"{FRAME_PATH}/{token}")

    assert unsigned.status_code == 200
    assert NO_BROWSER_SIGN_IN in unsigned.text
    assert "<iframe" not in unsigned.text
    assert NOT_SIGNED_IN_PAGE.format(sign_in="/login") in signed.text
    assert NO_BROWSER_SIGN_IN not in signed.text


@pytest.mark.parametrize("sign_in_url", ["/logout", "/sign&out"])
async def test_a_non_public_site_links_the_sites_sign_in_in_place_of_the_deploys(
    deploy: _Deploy, tmp_path: Path, sign_in_url: str
) -> None:
    _workspace_id, _conversation_id, token = await _hosted("workspace")
    configured = SitesConfig(sign_in_url=sign_in_url)

    async with _mounted(tmp_path, deploy.blob, PUBLIC_BASE_URL, "/login", configured) as client:
        page = (await client.get(f"{FRAME_PATH}/{token}")).text

    assert NOT_SIGNED_IN_PAGE.format(sign_in=html.escape(sign_in_url, quote=True)) in page
    assert 'href="/login"' not in page


async def test_a_public_site_opens_and_unfurls_with_the_generic_card_off_the_deploys_own_host(
    deploy: _Deploy,
) -> None:
    _workspace_id, _conversation_id, token = await _hosted("public")

    opened = await deploy.signs_none_in.get(f"{FRAME_PATH}/{token}")

    assert opened.status_code == 200
    assert f".{INGRESS_HOST}/" in opened.text.split("<iframe", 1)[1]
    card = f"{SITE_CARD_PATH}/{token}/{SHARE_CARD_DIGEST}.jpg"
    assert _og_image(opened.text) == f"{PUBLIC_BASE_URL}{card}"
    served = await deploy.signs_none_in.get(card)
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/jpeg"
    assert served.content == SHARE_CARD


async def test_a_public_sites_own_card_is_named_and_served_until_the_site_narrows(
    deploy: _Deploy,
) -> None:
    workspace_id, conversation_id, token = await _hosted("public")
    digest = hashlib.sha256(COMPOSED_CARD).hexdigest()
    sites = HostedSites(workspace_id, workspace_tx)
    with ws(workspace_id):
        await deploy.blob.put(COMPOSED_CARD_KEY, COMPOSED_CARD)
        await sites.set_share_card(conversation_id, SITE, COMPOSED_CARD_KEY, digest)
    own = f"{SITE_CARD_PATH}/{token}/{digest}.jpg"

    public = await deploy.signs_in.get(f"{FRAME_PATH}/{token}")
    assert _og_image(public.text) == f"{PUBLIC_BASE_URL}{own}"
    assert (await deploy.signs_in.get(own)).content == COMPOSED_CARD

    with ws(workspace_id):
        await sites.set_visibility(conversation_id, SITE, "workspace")
    narrowed = await deploy.signs_in.get(f"{FRAME_PATH}/{token}")
    generic = f"{PUBLIC_BASE_URL}{SITE_CARD_PATH}/{token}/{SHARE_CARD_DIGEST}.jpg"
    assert _og_image(narrowed.text) == generic
    assert f'<meta property=og:title content="{GENERIC_SHARE_TITLE}">' in narrowed.text
    assert (await deploy.signs_in.get(own)).status_code == 404


async def test_a_site_with_no_card_of_its_own_unfurls_as_the_configured_card(
    deploy: _Deploy, tmp_path: Path
) -> None:
    workspace_id, conversation_id, token = await _hosted("public")
    digest = hashlib.sha256(COMPOSED_CARD).hexdigest()
    sites = HostedSites(workspace_id, workspace_tx)
    configured = SitesConfig(share_card_url=CONFIGURED_CARD)
    frame = f"{FRAME_PATH}/{token}"

    async with (
        _mounted(tmp_path, deploy.blob, PUBLIC_BASE_URL, None, configured) as based,
        _mounted(tmp_path, deploy.blob, None, None, configured) as baseless,
    ):
        assert _og_image((await based.get(frame)).text) == CONFIGURED_CARD
        assert _og_image((await baseless.get(frame)).text) == CONFIGURED_CARD
        with ws(workspace_id):
            await deploy.blob.put(COMPOSED_CARD_KEY, COMPOSED_CARD)
            await sites.set_share_card(conversation_id, SITE, COMPOSED_CARD_KEY, digest)
        own = f"{PUBLIC_BASE_URL}{SITE_CARD_PATH}/{token}/{digest}.jpg"
        assert _og_image((await based.get(frame)).text) == own
        with ws(workspace_id):
            await sites.set_visibility(conversation_id, SITE, "workspace")
        assert _og_image((await based.get(frame)).text) == CONFIGURED_CARD


async def test_a_shipped_app_page_opened_outside_the_portal_unfurls_as_the_generic_card(
    deploy: _Deploy, tmp_path: Path
) -> None:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=uuid4(),
                workspace_id=workspace_id,
                name=SITE,
                prompt="p",
                model="claude-opus-4-8",
                provisioned_by=f"app_{SITE}",
                provisioned_name=SITE,
                provisioned_version="1",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    link = shipped_homepage_url(PUBLIC_BASE_URL, workspace_id, SITE, "0" * 64)
    assert link is not None
    frame = link.removeprefix(PUBLIC_BASE_URL)
    token = frame.removeprefix(f"{FRAME_PATH}/")
    configured = SitesConfig(share_card_url=CONFIGURED_CARD)

    bundled = (await deploy.signs_none_in.get(frame)).text
    async with _mounted(tmp_path, deploy.blob, PUBLIC_BASE_URL, None, configured) as client:
        named = (await client.get(frame)).text

    assert NO_PORTAL_PAGE in bundled
    card = f"{PUBLIC_BASE_URL}{SITE_CARD_PATH}/{token}/{SHARE_CARD_DIGEST}.jpg"
    assert _og_image(bundled) == card
    assert NO_PORTAL_PAGE in named
    assert _og_image(named) == CONFIGURED_CARD


async def test_a_forged_token_or_a_stale_digest_answers_the_frames_404(deploy: _Deploy) -> None:
    _workspace_id, _conversation_id, token = await _hosted("public")

    answers = [
        await deploy.signs_in.get(f"{FRAME_PATH}/forged"),
        await deploy.signs_in.get(f"{SITE_CARD_PATH}/forged/{SHARE_CARD_DIGEST}.jpg"),
        await deploy.signs_in.get(f"{SITE_CARD_PATH}/{token}/{'0' * 64}.jpg"),
    ]

    assert {(answer.status_code, answer.text) for answer in answers} == {(404, "no such site")}


async def test_a_deploy_with_no_public_base_names_no_card_and_no_canonical_link(
    deploy: _Deploy,
) -> None:
    _workspace_id, _conversation_id, token = await _hosted("public")

    opened = await deploy.baseless.get(f"{FRAME_PATH}/{token}")

    assert opened.status_code == 200
    assert _og_image(opened.text) is None
    assert "og:url" not in opened.text
    assert "twitter:image" not in opened.text
    assert f'<meta property=og:title content="{SITE}">' in opened.text

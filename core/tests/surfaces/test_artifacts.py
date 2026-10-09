import mimetypes
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from email.message import Message
from io import BytesIO
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image

from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.harness.auth.bearer import SESSION_COOKIE, UFO_TOKEN_SECRET_ENV, mint_token
from ufo.harness.auth.token_signing import sign_detached
from ufo.runtime.media.artifact_url import (
    ARTIFACT_KEY_PREFIX,
    ARTIFACT_MEDIA_TYPES,
    ARTIFACT_URL_BUCKET_SECONDS,
    ARTIFACT_URL_TTL_SECONDS,
    TEXT_APPLICATION_MEDIA_TYPES,
    ArtifactUrlError,
    ArtifactUrlExpired,
    artifact_media_type,
    artifact_url_expiry,
    is_text_media,
    mint_artifact_url,
    verify_artifact_url,
)
from ufo.runtime.media.image_previews import IMAGE_PREVIEW_MAX_BYTES, ImagePreviewGrant
from ufo.runtime.surfaces.artifacts import EXPIRED_DETAIL
from ufo.runtime.surfaces.artifacts import router as artifacts_router
from ufo.runtime.workspace import ws
from ufo.schema import tables

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

SECRET = "artifact-signing-secret"
BEARER_SECRET = "bearer-signing-secret"
MEMBER_EMAIL = "member@example.com"
SIGN_IN_PATH = "/login"


def _future() -> int:
    return int(datetime.now(UTC).timestamp()) + 3600


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), (12, 34, 56)).save(output, format="PNG")
    return output.getvalue()


def _parts(url: str) -> tuple[str, str, str, str, str]:
    """(artifact_id, filename, exp, ws, sig) of a minted URL — path is identity, query the grant."""
    split = urlsplit(url)
    artifact_id, filename = split.path.removeprefix(f"/{ARTIFACT_KEY_PREFIX}").split("/")
    query = parse_qs(split.query)
    return artifact_id, unquote(filename), query["exp"][0], query["ws"][0], query["sig"][0]


def _client(blob: WorkspaceBlobStore, sign_in_path: str | None) -> AsyncClient:
    app = FastAPI()
    app.state.blob = blob
    app.state.artifact_token_secret = SECRET
    app.state.sign_in_path = sign_in_path
    app.include_router(artifacts_router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://core")


@pytest.fixture
async def artifact_client(
    tmp_path,
) -> AsyncIterator[tuple[AsyncClient, WorkspaceBlobStore]]:
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path))
    async with _client(blob, SIGN_IN_PATH) as client:
        yield client, blob


def test_artifact_media_types_answer_from_the_product_table_on_any_host() -> None:
    assert artifact_media_type("Deck.PPTX") == (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )
    assert artifact_media_type("brief.docx") == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert artifact_media_type("grid.xlsx") == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert artifact_media_type("fix.patch") == "text/x-patch"
    assert artifact_media_type("fix.diff") == "text/x-patch"
    assert artifact_media_type("compose.yaml") == "application/yaml"
    assert artifact_media_type("ci.YML") == "application/yaml"
    assert artifact_media_type("pyproject.toml") == "application/toml"
    assert artifact_media_type("app.ts") == "application/typescript"
    assert artifact_media_type("shot.webp") == "image/webp"
    assert artifact_media_type("clip.mkv") == "video/x-matroska"
    assert artifact_media_type("App.tsx") == "application/octet-stream"
    bare_registry = mimetypes.MimeTypes(filenames=())
    for suffix in ARTIFACT_MEDIA_TYPES:
        assert bare_registry.guess_type(f"a{suffix}")[0] is None


def test_text_media_is_every_text_type_and_the_application_types_the_store_emits() -> None:
    for media_type in ("text/plain", "text/x-python", "Text/Markdown", "text/yaml"):
        assert is_text_media(media_type)
    for media_type in TEXT_APPLICATION_MEDIA_TYPES:
        assert is_text_media(media_type)
    for filename in ("compose.yaml", "ci.yml", "pyproject.toml", "app.ts", "run.sh", "data.json"):
        assert is_text_media(artifact_media_type(filename))
    for media_type in (
        "application/octet-stream",
        "application/x-yaml",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "image/png",
        "video/mp2t",
    ):
        assert not is_text_media(media_type)


def test_mint_and_verify_agree_on_a_round_trip() -> None:
    now = datetime.now(UTC)
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/report.txt"
    expires = int(now.timestamp()) + 100
    workspace_id = uuid4()
    url = mint_artifact_url(SECRET, key, expires, workspace_id=workspace_id)
    assert urlsplit(url).path.removeprefix("/") == key
    artifact_id, filename, exp, workspace, sig = _parts(url)
    claims = verify_artifact_url(SECRET, artifact_id, filename, exp, sig, "", workspace, now)
    assert claims.blob_key == key
    assert claims.filename == "report.txt"
    assert claims.expires_at == expires
    assert claims.workspace_id == workspace_id
    assert claims.preview is None


def test_expiry_buckets_hold_urls_still_between_mints() -> None:
    inside = datetime.fromtimestamp(1_000_000, tz=UTC)
    later = datetime.fromtimestamp(1_000_030, tz=UTC)
    assert artifact_url_expiry(inside) == artifact_url_expiry(later)
    for moment in (1_000_000, 997_200, 997_201, 1_000_799):
        expiry = artifact_url_expiry(datetime.fromtimestamp(moment, tz=UTC))
        assert expiry % ARTIFACT_URL_BUCKET_SECONDS == 0
        validity = expiry - moment
        assert ARTIFACT_URL_TTL_SECONDS < validity
        assert validity <= ARTIFACT_URL_TTL_SECONDS + ARTIFACT_URL_BUCKET_SECONDS
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/report.txt"
    workspace_id = uuid4()
    expiry = artifact_url_expiry(datetime.now(UTC))
    assert mint_artifact_url(SECRET, key, expiry, workspace_id=workspace_id) == mint_artifact_url(
        SECRET, key, expiry, workspace_id=workspace_id
    )


def test_the_workspace_claim_rides_the_query_inside_the_signature() -> None:
    now = datetime.now(UTC)
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/report.txt"
    workspace_id = uuid4()
    url = mint_artifact_url(SECRET, key, _future(), workspace_id=workspace_id)
    assert parse_qs(urlsplit(url).query)["ws"] == [str(workspace_id)]
    artifact_id, filename, exp, workspace, sig = _parts(url)
    claims = verify_artifact_url(SECRET, artifact_id, filename, exp, sig, "", workspace, now)
    assert claims.workspace_id == workspace_id
    assert claims.blob_key == key
    with pytest.raises(ArtifactUrlError):
        verify_artifact_url(SECRET, artifact_id, filename, exp, sig, "", str(uuid4()), now)
    with pytest.raises(ArtifactUrlError):
        verify_artifact_url(SECRET, artifact_id, filename, exp, sig, "", "not-a-uuid", now)


def test_an_address_without_a_workspace_claim_heals_and_never_serves() -> None:
    now = datetime.now(UTC)
    artifact_id = str(uuid4())
    exp = str(_future())
    sig = sign_detached(SECRET.encode(), f"{artifact_id}:{exp}:".encode())
    with pytest.raises(ArtifactUrlExpired) as caught:
        verify_artifact_url(SECRET, artifact_id, "report.txt", exp, sig, "", "", now)
    assert caught.value.claims.workspace_id is None
    assert caught.value.claims.blob_key == f"{ARTIFACT_KEY_PREFIX}{artifact_id}/report.txt"
    with pytest.raises(ArtifactUrlError) as refused:
        verify_artifact_url(SECRET, artifact_id, "report.txt", exp, "tampered", "", "", now)
    assert not isinstance(refused.value, ArtifactUrlExpired)


def test_a_preview_grant_rides_the_query_inside_the_signature() -> None:
    now = datetime.now(UTC)
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/chart.png"
    grant = ImagePreviewGrant(media_type="image/png", size_bytes=64)
    url = mint_artifact_url(
        SECRET, key, int(now.timestamp()) + 100, workspace_id=uuid4(), preview=grant
    )
    artifact_id, filename, exp, workspace, sig = _parts(url)
    preview = parse_qs(urlsplit(url).query)["preview"][0]
    assert verify_artifact_url(
        SECRET, artifact_id, filename, exp, sig, preview, workspace, now
    ).preview == (grant)
    with pytest.raises(ArtifactUrlError):
        verify_artifact_url(SECRET, artifact_id, filename, exp, sig, "", workspace, now)
    with pytest.raises(ArtifactUrlError):
        verify_artifact_url(SECRET, artifact_id, filename, exp, sig, "image/png:65", workspace, now)
    with pytest.raises(ArtifactUrlError):
        mint_artifact_url(
            SECRET,
            key,
            _future(),
            workspace_id=uuid4(),
            preview=ImagePreviewGrant(
                media_type="image/png", size_bytes=IMAGE_PREVIEW_MAX_BYTES + 1
            ),
        )


def test_mint_signs_only_the_artifact_key_shape() -> None:
    for key in (
        "conversations/c/messages.json.lz4",
        "artifacts/abc123",
        "artifacts/not-a-uuid/report.txt",
        f"artifacts/{uuid4()}/",
        f"artifacts/{uuid4()}/nested/report.txt",
        f"artifacts/{uuid4()}/..",
    ):
        with pytest.raises(ArtifactUrlError):
            mint_artifact_url(SECRET, key, _future(), workspace_id=uuid4())
    with pytest.raises(ArtifactUrlError):
        mint_artifact_url("", f"artifacts/{uuid4()}/report.txt", _future(), workspace_id=uuid4())


def test_verify_rejects_a_tampered_or_stale_grant() -> None:
    now = datetime.now(UTC)
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/report.txt"
    artifact_id, filename, exp, workspace, sig = _parts(
        mint_artifact_url(SECRET, key, int(now.timestamp()) + 100, workspace_id=uuid4())
    )
    for tampered in (
        (artifact_id, filename, exp, sig, "wrong-secret"),
        (str(uuid4()), filename, exp, sig, SECRET),
        (artifact_id, filename, str(int(exp) + 9999), sig, SECRET),
        (artifact_id, filename, exp, sig[:-2], SECRET),
        (artifact_id, filename, "not-a-number", sig, SECRET),
        ("not-a-uuid", filename, exp, sig, SECRET),
        (artifact_id, "..", exp, sig, SECRET),
        (artifact_id, "a/b", exp, sig, SECRET),
        (artifact_id, "", exp, sig, SECRET),
        (artifact_id, filename, exp, sig, ""),
    ):
        tampered_id, tampered_name, tampered_exp, tampered_sig, secret = tampered
        with pytest.raises(ArtifactUrlError):
            verify_artifact_url(
                secret, tampered_id, tampered_name, tampered_exp, tampered_sig, "", workspace, now
            )
    with pytest.raises(ArtifactUrlError):
        verify_artifact_url(SECRET, artifact_id, filename, exp, sig, "image/png:9", workspace, now)


def test_verify_raises_expired_carrying_the_authentic_claims() -> None:
    """Expiry is a fact about time, not authenticity: the signature already verified, so the
    exception carries the claims for a caller holding another proof of access."""
    now = datetime.now(UTC)
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/report.txt"
    artifact_id, filename, exp, workspace, sig = _parts(
        mint_artifact_url(SECRET, key, int(now.timestamp()) - 1, workspace_id=uuid4())
    )
    with pytest.raises(ArtifactUrlExpired) as caught:
        verify_artifact_url(SECRET, artifact_id, filename, exp, sig, "", workspace, now)
    assert isinstance(caught.value, ArtifactUrlError)
    assert caught.value.claims.blob_key == key


def test_a_renamed_filename_verifies_but_addresses_a_different_blob() -> None:
    now = datetime.now(UTC)
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/report.txt"
    artifact_id, _, exp, workspace, sig = _parts(
        mint_artifact_url(SECRET, key, int(now.timestamp()) + 100, workspace_id=uuid4())
    )
    claims = verify_artifact_url(SECRET, artifact_id, "renamed.exe", exp, sig, "", workspace, now)
    assert claims.blob_key != key
    assert claims.blob_key.endswith("/renamed.exe")


@pytest.mark.parametrize(
    "filename,media_type",
    [
        ("index.html", "text/html"),
        ("report.pdf", "application/pdf"),
        ("data.csv", "text/csv"),
        ("photo.png", "image/png"),
        ("file.qzx", "application/octet-stream"),
        ("chart.png.gz", "application/octet-stream"),
    ],
)
async def test_artifact_download_serves_attachments_under_their_real_type(
    artifact_client: tuple[AsyncClient, WorkspaceBlobStore], filename: str, media_type: str
) -> None:
    """A plain grant always downloads — under the type the filename names, so a consumer sees
    `application/pdf` rather than octet-stream — and only a signed preview claim renders inline."""
    client, blob = artifact_client
    workspace_id = uuid4()
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/{filename}"
    with ws(workspace_id):
        await blob.put(key, b"the bytes")
    response = await client.get(
        mint_artifact_url(SECRET, key, _future(), workspace_id=workspace_id)
    )
    assert response.status_code == 200
    assert response.content == b"the bytes"
    assert response.headers["content-type"].startswith(media_type)
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "public, max-age=600"


async def test_artifact_preview_serves_a_validated_raster_inline(
    artifact_client: tuple[AsyncClient, WorkspaceBlobStore],
) -> None:
    client, blob = artifact_client
    workspace_id = uuid4()
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/chart.png"
    raster = _png()
    with ws(workspace_id):
        await blob.put(key, raster)
    grant = ImagePreviewGrant(media_type="image/png", size_bytes=len(raster))
    shown = await client.get(
        mint_artifact_url(SECRET, key, _future(), workspace_id=workspace_id, preview=grant)
    )
    assert shown.status_code == 200
    assert shown.content == raster
    assert shown.headers["content-type"] == "image/png"
    assert shown.headers["x-content-type-options"] == "nosniff"
    assert shown.headers["cache-control"] == "public, max-age=600"
    assert "content-disposition" not in shown.headers


async def test_artifact_preview_rejects_mislabeled_invalid_and_unsigned_claims(
    artifact_client: tuple[AsyncClient, WorkspaceBlobStore],
) -> None:
    """The preview claim is signed and the bytes must prove it: a mislabeled type, a non-image, a
    wrong size, and a claim pasted onto a plain grant all refuse rather than render."""
    client, blob = artifact_client
    workspace_id = uuid4()
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/chart.png"
    raster = _png()
    mislabeled_key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/chart.jpg"
    invalid_key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/chart.png"
    with ws(workspace_id):
        await blob.put(key, raster)
        await blob.put(mislabeled_key, raster)
        await blob.put(invalid_key, b"not an image")

    mislabeled = await client.get(
        mint_artifact_url(
            SECRET,
            mislabeled_key,
            _future(),
            workspace_id=workspace_id,
            preview=ImagePreviewGrant(media_type="image/jpeg", size_bytes=len(raster)),
        )
    )
    invalid = await client.get(
        mint_artifact_url(
            SECRET,
            invalid_key,
            _future(),
            workspace_id=workspace_id,
            preview=ImagePreviewGrant(media_type="image/png", size_bytes=len(b"not an image")),
        )
    )
    wrong_size = await client.get(
        mint_artifact_url(
            SECRET,
            key,
            _future(),
            workspace_id=workspace_id,
            preview=ImagePreviewGrant(media_type="image/png", size_bytes=len(raster) - 1),
        )
    )
    pasted = await client.get(
        mint_artifact_url(SECRET, key, _future(), workspace_id=workspace_id)
        + f"&preview=image/png:{len(raster)}"
    )
    assert mislabeled.status_code == 415
    assert invalid.status_code == 415
    assert wrong_size.status_code == 415
    assert pasted.status_code == 403


async def test_artifact_download_escapes_special_filenames(
    artifact_client: tuple[AsyncClient, WorkspaceBlobStore],
) -> None:
    """A `"` would break the quoted `filename=` and a unicode name is neither URL- nor header-safe;
    both must ride the URL out and back as a well-formed Content-Disposition that still serves."""
    client, blob = artifact_client
    workspace_id = uuid4()
    for filename in ('a"quote.txt', "résumé pièce.txt"):
        key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/{filename}"
        with ws(workspace_id):
            await blob.put(key, b"the bytes")
        response = await client.get(
            mint_artifact_url(SECRET, key, _future(), workspace_id=workspace_id)
        )
        assert response.status_code == 200
        assert response.content == b"the bytes"
        parsed = Message()
        parsed["content-disposition"] = response.headers["content-disposition"]
        assert parsed.get_content_disposition() == "attachment"
        assert parsed.get_filename() == filename


async def test_artifact_download_rejects_a_tampered_stale_or_missing_grant(
    artifact_client: tuple[AsyncClient, WorkspaceBlobStore],
) -> None:
    client, blob = artifact_client
    workspace_id = uuid4()
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/report.txt"
    with ws(workspace_id):
        await blob.put(key, b"x")
    artifact_id, filename, exp, workspace, sig = _parts(
        mint_artifact_url(SECRET, key, _future(), workspace_id=workspace_id)
    )
    path = f"/{ARTIFACT_KEY_PREFIX}{artifact_id}/{filename}"
    tampered = await client.get(f"{path}?exp={exp}&ws={workspace}&sig={sig[:-2]}xx")
    expired = await client.get(
        mint_artifact_url(
            SECRET, key, int(datetime.now(UTC).timestamp()) - 10, workspace_id=workspace_id
        )
    )
    unclaimed = await client.get(f"{path}?exp={exp}&sig={sig}")
    grantless = await client.get(path)
    bare = await client.get(f"/{ARTIFACT_KEY_PREFIX}{artifact_id}")
    assert tampered.status_code == 403
    assert expired.status_code == 403
    assert unclaimed.status_code == 403
    assert grantless.status_code == 403
    assert bare.status_code == 404


async def test_artifact_download_404s_a_renamed_or_absent_blob(
    artifact_client: tuple[AsyncClient, WorkspaceBlobStore],
) -> None:
    """A well-formed URL whose blob is gone (reaped, or re-pointed by a renamed filename) is a 404
    decided up front, before the stream opens."""
    client, blob = artifact_client
    workspace_id = uuid4()
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/report.txt"
    with ws(workspace_id):
        await blob.put(key, b"x")
    artifact_id, _, exp, workspace, sig = _parts(
        mint_artifact_url(SECRET, key, _future(), workspace_id=workspace_id)
    )
    renamed = await client.get(
        f"/{ARTIFACT_KEY_PREFIX}{artifact_id}/renamed.exe?exp={exp}&ws={workspace}&sig={sig}"
    )
    absent = await client.get(
        mint_artifact_url(
            SECRET, f"{ARTIFACT_KEY_PREFIX}{uuid4()}/gone.txt", _future(), workspace_id=workspace_id
        )
    )
    assert renamed.status_code == 404
    assert absent.status_code == 404


async def _seed_shared_artifact(blob_key: str, filename: str) -> UUID:
    workspace_id, agent_id, conversation_id, turn_id = uuid4(), uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=uuid4(),
                workspace_id=workspace_id,
                email=MEMBER_EMAIL,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="be brief",
                model="claude-opus-4-8",
                is_main=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="web",
                queue_key=str(uuid4()),
                member_id=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="done",
                inbound="share",
                terminal={"status": "done", "text": "shared"},
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.shared_artifact).values(
                turn_id=turn_id,
                blob_key=blob_key,
                workspace_id=workspace_id,
                filename=filename,
                subject=None,
                media_type="text/plain",
                size_bytes=9,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id


def _session_cookie(workspace_id: UUID, email: str = MEMBER_EMAIL) -> dict[str, str]:
    bearer = mint_token(BEARER_SECRET, str(workspace_id), email, ttl=timedelta(hours=1))
    return {"cookie": f"{SESSION_COOKIE}={bearer}"}


async def test_an_expired_url_redirects_a_member_to_a_fresh_grant(
    db: None,
    artifact_client: tuple[AsyncClient, WorkspaceBlobStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, BEARER_SECRET)
    client, blob = artifact_client
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/report.txt"
    workspace_id = await _seed_shared_artifact(key, "report.txt")
    with ws(workspace_id):
        await blob.put(key, b"the bytes")
    expired = mint_artifact_url(
        SECRET, key, int(datetime.now(UTC).timestamp()) - 10, workspace_id=workspace_id
    )
    response = await client.get(expired, headers=_session_cookie(workspace_id))
    assert response.status_code == 303
    assert response.headers["cache-control"] == "private, no-store"
    fresh = response.headers["location"]
    assert urlsplit(fresh).path == urlsplit(expired).path
    followed = await client.get(fresh)
    assert followed.status_code == 200
    assert followed.content == b"the bytes"


async def test_an_unclaimed_address_redirects_a_member_to_a_scoped_grant(
    db: None,
    artifact_client: tuple[AsyncClient, WorkspaceBlobStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, BEARER_SECRET)
    client, blob = artifact_client
    artifact_id = uuid4()
    key = f"{ARTIFACT_KEY_PREFIX}{artifact_id}/report.txt"
    workspace_id = await _seed_shared_artifact(key, "report.txt")
    with ws(workspace_id):
        await blob.put(key, b"the bytes")
    exp = _future()
    sig = sign_detached(SECRET.encode(), f"{artifact_id}:{exp}:".encode())
    unclaimed = f"/{ARTIFACT_KEY_PREFIX}{artifact_id}/report.txt?exp={exp}&sig={sig}"
    response = await client.get(unclaimed, headers=_session_cookie(workspace_id))
    assert response.status_code == 303
    fresh = response.headers["location"]
    assert urlsplit(fresh).path == urlsplit(unclaimed).path
    assert parse_qs(urlsplit(fresh).query)["ws"] == [str(workspace_id)]
    followed = await client.get(fresh)
    assert followed.status_code == 200
    assert followed.content == b"the bytes"
    refused = await client.get(unclaimed)
    assert refused.status_code == 403


async def test_an_expired_url_refuses_a_missing_foreign_or_unmembered_session(
    db: None,
    artifact_client: tuple[AsyncClient, WorkspaceBlobStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, BEARER_SECRET)
    client, blob = artifact_client
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/report.txt"
    workspace_id = await _seed_shared_artifact(key, "report.txt")
    with ws(workspace_id):
        await blob.put(key, b"the bytes")
    expired = mint_artifact_url(
        SECRET, key, int(datetime.now(UTC).timestamp()) - 10, workspace_id=workspace_id
    )
    no_cookie = await client.get(expired)
    foreign = await client.get(expired, headers=_session_cookie(uuid4()))
    unmembered = await client.get(
        expired, headers=_session_cookie(workspace_id, email="stranger@example.com")
    )
    garbage = await client.get(expired, headers={"cookie": f"{SESSION_COOKIE}=nonsense"})
    for response in (no_cookie, foreign, unmembered, garbage):
        assert response.status_code == 403
        assert response.json()["detail"] == EXPIRED_DETAIL


async def test_an_expired_url_refuses_a_member_whose_seat_was_revoked(
    db: None,
    artifact_client: tuple[AsyncClient, WorkspaceBlobStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bearer is stateless and outlives the seat it was minted for, so revoking web access must
    end the refresh too: an unseated member's live session no longer re-grants the workspace's
    artifacts."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, BEARER_SECRET)
    client, blob = artifact_client
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/report.txt"
    workspace_id = await _seed_shared_artifact(key, "report.txt")
    with ws(workspace_id):
        await blob.put(key, b"the bytes")
    expired = mint_artifact_url(
        SECRET, key, int(datetime.now(UTC).timestamp()) - 10, workspace_id=workspace_id
    )
    session = _session_cookie(workspace_id)
    assert (await client.get(expired, headers=session)).status_code == 303
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.member)
                .where(tables.member.c.workspace_id == workspace_id)
                .values(seated_at=None, updated_at=sa.func.now())
            )
    revoked = await client.get(expired, headers=session)
    assert revoked.status_code == 403
    assert revoked.json()["detail"] == EXPIRED_DETAIL


async def test_an_expired_url_sends_an_unauthorized_browser_to_sign_in_with_its_target(
    db: None,
    artifact_client: tuple[AsyncClient, WorkspaceBlobStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, BEARER_SECRET)
    client, blob = artifact_client
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/report.txt"
    workspace_id = await _seed_shared_artifact(key, "report.txt")
    with ws(workspace_id):
        await blob.put(key, b"the bytes")
    expired = mint_artifact_url(
        SECRET, key, int(datetime.now(UTC).timestamp()) - 10, workspace_id=workspace_id
    )
    browser = {"accept": "text/html,application/xhtml+xml"}
    sessionless = await client.get(expired, headers=browser)
    assert sessionless.status_code == 303
    location = sessionless.headers["location"]
    assert location.startswith(f"{SIGN_IN_PATH}?a=")
    assert unquote(location.removeprefix(f"{SIGN_IN_PATH}?a=")) == expired
    signed_in = await client.get(expired, headers={**browser, **_session_cookie(workspace_id)})
    assert signed_in.status_code == 303
    assert signed_in.headers["location"].startswith(f"/{ARTIFACT_KEY_PREFIX}")
    artifact_id, filename, exp, workspace, sig = _parts(expired)
    tampered = await client.get(
        f"/{ARTIFACT_KEY_PREFIX}{artifact_id}/{filename}?exp={exp}&ws={workspace}&sig={sig[:-2]}xx",
        headers={**browser, **_session_cookie(workspace_id)},
    )
    assert tampered.status_code == 403


async def test_a_deploy_with_no_browser_sign_in_refuses_an_expired_link_without_redirecting(
    db: None, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, BEARER_SECRET)
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path))
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/report.txt"
    workspace_id = await _seed_shared_artifact(key, "report.txt")
    with ws(workspace_id):
        await blob.put(key, b"the bytes")
    expired = mint_artifact_url(
        SECRET, key, int(datetime.now(UTC).timestamp()) - 10, workspace_id=workspace_id
    )
    browser = {"accept": "text/html,application/xhtml+xml"}
    async with _client(blob, None) as client:
        sessionless = await client.get(expired, headers=browser)
        member = await client.get(expired, headers={**browser, **_session_cookie(workspace_id)})
    assert sessionless.status_code == 403
    assert "location" not in sessionless.headers
    assert sessionless.json()["detail"] == EXPIRED_DETAIL
    assert member.status_code == 303
    assert member.headers["location"].startswith(f"/{ARTIFACT_KEY_PREFIX}")

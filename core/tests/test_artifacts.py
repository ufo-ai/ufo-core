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

from ufo.artifact_url import (
    ARTIFACT_KEY_PREFIX,
    ArtifactUrlError,
    ArtifactUrlExpired,
    mint_artifact_url,
    verify_artifact_url,
)
from ufo.bearer import LOGIN_PATH, SESSION_COOKIE, UFO_TOKEN_SECRET_ENV, mint_token
from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.image_previews import IMAGE_PREVIEW_MAX_BYTES, ImagePreviewGrant
from ufo.schema import tables
from ufo.surfaces.artifacts import EXPIRED_DETAIL
from ufo.surfaces.artifacts import router as artifacts_router

SECRET = "artifact-signing-secret"
BEARER_SECRET = "bearer-signing-secret"
MEMBER_EMAIL = "member@example.com"


def _future() -> int:
    return int(datetime.now(UTC).timestamp()) + 3600


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), (12, 34, 56)).save(output, format="PNG")
    return output.getvalue()


def _parts(url: str) -> tuple[str, str, str, str]:
    """(artifact_id, filename, exp, sig) of a minted URL — path is identity, query is the grant."""
    split = urlsplit(url)
    artifact_id, filename = split.path.removeprefix(f"/{ARTIFACT_KEY_PREFIX}").split("/")
    query = parse_qs(split.query)
    return artifact_id, unquote(filename), query["exp"][0], query["sig"][0]


@pytest.fixture
async def artifact_client(
    tmp_path,
) -> AsyncIterator[tuple[AsyncClient, FilesystemBlobStore]]:
    blob = FilesystemBlobStore(root=tmp_path)
    app = FastAPI()
    app.state.blob = blob
    app.state.artifact_token_secret = SECRET
    app.include_router(artifacts_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://core") as client:
        yield client, blob


def test_mint_and_verify_agree_on_a_round_trip() -> None:
    now = datetime.now(UTC)
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/report.txt"
    expires = int(now.timestamp()) + 100
    url = mint_artifact_url(SECRET, key, expires)
    assert urlsplit(url).path.removeprefix("/") == key
    artifact_id, filename, exp, sig = _parts(url)
    claims = verify_artifact_url(SECRET, artifact_id, filename, exp, sig, "", now)
    assert claims.blob_key == key
    assert claims.filename == "report.txt"
    assert claims.expires_at == expires
    assert claims.preview is None


def test_a_preview_grant_rides_the_query_inside_the_signature() -> None:
    now = datetime.now(UTC)
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/chart.png"
    grant = ImagePreviewGrant(media_type="image/png", size_bytes=64)
    url = mint_artifact_url(SECRET, key, int(now.timestamp()) + 100, preview=grant)
    artifact_id, filename, exp, sig = _parts(url)
    preview = parse_qs(urlsplit(url).query)["preview"][0]
    assert verify_artifact_url(SECRET, artifact_id, filename, exp, sig, preview, now).preview == (
        grant
    )
    with pytest.raises(ArtifactUrlError):
        verify_artifact_url(SECRET, artifact_id, filename, exp, sig, "", now)
    with pytest.raises(ArtifactUrlError):
        verify_artifact_url(SECRET, artifact_id, filename, exp, sig, "image/png:65", now)
    with pytest.raises(ArtifactUrlError):
        mint_artifact_url(
            SECRET,
            key,
            _future(),
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
            mint_artifact_url(SECRET, key, _future())
    with pytest.raises(ArtifactUrlError):
        mint_artifact_url("", f"artifacts/{uuid4()}/report.txt", _future())


def test_verify_rejects_a_tampered_or_stale_grant() -> None:
    now = datetime.now(UTC)
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/report.txt"
    artifact_id, filename, exp, sig = _parts(
        mint_artifact_url(SECRET, key, int(now.timestamp()) + 100)
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
                secret, tampered_id, tampered_name, tampered_exp, tampered_sig, "", now
            )
    with pytest.raises(ArtifactUrlError):
        verify_artifact_url(SECRET, artifact_id, filename, exp, sig, "image/png:9", now)


def test_verify_raises_expired_carrying_the_authentic_claims() -> None:
    """Expiry is a fact about time, not authenticity: the signature already verified, so the
    exception carries the claims for a caller holding another proof of access."""
    now = datetime.now(UTC)
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/report.txt"
    artifact_id, filename, exp, sig = _parts(
        mint_artifact_url(SECRET, key, int(now.timestamp()) - 1)
    )
    with pytest.raises(ArtifactUrlExpired) as caught:
        verify_artifact_url(SECRET, artifact_id, filename, exp, sig, "", now)
    assert isinstance(caught.value, ArtifactUrlError)
    assert caught.value.claims.blob_key == key


def test_a_renamed_filename_verifies_but_addresses_a_different_blob() -> None:
    """The filename rides outside the signature: renaming it re-points the claims at a blob that
    does not exist (each artifact id holds exactly one file), so the route answers 404 rather than
    serving true bytes under a false name."""
    now = datetime.now(UTC)
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/report.txt"
    artifact_id, _, exp, sig = _parts(mint_artifact_url(SECRET, key, int(now.timestamp()) + 100))
    claims = verify_artifact_url(SECRET, artifact_id, "renamed.exe", exp, sig, "", now)
    assert claims.blob_key != key
    assert claims.blob_key.endswith("/renamed.exe")


@pytest.mark.parametrize(
    "filename,media_type",
    [
        ("report.pdf", "application/pdf"),
        ("data.csv", "text/csv"),
        ("photo.png", "image/png"),
        ("file.qzx", "application/octet-stream"),
        ("chart.png.gz", "application/octet-stream"),
    ],
)
async def test_artifact_download_serves_attachments_under_their_real_type(
    artifact_client: tuple[AsyncClient, FilesystemBlobStore], filename: str, media_type: str
) -> None:
    """A plain grant always downloads — under the type the filename names, so a consumer sees
    `application/pdf` rather than octet-stream — and only a signed preview claim renders inline.
    An unknown extension has no type to declare and a compressed name (`.png.gz`) must not claim
    the inner type without its encoding; both fall back to octet-stream."""
    client, blob = artifact_client
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/{filename}"
    await blob.put(key, b"the bytes")
    response = await client.get(mint_artifact_url(SECRET, key, _future()))
    assert response.status_code == 200
    assert response.content == b"the bytes"
    assert response.headers["content-type"].startswith(media_type)
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-store"


async def test_artifact_preview_serves_a_validated_raster_inline(
    artifact_client: tuple[AsyncClient, FilesystemBlobStore],
) -> None:
    client, blob = artifact_client
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/chart.png"
    raster = _png()
    await blob.put(key, raster)
    grant = ImagePreviewGrant(media_type="image/png", size_bytes=len(raster))
    shown = await client.get(mint_artifact_url(SECRET, key, _future(), preview=grant))
    assert shown.status_code == 200
    assert shown.content == raster
    assert shown.headers["content-type"] == "image/png"
    assert shown.headers["x-content-type-options"] == "nosniff"
    assert shown.headers["cache-control"] == "private, no-store"
    assert "content-disposition" not in shown.headers


async def test_artifact_preview_rejects_mislabeled_invalid_and_unsigned_claims(
    artifact_client: tuple[AsyncClient, FilesystemBlobStore],
) -> None:
    """The preview claim is signed and the bytes must prove it: a mislabeled type, a non-image, a
    wrong size, and a claim pasted onto a plain grant all refuse rather than render."""
    client, blob = artifact_client
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/chart.png"
    raster = _png()
    await blob.put(key, raster)
    mislabeled_key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/chart.jpg"
    await blob.put(mislabeled_key, raster)
    invalid_key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/chart.png"
    await blob.put(invalid_key, b"not an image")

    mislabeled = await client.get(
        mint_artifact_url(
            SECRET,
            mislabeled_key,
            _future(),
            preview=ImagePreviewGrant(media_type="image/jpeg", size_bytes=len(raster)),
        )
    )
    invalid = await client.get(
        mint_artifact_url(
            SECRET,
            invalid_key,
            _future(),
            preview=ImagePreviewGrant(media_type="image/png", size_bytes=len(b"not an image")),
        )
    )
    wrong_size = await client.get(
        mint_artifact_url(
            SECRET,
            key,
            _future(),
            preview=ImagePreviewGrant(media_type="image/png", size_bytes=len(raster) - 1),
        )
    )
    pasted = await client.get(
        mint_artifact_url(SECRET, key, _future()) + f"&preview=image/png:{len(raster)}"
    )
    assert mislabeled.status_code == 415
    assert invalid.status_code == 415
    assert wrong_size.status_code == 415
    assert pasted.status_code == 403


async def test_artifact_download_escapes_special_filenames(
    artifact_client: tuple[AsyncClient, FilesystemBlobStore],
) -> None:
    """A `"` would break the quoted `filename=` and a unicode name is neither URL- nor header-safe;
    both must ride the URL out and back as a well-formed Content-Disposition that still serves."""
    client, blob = artifact_client
    for filename in ('a"quote.txt', "résumé pièce.txt"):
        key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/{filename}"
        await blob.put(key, b"the bytes")
        response = await client.get(mint_artifact_url(SECRET, key, _future()))
        assert response.status_code == 200
        assert response.content == b"the bytes"
        parsed = Message()
        parsed["content-disposition"] = response.headers["content-disposition"]
        assert parsed.get_content_disposition() == "attachment"
        assert parsed.get_filename() == filename


async def test_artifact_download_rejects_a_tampered_stale_or_missing_grant(
    artifact_client: tuple[AsyncClient, FilesystemBlobStore],
) -> None:
    client, blob = artifact_client
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/report.txt"
    await blob.put(key, b"x")
    artifact_id, filename, exp, sig = _parts(mint_artifact_url(SECRET, key, _future()))
    path = f"/{ARTIFACT_KEY_PREFIX}{artifact_id}/{filename}"
    tampered = await client.get(f"{path}?exp={exp}&sig={sig[:-2]}xx")
    expired = await client.get(
        mint_artifact_url(SECRET, key, int(datetime.now(UTC).timestamp()) - 10)
    )
    grantless = await client.get(path)
    bare = await client.get(f"/{ARTIFACT_KEY_PREFIX}{artifact_id}")
    assert tampered.status_code == 403
    assert expired.status_code == 403
    assert grantless.status_code == 403
    assert bare.status_code == 404


async def test_artifact_download_404s_a_renamed_or_absent_blob(
    artifact_client: tuple[AsyncClient, FilesystemBlobStore],
) -> None:
    """A well-formed URL whose blob is gone (reaped, or re-pointed by a renamed filename) is a 404
    decided up front, before the stream opens."""
    client, blob = artifact_client
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/report.txt"
    await blob.put(key, b"x")
    artifact_id, _, exp, sig = _parts(mint_artifact_url(SECRET, key, _future()))
    renamed = await client.get(
        f"/{ARTIFACT_KEY_PREFIX}{artifact_id}/renamed.exe?exp={exp}&sig={sig}"
    )
    absent = await client.get(
        mint_artifact_url(SECRET, f"{ARTIFACT_KEY_PREFIX}{uuid4()}/gone.txt", _future())
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
    artifact_client: tuple[AsyncClient, FilesystemBlobStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The path is the artifact's identity, the query its grant: for a signed-in member of the
    owning workspace an expired grant refreshes in place — same path, fresh `exp`/`sig` — and the
    redirected URL then serves with no session at all, like any live link."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, BEARER_SECRET)
    client, blob = artifact_client
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/report.txt"
    await blob.put(key, b"the bytes")
    workspace_id = await _seed_shared_artifact(key, "report.txt")
    expired = mint_artifact_url(SECRET, key, int(datetime.now(UTC).timestamp()) - 10)
    response = await client.get(expired, headers=_session_cookie(workspace_id))
    assert response.status_code == 303
    fresh = response.headers["location"]
    assert urlsplit(fresh).path == urlsplit(expired).path
    followed = await client.get(fresh)
    assert followed.status_code == 200
    assert followed.content == b"the bytes"


async def test_an_expired_url_refuses_a_missing_foreign_or_unmembered_session(
    db: None,
    artifact_client: tuple[AsyncClient, FilesystemBlobStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, BEARER_SECRET)
    client, blob = artifact_client
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/report.txt"
    await blob.put(key, b"the bytes")
    workspace_id = await _seed_shared_artifact(key, "report.txt")
    expired = mint_artifact_url(SECRET, key, int(datetime.now(UTC).timestamp()) - 10)
    no_cookie = await client.get(expired)
    foreign = await client.get(expired, headers=_session_cookie(uuid4()))
    unmembered = await client.get(
        expired, headers=_session_cookie(workspace_id, email="stranger@example.com")
    )
    garbage = await client.get(expired, headers={"cookie": f"{SESSION_COOKIE}=nonsense"})
    for response in (no_cookie, foreign, unmembered, garbage):
        assert response.status_code == 403
        assert response.json()["detail"] == EXPIRED_DETAIL


async def test_an_expired_url_sends_an_unauthorized_browser_to_sign_in_with_its_target(
    db: None,
    artifact_client: tuple[AsyncClient, FilesystemBlobStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A browser can recover from an expired link, so a refused navigation request lands on the
    sign-in page carrying the link as its target — signing in returns to the link and the grant
    refreshes. Every other client keeps the 403 that names the next step, and a tampered grant
    earns no sign-in funnel at all."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, BEARER_SECRET)
    client, blob = artifact_client
    key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/report.txt"
    await blob.put(key, b"the bytes")
    workspace_id = await _seed_shared_artifact(key, "report.txt")
    expired = mint_artifact_url(SECRET, key, int(datetime.now(UTC).timestamp()) - 10)
    browser = {"accept": "text/html,application/xhtml+xml"}
    sessionless = await client.get(expired, headers=browser)
    assert sessionless.status_code == 303
    location = sessionless.headers["location"]
    assert location.startswith(f"{LOGIN_PATH}?a=")
    assert unquote(location.removeprefix(f"{LOGIN_PATH}?a=")) == expired
    signed_in = await client.get(expired, headers={**browser, **_session_cookie(workspace_id)})
    assert signed_in.status_code == 303
    assert signed_in.headers["location"].startswith(f"/{ARTIFACT_KEY_PREFIX}")
    artifact_id, filename, exp, sig = _parts(expired)
    tampered = await client.get(
        f"/{ARTIFACT_KEY_PREFIX}{artifact_id}/{filename}?exp={exp}&sig={sig[:-2]}xx",
        headers={**browser, **_session_cookie(workspace_id)},
    )
    assert tampered.status_code == 403

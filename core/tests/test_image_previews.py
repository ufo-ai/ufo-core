from collections.abc import AsyncIterator
from io import BytesIO

import pytest
from PIL import GifImagePlugin, Image

import ufo.image_previews as image_previews
from ufo.image_previews import ImagePreviewGrant, InvalidImagePreview, validated_image_preview


def _image(format: str, *, size: tuple[int, int] = (4, 4)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, "red").save(output, format=format)
    return output.getvalue()


def _animation(frame_count: int, *, size: tuple[int, int] = (2, 2)) -> bytes:
    frames = [Image.new("RGB", size, (index % 256, 0, 0)) for index in range(frame_count)]
    output = BytesIO()
    frames[0].save(
        output,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=10,
        loop=0,
        optimize=False,
    )
    return output.getvalue()


async def _stream(data: bytes) -> AsyncIterator[bytes]:
    yield data


@pytest.mark.parametrize(
    ("format", "media_type"),
    [
        ("JPEG", "image/jpeg"),
        ("GIF", "image/gif"),
        ("PNG", "image/png"),
        ("WEBP", "image/webp"),
    ],
)
async def test_image_preview_rejects_truncated_raster_containers(
    format: str, media_type: image_previews.RasterImageMediaType
) -> None:
    truncated = _image(format)[:-1]

    with pytest.raises(InvalidImagePreview):
        await validated_image_preview(
            _stream(truncated), ImagePreviewGrant(media_type, len(truncated))
        )


async def test_image_preview_normalizes_malformed_png_parser_failures() -> None:
    malformed = bytearray(_image("PNG"))
    chunk_type = malformed.index(b"IDAT")
    chunk_size = int.from_bytes(malformed[chunk_type - 4 : chunk_type], "big")
    malformed[chunk_type + 4 + chunk_size] ^= 1
    data = bytes(malformed)

    with pytest.raises(InvalidImagePreview):
        await validated_image_preview(_stream(data), ImagePreviewGrant("image/png", len(data)))


async def test_image_preview_decodes_every_animation_frame() -> None:
    data = _animation(3)[:-6] + b";"
    with Image.open(BytesIO(data)) as image:
        image.verify()

    with pytest.raises(InvalidImagePreview):
        await validated_image_preview(_stream(data), ImagePreviewGrant("image/gif", len(data)))


async def test_image_preview_bounds_animation_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    data = _animation(3)
    monkeypatch.setattr(image_previews, "IMAGE_PREVIEW_MAX_FRAMES", 2)

    with pytest.raises(InvalidImagePreview):
        await validated_image_preview(_stream(data), ImagePreviewGrant("image/gif", len(data)))


async def test_image_preview_stops_seeking_after_the_frame_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _animation(image_previews.IMAGE_PREVIEW_MAX_FRAMES + 1, size=(1, 1))
    sought: list[int] = []
    original_seek = GifImagePlugin.GifImageFile.seek

    def tracked_seek(image: GifImagePlugin.GifImageFile, frame: int) -> None:
        sought.append(frame)
        original_seek(image, frame)

    monkeypatch.setattr(GifImagePlugin.GifImageFile, "seek", tracked_seek)

    with pytest.raises(InvalidImagePreview):
        await validated_image_preview(_stream(data), ImagePreviewGrant("image/gif", len(data)))
    assert max(sought) == image_previews.IMAGE_PREVIEW_MAX_FRAMES


async def test_image_preview_bounds_dimensions(monkeypatch: pytest.MonkeyPatch) -> None:
    data = _image("PNG", size=(3, 2))
    monkeypatch.setattr(image_previews, "IMAGE_PREVIEW_MAX_DIMENSION", 2)

    with pytest.raises(InvalidImagePreview):
        await validated_image_preview(_stream(data), ImagePreviewGrant("image/png", len(data)))


async def test_image_preview_bounds_aggregate_decoded_pixels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _animation(2)
    monkeypatch.setattr(image_previews, "IMAGE_PREVIEW_MAX_DECODED_PIXELS", 7)

    with pytest.raises(InvalidImagePreview):
        await validated_image_preview(_stream(data), ImagePreviewGrant("image/gif", len(data)))

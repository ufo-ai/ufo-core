import asyncio
import struct
import warnings
from collections.abc import AsyncIterator
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from typing import Literal

from PIL import Image

RasterImageMediaType = Literal["image/gif", "image/jpeg", "image/png", "image/webp"]

IMAGE_PREVIEW_MAX_BYTES = 20 * 1024 * 1024
IMAGE_PREVIEW_MAX_DIMENSION = 16_384
IMAGE_PREVIEW_MAX_FRAMES = 256
IMAGE_PREVIEW_MAX_DECODED_PIXELS = 64_000_000
IMAGE_FORMAT_MEDIA_TYPES: dict[str, RasterImageMediaType] = {
    "GIF": "image/gif",
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}
RASTER_IMAGE_SUFFIXES: dict[str, RasterImageMediaType] = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


class InvalidImagePreview(ValueError):
    pass


@dataclass(frozen=True)
class ImagePreviewGrant:
    media_type: RasterImageMediaType
    size_bytes: int


def raster_image_media_type(path: str) -> RasterImageMediaType | None:
    return RASTER_IMAGE_SUFFIXES.get(PurePosixPath(path).suffix.lower())


async def validated_image_preview(stream: AsyncIterator[bytes], grant: ImagePreviewGrant) -> bytes:
    if grant.size_bytes < 0 or grant.size_bytes > IMAGE_PREVIEW_MAX_BYTES:
        raise InvalidImagePreview("image preview exceeds the byte limit")
    chunks: list[bytes] = []
    size_bytes = 0
    async for chunk in stream:
        size_bytes += len(chunk)
        if size_bytes > grant.size_bytes or size_bytes > IMAGE_PREVIEW_MAX_BYTES:
            raise InvalidImagePreview("image preview size differs from its signed claim")
        chunks.append(chunk)
    if size_bytes != grant.size_bytes:
        raise InvalidImagePreview("image preview size differs from its signed claim")
    data = b"".join(chunks)
    await asyncio.to_thread(_ImagePreviewValidator.validate, data, grant.media_type)
    return data


class _ImagePreviewValidator:
    @staticmethod
    def validate(data: bytes, media_type: RasterImageMediaType) -> None:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                _ImagePreviewValidator._validate_container(data, media_type)
                with Image.open(BytesIO(data)) as image:
                    actual_media_type = IMAGE_FORMAT_MEDIA_TYPES.get(image.format or "")
                    image.verify()
                with Image.open(BytesIO(data)) as image:
                    decoded_pixels = 0
                    for frame_index in range(IMAGE_PREVIEW_MAX_FRAMES + 1):
                        try:
                            image.seek(frame_index)
                        except EOFError:
                            break
                        if frame_index == IMAGE_PREVIEW_MAX_FRAMES:
                            raise InvalidImagePreview("image preview has too many frames")
                        width, height = image.size
                        if (
                            width <= 0
                            or height <= 0
                            or width > IMAGE_PREVIEW_MAX_DIMENSION
                            or height > IMAGE_PREVIEW_MAX_DIMENSION
                        ):
                            raise InvalidImagePreview("image preview dimensions exceed the limit")
                        decoded_pixels += width * height
                        if decoded_pixels > IMAGE_PREVIEW_MAX_DECODED_PIXELS:
                            raise InvalidImagePreview(
                                "image preview decoded pixels exceed the limit"
                            )
                        image.load()
        except (
            EOFError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            IndexError,
            OSError,
            SyntaxError,
            ValueError,
            struct.error,
        ) as error:
            raise InvalidImagePreview("image preview bytes are invalid") from error
        if actual_media_type != media_type:
            raise InvalidImagePreview("image preview media type differs from its signed claim")

    @staticmethod
    def _validate_container(data: bytes, media_type: RasterImageMediaType) -> None:
        if media_type == "image/jpeg" and not data.endswith(b"\xff\xd9"):
            raise InvalidImagePreview("image preview JPEG is incomplete")
        if media_type == "image/gif" and not data.endswith(b";"):
            raise InvalidImagePreview("image preview GIF is incomplete")
        if media_type == "image/png" and not data.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82"):
            raise InvalidImagePreview("image preview PNG is incomplete")
        if media_type == "image/webp" and (
            len(data) < 12
            or data[:4] != b"RIFF"
            or data[8:12] != b"WEBP"
            or int.from_bytes(data[4:8], "little") + 8 != len(data)
        ):
            raise InvalidImagePreview("image preview WebP is incomplete")

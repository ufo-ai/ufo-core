import base64

import pytest

from ufo.host.tools.builtins import _document_result
from ufo.runtime.tools.context import ImageContent, TextContent

PNG = b"\x89PNG\r\n\x1a\nrendered"


@pytest.mark.parametrize(
    ("kind", "unit"),
    (("pdf", "pages"), ("pptx", "slides"), ("docx", "pages"), ("xlsx", "pages")),
)
def test_document_result_keeps_text_pagination_and_image_blocks(kind: str, unit: str) -> None:
    result = _document_result(
        {
            "path": f"/workspace/fixture.{kind}",
            "type": kind,
            "text": "visible page two",
            "total_pages": 3,
            "start_page": 2,
            "pages_returned": 1,
            "next_page": 3,
            "pages": [
                {
                    "page": 2,
                    "width": 640,
                    "height": 480,
                    "media_type": "image/png",
                    "data": base64.b64encode(PNG).decode(),
                }
            ],
            "quality_reminder": "inspect the rendered page",
        }
    )
    text, image = result.content
    assert isinstance(text, TextContent)
    assert text.text == (
        f"visible page two\n\n[{kind} {unit} 2-2 of 3]; more {unit} - read with offset=3\n\n"
        "inspect the rendered page"
    )
    assert isinstance(image, ImageContent)
    assert image.media_type == "image/png"
    assert base64.b64decode(image.data) == PNG

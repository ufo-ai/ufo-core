import json

from core.tests.knowledge.model_read_text import PIN, collect, render


def test_model_read_text_is_unchanged() -> None:
    pinned = json.loads(PIN.read_text(encoding="utf-8"))
    current = json.loads(render(collect()))
    moved = sorted(
        f"{group}.{key}"
        for group in pinned.keys() | current.keys()
        for key in pinned.get(group, {}).keys() | current.get(group, {}).keys()
        if pinned.get(group, {}).get(key) != current.get(group, {}).get(key)
    )
    assert not moved, f"Model-read text moved: {', '.join(moved)}."

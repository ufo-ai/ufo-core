"""The structured question result shared by model-authored asks and runtime permission asks."""

import json

from ufo.schema.records import AskUserInput

ASK_USER_DIRECTIVE = (
    "Ask these in your reply, then end your turn — the user's answer arrives as the next message."
)


def question_result_text(question: AskUserInput) -> str:
    """Render one terminal question as the tool-result directive the turn loop recognizes."""
    payload = {
        "awaiting": "question",
        "title": question.title,
        **({"icon": question.icon} if question.icon else {}),
        "questions": [item.model_dump(exclude_none=True) for item in question.questions],
    }
    return f"{ASK_USER_DIRECTIVE}\n{json.dumps(payload)}"

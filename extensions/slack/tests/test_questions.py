import json
from uuid import uuid4

import pytest
from ufo_ext_slack import surface as slack

from ufo.sdk.surfaces import AskQuestion, AskUserInput, QuestionOption


@pytest.mark.parametrize("length", [74, 75, 76, 2_000, 3_001, 8_000])
@pytest.mark.parametrize("multi_select", [False, True])
def test_option_descriptions_reach_the_member_whole(length: int, multi_select: bool) -> None:
    description = ("Keep the decision and its conditions. " * length)[:length]
    question = AskUserInput(
        title="Report scope",
        questions=(
            AskQuestion(
                question="Where should the cut fall?",
                multi_select=multi_select,
                chosen="Keep universe",
                options=(
                    QuestionOption(label="Keep universe", description=description),
                    QuestionOption(label="Direct rivals", description="Track direct rivals only."),
                ),
            ),
        ),
    )
    blocks = slack.slack_ask_blocks(question)
    assert blocks is not None
    visible = "".join(block["text"]["text"] for block in blocks if block["type"] == "section")
    [control] = [block for block in blocks if block["type"] == "input"]
    picker = control["element"]
    option = picker["options"][0]
    visible += option.get("description", {}).get("text", "")
    assert description in visible
    assert len(option.get("description", {}).get("text", "")) <= 75
    assert picker["initial_options" if multi_select else "initial_option"] == (
        [option] if multi_select else option
    )
    state = {
        "values": {
            "ask:0": {
                "ask:0": {
                    "type": picker["type"],
                    "selected_options" if multi_select else "selected_option": (
                        [option] if multi_select else option
                    ),
                }
            }
        }
    }
    [answer] = slack._submitted_answers(tuple(blocks), state)
    assert answer.answer == "Keep universe"
    assert answer.question == "Where should the cut fall?"
    assert all(
        len(block["text"]["text"]) <= 3_000 for block in blocks if block["type"] == "section"
    )


def test_long_question_title_and_hint_survive_render_and_submit() -> None:
    title = "Report scope " * 300
    label = "Read every condition before approving. " * 160
    suggestion = "Include the full retention conditions. " * 120
    question = AskUserInput(
        title=title,
        questions=(
            AskQuestion(
                header="Approval",
                question=label,
                free_text_only=True,
                options=(QuestionOption(label="Conditions", description=suggestion),),
            ),
        ),
    )
    blocks = slack.slack_ask_blocks(question)
    assert blocks is not None
    text = "".join(block["text"]["text"] for block in blocks if block["type"] == "section")
    assert title in text
    assert label in text
    assert suggestion in text
    [control] = [block for block in blocks if block["type"] == "input"]
    assert len(control["label"]["text"]) <= 2_000
    state = {"values": {"ask:0": {"ask:0": {"type": "plain_text_input", "value": "Hold"}}}}
    [answer] = slack._submitted_answers(tuple(blocks), state)
    assert answer.question == f"Approval — {label}"
    assert answer.answer == "Hold"


def test_prose_questions_preserve_text_past_a_section_boundary() -> None:
    description = "Every condition is required. " * 200
    question = AskUserInput(
        title="Files",
        questions=(
            AskQuestion(
                question="Which file?",
                allow_attachments=True,
                options=(QuestionOption(label="Full report", description=description),),
            ),
        ),
    )
    blocks = slack.slack_ask_blocks(question)
    assert blocks is not None
    text = "".join(block["text"]["text"] for block in blocks)
    assert description in text
    assert slack.ASK_PROSE_HINT in text


@pytest.mark.parametrize("sections", [False, True])
def test_reply_body_never_silently_drops_an_oversized_question(sections: bool) -> None:
    actions = [{"type": "section", "text": {"type": "plain_text", "text": "界" * 3_000}}] * 20
    with pytest.raises(ValueError, match=r"Slack.*(large|limit)"):
        slack.slack_reply_body("C1", None, "Choose.", None, actions=actions, sections=sections)


def test_reply_body_refuses_more_than_fifty_blocks() -> None:
    actions = [{"type": "section", "text": {"type": "plain_text", "text": "Choice"}}] * 50
    with pytest.raises(ValueError, match=r"Slack.*(blocks|limit)"):
        slack.slack_reply_body("C1", None, "Choose.", None, actions=actions)


def test_short_question_keeps_native_description() -> None:
    question = AskUserInput(
        title="Scope",
        questions=(
            AskQuestion(
                question="Which scope?",
                options=(
                    QuestionOption(label="Narrow", description="Direct rivals."),
                    QuestionOption(label="Broad"),
                ),
            ),
        ),
    )
    blocks = slack.slack_ask_blocks(question)
    body = json.loads(slack.slack_reply_body("C1", None, "Choose.", None, actions=blocks))
    assert body["blocks"][2]["element"]["options"][0]["description"]["text"] == "Direct rivals."


@pytest.mark.parametrize("character", ["a", "界", "🚀"])
@pytest.mark.parametrize("length", [76, 3_001])
def test_public_question_overflow_preserves_all_text(character: str, length: int) -> None:
    question = AskUserInput(
        title="Report scope",
        questions=tuple(
            AskQuestion(
                header=f"Scope {index}",
                question=f"Which scope applies to report {index}?",
                chosen=f"Choice {index}:0",
                options=tuple(
                    QuestionOption(
                        label=f"Choice {index}:{option}",
                        description=f"Description {index}:{option} {character * length}",
                    )
                    for option in range(10)
                ),
            )
            for index in range(4)
        ),
    )
    connect = {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Connect account"},
                "action_id": "connect-account",
                "url": "https://example.com/connect",
            }
        ],
    }
    parts, actions = slack.slack_public_question_reply(
        "C1", "123.456", "Choose the report scope.", "Usage: 1", question, [connect]
    )
    visible = "".join(parts)
    assert "Choose the report scope." in visible
    assert question.title in visible
    assert slack.ASK_PROSE_HINT in visible
    for ask in question.questions:
        assert ask.header in visible
        assert ask.question in visible
        for option in ask.options:
            assert option.label in visible
            assert option.description in visible
    assert actions == [connect]
    for sections in (False, True):
        bodies = [
            slack.slack_reply_body(
                "C1",
                "123.456",
                part,
                "Usage: 1" if index == len(parts) - 1 else None,
                actions=actions if index == len(parts) - 1 else None,
                sections=sections,
            )
            for index, part in enumerate(parts)
        ]
        messages = [json.loads(body) for body in bodies]
        assert all(message["channel"] == "C1" for message in messages)
        assert all(message["thread_ts"] == "123.456" for message in messages)
        assert all(len(message["blocks"]) <= 50 for message in messages)
        assert all(len(body) <= slack.MAX_SLACK_BLOCK_MESSAGE_BYTES for body in bodies)
        blocks = [block for message in messages for block in message["blocks"]]
        assert sum(block["type"] == "context" for block in blocks) == 1
        assert sum(block == connect for block in blocks) == 1
        assert not any(block["type"] == "input" for block in blocks)


@pytest.mark.parametrize("character", ["a", "界", "🚀"])
def test_public_question_overflow_preserves_large_title_question_and_hint(character: str) -> None:
    question = AskUserInput(
        title=f"Full title {character * 24_000}",
        questions=(
            AskQuestion(
                header="Conditions",
                question=f"Full question {character * 48_000}",
                free_text_only=True,
                options=(
                    QuestionOption(
                        label="Written conditions",
                        description=f"Full hint {character * 72_000}",
                    ),
                ),
            ),
        ),
    )
    parts, actions = slack.slack_public_question_reply("C1", None, "Choose.", None, question)
    visible = "".join(parts)
    assert question.title in visible
    assert question.questions[0].question in visible
    assert question.questions[0].options[0].description in visible
    assert slack.ASK_PROSE_HINT in visible
    assert actions is None
    for sections in (False, True):
        for part in parts:
            body = slack.slack_reply_body("C1", None, part, None, sections=sections)
            message = json.loads(body)
            assert len(body) <= slack.MAX_SLACK_MESSAGE_BYTES
            assert len(message["blocks"]) <= 50


def test_public_question_that_fits_keeps_native_controls() -> None:
    question = AskUserInput(
        title="Scope",
        questions=(
            AskQuestion(
                question="Which scope?",
                chosen="Narrow",
                options=(QuestionOption(label="Narrow"), QuestionOption(label="Broad")),
            ),
        ),
    )
    parts, actions = slack.slack_public_question_reply("C1", None, "Choose.", "Usage: 1", question)
    assert parts == ("Choose.", "Submit your answers below.")
    assert actions == slack.slack_ask_blocks(question)
    [control] = [block for block in actions if block["type"] == "input"]
    assert control["element"]["initial_option"]["value"] == "Narrow"


def test_public_question_at_fifty_blocks_keeps_native_controls() -> None:
    question = AskUserInput(
        title="t" * 6_001,
        questions=tuple(
            AskQuestion(
                question=f"Which scope applies to report {index}?",
                options=tuple(
                    QuestionOption(label=f"Choice {option}", description="d" * 76)
                    for option in range(9)
                ),
            )
            for index in range(4)
        ),
    )
    parts, actions = slack.slack_public_question_reply("C1", None, "Choose.", "Usage: 1", question)
    assert parts == ("Choose.", "Submit your answers below.")
    assert actions == slack.slack_ask_blocks(question)
    for sections in (False, True):
        body = slack.slack_reply_body(
            "C1", None, parts[-1], "Usage: 1", actions=actions, sections=sections
        )
        assert len(json.loads(body)["blocks"]) == 50


def test_public_question_overflow_keeps_existing_reply_parts() -> None:
    text = "r" * 25_000
    question = AskUserInput(
        title="Report scope",
        questions=tuple(
            AskQuestion(
                question=f"Which report {index}?",
                options=tuple(
                    QuestionOption(label=f"Choice {option}", description="d" * 200)
                    for option in range(10)
                ),
            )
            for index in range(4)
        ),
    )
    original = tuple(slack.slack_reply_parts(text))
    assert len(original) == 3
    parts, actions = slack.slack_public_question_reply("C1", None, text, None, question)
    assert parts[:3] == original
    assert len(parts) == 4
    assert parts[3].startswith(question.title)
    assert slack.ASK_PROSE_HINT in parts[3]
    assert actions is None


@pytest.mark.parametrize("character", ["a", "界", "🚀"])
@pytest.mark.parametrize("question_length", [40, 12_000])
def test_private_question_overflow_keeps_bound_controls_and_full_text(
    character: str, question_length: int
) -> None:
    member_id = uuid4()
    binding = slack._SlackAskBinding(
        target_member_id=member_id,
        conversation_id=uuid4(),
        queue_key="C1:123.456",
        question_turn_id=uuid4(),
    )
    question = AskUserInput(
        title="Private report scope",
        target_member_id=member_id,
        questions=tuple(
            AskQuestion(
                question=f"Report {index} {character * question_length}",
                chosen=f"Choice {index}:0",
                multi_select=index % 2 == 1,
                options=tuple(
                    QuestionOption(
                        label=f"Choice {index}:{option}",
                        description=f"Description {index}:{option} {character * 200}",
                    )
                    for option in range(10)
                ),
            )
            for index in range(4)
        ),
    )
    parts, actions, stored = slack.slack_private_question_reply("D1", "Choose.", question, binding)
    assert stored is not None
    assert stored.question == question
    assert stored.channel == "D1"
    assert stored.binding == binding.model_copy(update={"stored_question": True})
    visible = "".join(parts)
    assert question.title in visible
    for ask in question.questions:
        assert ask.question in visible
        for option in ask.options:
            assert option.label in visible
            assert option.description in visible
    controls = [block for block in actions if block["type"] == "input"]
    assert len(controls) == 4
    assert [block["block_id"] for block in controls] == [f"ask:{index}" for index in range(4)]
    for index, control in enumerate(controls):
        assert control["label"]["text"] == f"Question {index + 1}"
        element = control["element"]
        selected = element["initial_options"][0] if index % 2 else element["initial_option"]
        assert selected["text"]["text"] == f"Choice {index}:0"
    [submit] = [
        element
        for block in actions
        for element in block.get("elements", [])
        if element.get("action_id") == slack.ASK_SUBMIT_ACTION_ID
    ]
    submitted_binding = slack._SlackAskBinding.model_validate_json(submit["value"])
    assert submitted_binding.stored_question is True
    assert submitted_binding.model_copy(update={"stored_question": False}) == binding
    for sections in (False, True):
        messages = [
            json.loads(
                slack.slack_reply_body(
                    "D1",
                    None,
                    part,
                    None,
                    actions=actions if index == len(parts) - 1 else None,
                    sections=sections,
                )
            )
            for index, part in enumerate(parts)
        ]
        assert all(len(message["blocks"]) <= 50 for message in messages)
        assert all(
            not any(block["type"] in ("input", "actions") for block in message["blocks"])
            for message in messages[:-1]
        )
        assert sum(block["type"] == "input" for block in messages[-1]["blocks"]) == 4


def test_private_question_that_fits_keeps_original_binding() -> None:
    member_id = uuid4()
    binding = slack._SlackAskBinding(
        target_member_id=member_id,
        conversation_id=uuid4(),
        queue_key="C1:123.456",
        question_turn_id=uuid4(),
    )
    question = AskUserInput(
        title="Private scope",
        target_member_id=member_id,
        questions=(AskQuestion(question="Which scope?"),),
    )
    parts, actions, stored = slack.slack_private_question_reply("D1", "Choose.", question, binding)
    assert parts == ("Choose.", "Submit your answers below.")
    assert actions == slack.slack_ask_blocks(question, binding)
    assert stored is None


@pytest.mark.parametrize(
    ("chosen", "title"),
    [(None, "Scope"), ("Keep this answer.", "Scope " * 20_000), ("y" * 3_001, "Scope")],
    ids=["suggestion", "chosen", "oversized-chosen"],
)
def test_private_free_text_keeps_large_suggestions_outside_the_control(
    chosen: str | None,
    title: str,
) -> None:
    member_id = uuid4()
    suggestion = "x" * 3_001
    question = AskUserInput(
        title=title,
        target_member_id=member_id,
        questions=(
            AskQuestion(
                question="Review this proposed answer.",
                free_text_only=True,
                chosen=chosen,
                options=(QuestionOption(label=suggestion),),
            ),
        ),
    )
    binding = slack._SlackAskBinding(
        target_member_id=member_id,
        conversation_id=uuid4(),
        queue_key="C5:200.0",
        question_turn_id=uuid4(),
    )
    parts, actions, stored = slack.slack_private_question_reply("D-U1", "Read.", question, binding)
    assert stored is not None
    assert suggestion in "".join(parts)
    if chosen:
        assert chosen in "".join(parts)
    [control] = [block for block in actions if block["type"] == "input"]
    assert control["element"].get("initial_value") == (
        chosen if chosen is not None and len(chosen) <= 3_000 else None
    )
    assert control["element"]["max_length"] == 3_000
    for index, part in enumerate(parts):
        for sections in (False, True):
            body = slack.slack_reply_body(
                "D-U1",
                None,
                part,
                None,
                actions=actions if index == len(parts) - 1 else None,
                sections=sections,
            )
            assert len(body) <= slack.MAX_SLACK_BLOCK_MESSAGE_BYTES


@pytest.mark.parametrize("text", ["# Heading\n- One\n- Two\n```\ncode\n```", "Details. " * 3_000])
@pytest.mark.parametrize("private", [False, True])
def test_forms_keep_reply_markdown_and_use_a_separate_control_message(
    text: str, private: bool
) -> None:
    question = AskUserInput(title="Scope", questions=(AskQuestion(question="What should change?"),))
    if private:
        member_id = uuid4()
        question = question.model_copy(update={"target_member_id": member_id})
        binding = slack._SlackAskBinding(
            target_member_id=member_id,
            conversation_id=uuid4(),
            queue_key="C1:123.456",
            question_turn_id=uuid4(),
        )
        parts, actions, stored = slack.slack_private_question_reply("D1", text, question, binding)
        assert stored is None
    else:
        parts, actions = slack.slack_public_question_reply("C1", None, text, None, question)
    assert parts[:-1] == tuple(slack.slack_reply_parts(text))
    assert parts[-1] == "Submit your answers below."
    messages = [
        json.loads(
            slack.slack_reply_body(
                "D1" if private else "C1",
                None,
                part,
                None,
                actions=actions if index == len(parts) - 1 else None,
            )
        )
        for index, part in enumerate(parts)
    ]
    assert all(message["blocks"][0]["type"] == "markdown" for message in messages[:-1])
    assert "".join(message["blocks"][0]["text"] for message in messages[:-1]) == text
    assert messages[-1]["blocks"][0] == {
        "type": "section",
        "text": {"type": "mrkdwn", "text": "Submit your answers below."},
    }
    assert messages[-1]["blocks"][1:] == actions


def test_connect_only_reply_keeps_markdown() -> None:
    text = "# Heading\n- One\n- Two\n```\ncode\n```"
    connect = {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Connect account"},
                "action_id": slack.CONNECT_ACTION_ID,
                "value": str(uuid4()),
            }
        ],
    }
    parts, actions = slack.slack_public_question_reply("C1", None, text, None, None, [connect])
    assert parts == (text,)
    assert actions == [connect]
    body = json.loads(slack.slack_reply_body("C1", None, parts[0], None, actions=actions))
    assert body["blocks"] == [{"type": "markdown", "text": text}, connect]


@pytest.mark.parametrize("text", ["<br>", "<br/>", "<BR />", " <response></response> "])
@pytest.mark.parametrize("private", [False, True])
def test_silent_question_posts_only_the_form(text: str, private: bool) -> None:
    question = AskUserInput(
        title="Decision",
        questions=(AskQuestion(question="Proceed?", free_text_only=True),),
        target_member_id=uuid4() if private else None,
    )
    if private:
        binding = slack._SlackAskBinding(
            target_member_id=question.target_member_id,
            conversation_id=uuid4(),
            queue_key="C1:1.0",
            question_turn_id=uuid4(),
        )
        parts, actions, stored = slack.slack_private_question_reply("D1", text, question, binding)
        assert stored is None
    else:
        parts, actions = slack.slack_public_question_reply("C1", "1.0", text, None, question)
    assert parts == (slack.ASK_FORM_TEXT,)
    assert actions is not None
    assert any(block["type"] == "input" for block in actions)


@pytest.mark.parametrize("text", ["<br>", "<br/>", "<BR />", " <response></response> "])
@pytest.mark.parametrize("length", [10, 60_000])
def test_silent_attachment_question_posts_only_question_prose(text: str, length: int) -> None:
    question = AskUserInput(
        title="Files",
        questions=tuple(
            AskQuestion(question=f"File {index}: " + "details " * length, allow_attachments=True)
            for index in range(4)
        ),
    )
    parts, actions = slack.slack_public_question_reply("C1", "1.0", text, None, question)
    assert all(not slack.is_silence_sentinel(part) for part in parts)
    bodies = [
        json.loads(
            slack.slack_reply_body(
                "C1", "1.0", part, None, actions=actions if index == len(parts) - 1 else None
            )
        )
        for index, part in enumerate(parts)
    ]
    visible = "".join(
        block["text"]["text"] if block["type"] == "section" else block["text"]
        for body in bodies
        for block in body["blocks"]
        if block["type"] in {"section", "markdown"}
    )
    assert all(ask.question in visible for ask in question.questions)
    if length == 10:
        assert parts == (slack.ASK_REPLY_TEXT,)
        assert actions is not None
    else:
        assert actions is None

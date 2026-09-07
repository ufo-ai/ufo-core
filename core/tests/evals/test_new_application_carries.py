from evals.suites.new_application import _carries

PROMPT = """You keep the shipping log for one member of this workspace.

Your job: each week, read the pull requests merged in that member's repositories
and turn them into a record a person can read."""


def test_a_rewrapped_prompt_is_still_carried() -> None:
    """The objective quotes the prompt into a longer brief and wraps it at its own columns, while
    the stored prompt keeps the ones its YAML block scalar had. Two recorded runs reproduced the
    whole prompt and failed a raw substring check on the line breaks alone."""

    rewrapped = " ".join(PROMPT.split())
    objective = (
        f"Build and host the homepage.\n\nThe app's prompt, verbatim:\n{rewrapped}\n\nDeploy it."
    )
    assert PROMPT not in objective
    assert _carries(objective, PROMPT)


def test_a_summarised_prompt_is_not_carried() -> None:
    summary = "Build the homepage for an app that keeps a weekly shipping log from merged PRs."
    assert not _carries(summary, PROMPT)


def test_a_prompt_missing_its_middle_is_not_carried() -> None:
    head, tail = PROMPT.split("Your job:", 1)
    assert not _carries(f"{head}\n{tail[40:]}", PROMPT)

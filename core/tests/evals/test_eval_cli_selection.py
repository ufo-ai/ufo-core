"""What `--case` selects when the flag is written more than once.

`nargs="*"` keeps the last occurrence and discards the rest, so `--case a --case b` ran only `b`
and said nothing about `a`. A narrowed run that quietly drops cases reports a smaller suite as if
it were the one asked for, and two of those were compared against each other."""

import pytest

from evals.__main__ import main as eval_main


def test_a_repeated_case_flag_keeps_every_name() -> None:
    with pytest.raises(SystemExit):
        eval_main(["--list", "--only", "ufo-app-bench", "--case", "no-such-case"])

    with pytest.raises(SystemExit):
        eval_main(
            [
                "--list",
                "--only",
                "ufo-app-bench",
                "--case",
                "no-such-case",
                "--case",
                "kanban-board",
            ]
        )


def test_space_separated_names_still_select_together(capsys) -> None:
    eval_main(["--list", "--only", "ufo-app-bench", "--case", "kanban-board", "call-notes"])
    assert "ufo-app-bench" in capsys.readouterr().out

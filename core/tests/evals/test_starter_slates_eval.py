from evals.suites.starter_slates import CASES, starter_slates_task


def test_the_task_narrows_to_the_named_cases() -> None:
    task = starter_slates_task()
    assert task.narrow is not None

    narrowed = task.narrow((CASES[0].name, CASES[2].name))

    assert narrowed.name == task.name
    assert narrowed.cases == (CASES[0].name, CASES[2].name)
    assert narrowed.digest != task.digest
    assert narrowed.judge_model == task.judge_model

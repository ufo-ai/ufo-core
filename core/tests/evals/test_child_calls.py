from evals.harness.capability import CapabilityOutput, ToolInvocation

BIND = "action:agent:set_homepage"
DEPLOY = "action:site:deploy_website"


def _call(name: str, call_id: str) -> ToolInvocation:
    return ToolInvocation(name=name, input={}, result="{}", has_result=True, call_id=call_id)


def test_child_calls_excludes_the_parents_own() -> None:
    """`calls` is the parent's with every child's appended, so a grader reading it for what the
    worker did also reads the parent. Both app suites did exactly that and failed a parent for
    binding the homepage, which is the parent's act."""

    parent_bind = _call(BIND, "p1")
    child_deploy = _call(DEPLOY, "c1")
    output = CapabilityOutput(
        response="",
        calls=(parent_bind, child_deploy),
        own_calls=(parent_bind,),
    )
    assert output.child_calls == (child_deploy,)
    assert not any(call.call == BIND for call in output.child_calls)


def test_child_calls_keeps_a_childs_bind() -> None:
    child_bind = _call(BIND, "c2")
    output = CapabilityOutput(
        response="", calls=(_call(DEPLOY, "p1"), child_bind), own_calls=(_call(DEPLOY, "p1"),)
    )
    assert any(call.call == BIND for call in output.child_calls)


def test_child_calls_accounts_for_a_repeated_call_once() -> None:
    """A parent and a child can call the same verb. Each of the parent's own is accounted for
    once, so the child's identical call survives."""

    output = CapabilityOutput(
        response="",
        calls=(_call(DEPLOY, "p1"), _call(DEPLOY, "c1")),
        own_calls=(_call(DEPLOY, "p1"),),
    )
    assert len(output.child_calls) == 1
    assert output.child_calls[0].call_id == "c1"


def test_child_calls_is_empty_when_nothing_was_delegated() -> None:
    only_own = (_call(DEPLOY, "p1"), _call(BIND, "p2"))
    assert CapabilityOutput(response="", calls=only_own, own_calls=only_own).child_calls == ()

from ufo.tools.tasks import FLAT_SLEEP_REFUSAL, flat_sleeps


def test_a_long_flat_sleep_is_padding() -> None:
    assert flat_sleeps("sleep 120") == (120,)
    assert flat_sleeps("uv sync & sleep 120; cat /tmp/uvsync.log") == (120,)


def test_short_sleeps_are_not_padding() -> None:
    assert flat_sleeps("sleep 5; curl -s localhost:8080/health") == ()
    assert flat_sleeps("sleep 10") == ()


def test_poll_loop_sleeps_are_the_waits_signal() -> None:
    assert flat_sleeps("until [ -f out ]; do sleep 15; done; cat out") == ()
    assert flat_sleeps("make build &\nwhile ! test -f done; do\n  sleep 30\ndone\ncat done") == ()


def test_a_flat_sleep_after_a_loop_is_still_padding() -> None:
    assert flat_sleeps("until [ -f out ]; do sleep 2; done\nsleep 45\ncat out") == (45,)


def test_nested_loops_track_depth() -> None:
    command = "for f in a b; do while ! test -f $f; do sleep 20; done; done; sleep 90"
    assert flat_sleeps(command) == (90,)


def test_mentioning_a_sleep_as_data_is_not_padding() -> None:
    assert flat_sleeps('grep -n "sleep 30" core/tests/sandbox/test_sandbox_session.py') == ()
    assert flat_sleeps("printf 'sleep 60\\n' >> job.sh") == ()
    assert flat_sleeps("cat > job.sh <<EOF\nsleep 60\nEOF\nchmod +x job.sh") == ()


def test_a_sleep_inside_a_nested_shell_string_is_the_documented_residual() -> None:
    assert flat_sleeps("bash -c 'sleep 120'") == ()


def test_words_containing_sleep_are_not_the_command() -> None:
    assert flat_sleeps("python3 -c 'import time; time.sleep(120)'") == ()
    assert flat_sleeps("cat /var/log/sleep 2>/dev/null") == ()


def test_refusal_names_the_sleep_it_refused() -> None:
    assert "sleep 120" in FLAT_SLEEP_REFUSAL.format(seconds=120)
    assert "background: true" in FLAT_SLEEP_REFUSAL.format(seconds=120)

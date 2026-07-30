import pathlib

t = pathlib.Path("extensions/web/ufo_ext_web/static/portal.html").read_text()

cases = {
    ".m1.html": (
        "const correctable = typeof match.ref === 'string' && match.ref.startsWith('memory/');",
        "const correctable = typeof match.ref === 'string';",
    ),
    ".m2.html": (
        "corrects: match.ref.slice('memory/'.length),",
        "corrects: '00000000-0000-4000-8000-000000000000',",
    ),
    ".m3.html": (
        "    result.replaceChildren(form, document.createTextNode(outcome.message));",
        "    result.replaceChildren(form);",
    ),
    ".m4.html": (
        "  const mainAgent = agents.find((agent) => agent.main);",
        "  const mainAgent = agents[agents.length - 1];",
    ),
    ".m5.html": (
        """    if (outcome.applied) {
      showWorkspace('memory', query);
      return;
    }""",
        """    if (outcome.applied) {
      return;
    }""",
    ),
}
for path, (old, new) in cases.items():
    assert old in t, path
    pathlib.Path(path).write_text(t.replace(old, new, 1))
print("mutants written")

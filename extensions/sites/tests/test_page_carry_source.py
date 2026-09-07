"""The deploy's own vite config, driven by node the way a repair cycle drives it."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from ufo_ext_sites.source import PAGE_DIR

pytestmark = pytest.mark.integration

DRIVER = """
import { chmodSync, mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const here = process.argv[2];
writeFileSync(join(here, "app.tsx"), "// first\\n");
writeFileSync(join(here, "index.html"), "<html></html>\\n");
chmodSync(join(here, "app.tsx"), 0o444);
chmodSync(join(here, "index.html"), 0o444);

const config = await import(join(here, "vite.config.ts"));
const plugin = config.default.plugins.find((p) => p.name === "ufo-carry-source");

plugin.closeBundle();
const mode = statSync(join(here, "dist", "src", "app.tsx")).mode & 0o777;
chmodSync(join(here, "app.tsx"), 0o644);
writeFileSync(join(here, "app.tsx"), "// second\\n");
chmodSync(join(here, "app.tsx"), 0o444);
plugin.closeBundle();

console.log(JSON.stringify({
  mode: mode.toString(8),
  carried: readFileSync(join(here, "dist", "src", "app.tsx"), "utf8").trim(),
}));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="the deploy's config is run by node")
def test_a_second_deploy_carries_the_source_over_its_own_output(tmp_path: Path) -> None:
    """A project scaffolded out of the read-only skills tree arrives at 0444, and `cpSync` copies
    the mode it finds — so the first deploy's own `dist/src` was unwritable, and the deploy that
    followed a repair had to overwrite exactly that. Recorded builds met it as a page refusal
    carrying a `closeBundle` stack, then ran `chmod -R u+w` and `rm -rf dist` to get past it.

    The mode also has to be writable for the Rebuild path, which pulls this carried source back
    and edits it in place."""

    project = tmp_path / "ufo-app"
    project.mkdir()
    (project / "vite.config.ts").write_bytes((PAGE_DIR / "vite.config.ts").read_bytes())
    (project / "sdk").mkdir()
    (project / "sdk" / "kit.js").write_text("export const mountApp = () => {};\n")
    driver = tmp_path / "drive.mjs"
    driver.write_text(DRIVER)

    done = subprocess.run(
        ["node", str(driver), str(project)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout.strip().splitlines()[-1])
    assert result["mode"] == "644"
    assert result["carried"] == "// second"

"""The deploy's own vite config, driven by node the way a repair cycle drives it."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from ufo_ext_sites.source import KIT_DIR, PAGE_DIR

pytestmark = pytest.mark.integration

VITE = Path(__file__).resolve().parents[2] / "web" / "frontend" / "node_modules" / ".bin" / "vite"
BUILD_CEILING_S = 240

BLOCKS_PAGE = """import { mountApp, Header, Page } from "ufo/kit";
import { BlockRoot, Item, ItemContent, ItemGroup, ItemTitle, StatGrid, StatTile } from "ufo/blocks";

function App() {
  return (
    <Page>
      <Header>Queue</Header>
      <BlockRoot>
        <div data-app-region="overview">
          <StatGrid columns={2}>
            <StatTile label="Waiting" value="3" />
            <StatTile label="Done" value="9" />
          </StatGrid>
        </div>
        <div data-app-region="records">
          <ItemGroup>
            <Item>
              <ItemContent>
                <ItemTitle>Rotate the signing key</ItemTitle>
              </ItemContent>
            </Item>
          </ItemGroup>
        </div>
      </BlockRoot>
    </Page>
  );
}

mountApp(document.getElementById("root")!, () => <App />);
"""

BLOCKS_DOCUMENT = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Application</title>
<link rel="stylesheet" href="./sdk/kit.css">
</head>
<body><div id="root"></div><script type="module" src="./app.tsx"></script></body>
</html>
"""

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


@pytest.mark.skipif(not VITE.is_file(), reason="the deploy config is built by this package's vite")
def test_a_page_importing_a_block_resolves_and_builds_through_the_deploy_config(
    tmp_path: Path,
) -> None:
    """`ufo/blocks` is a second alias onto a second SDK entry, so a page that imports a block
    resolves nothing unless the deploy's own config carries it and the kit build emitted
    `blocks.js` beside `kit.js`. A unit test over the alias list proves neither."""

    project = tmp_path / "ufo-app"
    project.mkdir()
    shutil.copytree(KIT_DIR, project / "sdk")
    (project / "vite.config.ts").write_bytes((PAGE_DIR / "vite.config.ts").read_bytes())
    (project / "app.tsx").write_text(BLOCKS_PAGE)
    (project / "index.html").write_text(BLOCKS_DOCUMENT)

    done = subprocess.run(
        [str(VITE), "build"],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=BUILD_CEILING_S,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert "failed to resolve import" not in done.stderr.lower()

    bundled = "".join(
        path.read_text(errors="ignore") for path in (project / "dist" / "assets").glob("*.js")
    )
    assert "blk-item-title" in bundled
    styled = "".join(path.read_text() for path in (project / "dist" / "assets").glob("*.css"))
    assert "--blk-bg-100" in styled and ".blk-root" in styled

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from ufo_testsupport.plugin import docker_or_fail

from evals.sandbox_image import SandboxImagePlan
from sandbox.build_template import build_definition_digest
from ufo.sandbox.session import SANDBOX_GID, SANDBOX_UID

pytestmark = pytest.mark.docker

AUDIT_SCRIPT = (
    Path(__file__).parents[3]
    / "extensions"
    / "sites"
    / "ufo_ext_sites"
    / "scripts"
    / "audit_application.cjs"
)
TIMEOUT_SECONDS = 120
PREVIEW = (
    '<!doctype html><html><head><link rel="manifest" href="/app.webmanifest"></head>'
    '<body><iframe name="ufo-app" src="./dist/index.html"></iframe></body></html>'
)
APPLICATION = """<!doctype html><html><head>
<link rel="stylesheet" href="/assets/app.css"><style>main{padding:1rem}</style></head><body><main>
<h1>Audit fixture</h1><p id="state">Ready</p>
<button onclick="document.querySelector('#state').textContent='First changed'">First</button>
<button onclick="document.querySelector('#state').textContent='Second changed'">Second</button>
</main><script>
const blocking=Object.freeze({
  startup:0,observation:0,unary:0,stream:0,timeout:0,interval:0
});
const snapshot=()=>Object.freeze({
  version:1,generation:1,epoch:0,mounted:true,state:'idle',revision:1,
  blockingWork:0,blocking
});
const afterPaint=()=>new Promise(resolve=>requestAnimationFrame(
  ()=>requestAnimationFrame(resolve)
));
Object.defineProperty(window,'__ufoApplicationLifecycle',{value:Object.freeze({
  snapshot,afterPaint,beginObservation:()=>0,endObservation:()=>Promise.resolve()
})});
</script></body></html>"""
ROUTE_RUNNER = r"""const http = require('http');
const {
  closeApplicationAudit,
  closeApplicationServer,
  startApplicationServer,
  validatedApplicationRoot,
} = require('/fixture/audit_application.cjs');
const request = (port, pathname) => new Promise((resolve, reject) => {
  const call = http.request({ host: '127.0.0.1', port, path: pathname }, (response) => {
    const chunks = [];
    response.on('data', (chunk) => chunks.push(chunk));
    response.on('end', () => resolve({
      body: Buffer.concat(chunks).toString(),
      contentType: response.headers['content-type'] || '',
      status: response.statusCode,
    }));
  });
  call.on('error', reject);
  call.end();
});
void (async () => {
  const application = await validatedApplicationRoot('/fixture/app');
  const { server, sockets, url } = await startApplicationServer(application);
  const address = server.address();
  const checks = [];
  for (const pathname of [
    '/preview.html',
    '/root.css',
    '/app.webmanifest',
    '/nested/font.woff2',
    '/nested/app.js',
    '/nested/image.svg',
    '/dist/index.html',
    '/assets/app.css',
    '/dist/assets/data.json',
    '/dist/assets',
    '/assets/escape.txt',
    '/assets/%2e%2e/%2e%2e/outside.txt',
    '/assets/%2E%2E%2F%2E%2E%2Foutside.txt',
    '/../outside.txt',
    '/%2e%2e/outside.txt',
    '//etc/passwd',
    '/%2Fetc%2Fpasswd',
    '/escape-root.txt',
  ]) checks.push([pathname, await request(address.port, pathname)]);
  await closeApplicationServer(server, sockets);
  let refused = false;
  try {
    await request(address.port, '/preview.html');
  } catch (error) {
    refused = Boolean(error.code);
  }
  const failedClose = await startApplicationServer(application);
  const failurePort = failedClose.server.address().port;
  let closeFailure = '';
  try {
    await closeApplicationAudit({ close: async () => {
      throw new Error('forced browser close failure');
    } }, failedClose.server, failedClose.sockets);
  } catch (error) {
    closeFailure = error.message;
  }
  let failureRefused = false;
  try {
    await request(failurePort, '/preview.html');
  } catch (error) {
    failureRefused = Boolean(error.code);
  }
  process.stdout.write(JSON.stringify({
    address, checks, closeFailure, failureRefused, refused, url
  }));
})().catch((error) => {
  process.stderr.write(String(error));
  process.exitCode = 1;
});
"""


def _share_with_sandbox_user(tmp_path: Path, sandbox_image: str) -> None:
    tmp_path.chmod(0o755)
    docker_or_fail(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "chown",
            "--user",
            "0:0",
            "-v",
            f"{tmp_path}:/fixture",
            sandbox_image,
            "-R",
            f"{SANDBOX_UID}:{SANDBOX_GID}",
            "/fixture",
        ],
        timeout=TIMEOUT_SECONDS,
    )


def test_application_eval_image_runs_the_node_owned_audit_protocol(
    sandbox_image: str, tmp_path: Path
) -> None:
    SandboxImagePlan(
        tmp_path / "unused.Dockerfile",
        sandbox_image,
        build_definition_digest(None),
    ).prepare()


def test_node_owned_audit_serves_only_the_application_and_closes(
    tmp_path: Path, sandbox_image: str
) -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker executable is not available")
    app = tmp_path / "app"
    assets = app / "dist" / "assets"
    nested = app / "nested"
    assets.mkdir(parents=True)
    nested.mkdir()
    shutil.copy2(AUDIT_SCRIPT, tmp_path / "audit_application.cjs")
    (tmp_path / "outside.txt").write_text("outside")
    (tmp_path / "route-runner.cjs").write_text(ROUTE_RUNNER)
    (app / "preview.html").write_text(PREVIEW)
    (app / "root.css").write_text("body{color:black}")
    (app / "app.webmanifest").write_text('{"name":"Audit fixture"}')
    (nested / "font.woff2").write_bytes(b"font")
    (nested / "app.js").write_text("window.loaded=true")
    (nested / "image.svg").write_text("<svg/>")
    (app / "dist" / "index.html").write_text(APPLICATION)
    (assets / "app.css").write_text("body{color:#111;background:#fff}")
    (assets / "data.json").write_text('{"ok":true}')
    (assets / "escape.txt").symlink_to(tmp_path / "outside.txt")
    (app / "escape-root.txt").symlink_to(tmp_path / "outside.txt")
    _share_with_sandbox_user(tmp_path, sandbox_image)

    routed = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "node",
            "-v",
            f"{tmp_path}:/fixture:ro",
            sandbox_image,
            "/fixture/route-runner.cjs",
        ],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )

    assert routed.returncode == 0, routed.stderr or routed.stdout
    result = json.loads(routed.stdout)
    assert result["address"]["address"] == "127.0.0.1"
    assert result["address"]["family"] == "IPv4"
    assert result["closeFailure"] == "forced browser close failure"
    assert result["failureRefused"] is True
    assert result["refused"] is True
    checks = dict(result["checks"])
    assert checks["/preview.html"]["contentType"] == "text/html; charset=utf-8"
    assert checks["/root.css"] == {
        "body": "body{color:black}",
        "contentType": "text/css; charset=utf-8",
        "status": 200,
    }
    assert checks["/app.webmanifest"] == {
        "body": '{"name":"Audit fixture"}',
        "contentType": "application/manifest+json",
        "status": 200,
    }
    assert checks["/nested/font.woff2"] == {
        "body": "font",
        "contentType": "font/woff2",
        "status": 200,
    }
    assert checks["/nested/app.js"]["contentType"] == "text/javascript; charset=utf-8"
    assert checks["/nested/image.svg"]["contentType"] == "image/svg+xml"
    assert checks["/dist/index.html"]["status"] == 200
    assert checks["/assets/app.css"] == {
        "body": "body{color:#111;background:#fff}",
        "contentType": "text/css; charset=utf-8",
        "status": 200,
    }
    assert checks["/dist/assets/data.json"]["contentType"] == ("application/json; charset=utf-8")
    for pathname in (
        "/dist/assets",
        "/assets/escape.txt",
        "/assets/%2e%2e/%2e%2e/outside.txt",
        "/assets/%2E%2E%2F%2E%2E%2Foutside.txt",
        "/../outside.txt",
        "/%2e%2e/outside.txt",
        "//etc/passwd",
        "/%2Fetc%2Fpasswd",
        "/escape-root.txt",
    ):
        assert checks[pathname]["status"] == 404


def test_root_cli_writes_ordered_outputs_and_closes_on_success_and_failure(
    tmp_path: Path, sandbox_image: str
) -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker executable is not available")
    app = tmp_path / "app"
    dist = app / "dist"
    dist.mkdir(parents=True)
    (dist / "assets").mkdir()
    shutil.copy2(AUDIT_SCRIPT, tmp_path / "audit_application.cjs")
    (app / "preview.html").write_text(PREVIEW)
    (dist / "index.html").write_text(APPLICATION)
    (app / "app.webmanifest").write_text('{"name":"Audit fixture"}')
    (dist / "assets" / "app.css").write_text("body{color:#111;background:#fff}")
    design = (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 305 844" width="305" height="844">'
        b'<g data-app-region="queue"><rect width="145" height="844" /></g>'
        b'<g data-app-region="detail"><rect x="160" width="145" height="844" /></g>'
        b"</svg>"
    )
    (tmp_path / "accepted-design.svg").write_bytes(design)
    evidence = {
        "version": 1,
        "design_sha256": hashlib.sha256(design).hexdigest(),
        "regions": [
            {"name": "queue", "left": 0, "top": 0, "width": 145 / 305, "height": 1},
            {
                "name": "detail",
                "left": 160 / 305,
                "top": 0,
                "width": 145 / 305,
                "height": 1,
            },
        ],
    }
    (tmp_path / "accepted-design.json").write_text(json.dumps(evidence))
    command = """set -eu
if node /fixture/audit_application.cjs \
  http://127.0.0.1:49123/preview.html \
  /tmp/refused.json /tmp/refused-light.png /tmp/refused-dark.png \
  /tmp/refused-interactive.html /tmp/refused-static.html \
  /fixture/accepted-design.svg /fixture/accepted-design.json \
  >/tmp/refused.out 2>/tmp/refused.err; then exit 11; fi
node /fixture/audit_application.cjs /fixture/app \
  /fixture/report.json /fixture/light.png /fixture/dark.png \
  /fixture/interactive.html /fixture/static.html \
  /fixture/accepted-design.svg /fixture/accepted-design.json
python3 - <<'PY'
import json
from urllib.error import URLError
from urllib.request import urlopen
url = json.load(open('/fixture/report.json'))['url']
try:
    urlopen(url, timeout=1)
except URLError:
    raise SystemExit(0)
raise SystemExit(12)
PY
cp /fixture/app/dist/index.html /fixture/complete.html
cp /fixture/app/preview.html /fixture/complete-preview.html
sed 's|/assets/app.css|/missing.css|' /fixture/complete.html > /fixture/app/dist/index.html
if node /fixture/audit_application.cjs /fixture/app \
  /fixture/missing-report.json /fixture/missing-light.png /fixture/missing-dark.png \
  /fixture/missing-interactive.html /fixture/missing-static.html \
  /fixture/accepted-design.svg /fixture/accepted-design.json \
  >/fixture/missing.out 2>/fixture/missing.err; then
  exit 14
else
  test "$?" -eq 1
  test ! -e /fixture/missing-report.json
  grep -q 'application resource failed: stylesheet .*missing.css returned 404' /fixture/missing.err
fi
cp /fixture/complete.html /fixture/app/dist/index.html
sed 's|/app.webmanifest|/missing.webmanifest|' \
  /fixture/complete-preview.html > /fixture/app/preview.html
if node /fixture/audit_application.cjs /fixture/app \
  /fixture/missing-manifest-report.json /fixture/missing-manifest-light.png \
  /fixture/missing-manifest-dark.png /fixture/missing-manifest-interactive.html \
  /fixture/missing-manifest-static.html \
  /fixture/accepted-design.svg /fixture/accepted-design.json \
  >/fixture/missing-manifest.out 2>/fixture/missing-manifest.err; then
  exit 15
else
  test "$?" -eq 1
  test ! -e /fixture/missing-manifest-report.json
  grep -q 'application resource failed: manifest .*missing.webmanifest returned 404' \
    /fixture/missing-manifest.err
fi
cp /fixture/complete-preview.html /fixture/app/preview.html
cp /fixture/app/dist/index.html /fixture/failed.html
printf '%s' '<!doctype html><p>No lifecycle</p>' > /fixture/app/dist/index.html
if timeout 25 node /fixture/audit_application.cjs /fixture/app \
  /fixture/failed-report.json /fixture/failed-light.png /fixture/failed-dark.png \
  /fixture/failed-interactive.html /fixture/failed-static.html \
  /fixture/accepted-design.svg /fixture/accepted-design.json; then
  exit 13
else
  test "$?" -eq 3
fi
"""
    _share_with_sandbox_user(tmp_path, sandbox_image)
    completed = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "bash",
            "-v",
            f"{tmp_path}:/fixture",
            sandbox_image,
            "-c",
            command,
        ],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    report = json.loads((tmp_path / "report.json").read_text())
    assert [(view["scheme"], view["width"]) for view in report["views"]] == [
        ("light", 1440),
        ("dark", 1440),
        ("light", 305),
        ("dark", 305),
    ]
    assert report["designRegions"] == evidence["regions"]
    assert [control["name"] for control in report["interaction"]["controls"]] == [
        "First",
        "Second",
    ]
    assert (tmp_path / "light.png").stat().st_size > 0
    assert (tmp_path / "dark.png").stat().st_size > 0
    assert "Audit fixture" in (tmp_path / "interactive.html").read_text()
    assert "Audit fixture" in (tmp_path / "static.html").read_text()
    diagnostic = json.loads((tmp_path / "failed-report.json.lifecycle.json").read_text())
    assert diagnostic["reason"] == "application lifecycle signal is missing"

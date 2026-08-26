#!/usr/bin/env python3
"""Deploy gate for the off-cluster sandbox's TLS egress route."""

from __future__ import annotations

import argparse
import os
import shlex
from dataclasses import dataclass
from time import monotonic, sleep
from urllib.parse import urlsplit

from e2b import Sandbox
from ufo_ext_e2b import (
    CA_INSTALL_TIMEOUT_SECONDS,
    CA_STAGING_PATH,
    E2B_TEMPLATES_ENV,
    INSTALL_CA_COMMAND,
    sandbox_templates,
)

from ufo.sandbox.session import (
    EGRESS_CA_CERT_ENV,
    PROXY_PASSWORD,
    SANDBOX_SIZES,
)

EXPECTED_CONNECT_STATUS = "403"
PENDING_CONNECT_STATUS = "000 pending "
INVALID_RUN_TOKEN = "invalid-run-token"
PROBE_TIMEOUT_SECONDS = 15
PROXY_READY_TIMEOUT_SECONDS = 300
PROBE_DELAY_SECONDS = 5
SANDBOX_MARGIN_SECONDS = 30
SANDBOX_TIMEOUT_SECONDS = (
    CA_INSTALL_TIMEOUT_SECONDS
    + PROXY_READY_TIMEOUT_SECONDS
    + PROBE_TIMEOUT_SECONDS
    + SANDBOX_MARGIN_SECONDS
)
PYTHON_PROXY_PROBE = """\
import urllib.error
import urllib.request
import ssl

try:
    urllib.request.urlopen("https://api.anthropic.com/v1/messages", timeout=10)
except urllib.error.URLError as error:
    detail = str(error)
    if "403" in detail:
        print("403")
    elif isinstance(error.reason, ssl.SSLError):
        print(f"000 fatal {detail}")
    elif isinstance(error.reason, OSError):
        print(f"000 pending {detail}")
    else:
        print(f"000 fatal {detail}")
else:
    print("200")
"""


@dataclass(frozen=True)
class ProxyTlsGate:
    public_url: str
    ca_cert: str
    template: str

    def run(self) -> None:
        parsed = urlsplit(self.public_url)
        if parsed.scheme != "https" or parsed.hostname is None:
            raise RuntimeError("sandbox proxy gate requires an HTTPS proxy URL")
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        authority = f"{host}:{parsed.port}" if parsed.port is not None else host
        proxy_url = f"https://{INVALID_RUN_TOKEN}:{PROXY_PASSWORD}@{authority}"
        probe = shlex.join(
            (
                "env",
                f"HTTP_PROXY={proxy_url}",
                f"HTTPS_PROXY={proxy_url}",
                f"http_proxy={proxy_url}",
                f"https_proxy={proxy_url}",
                "ufo",
                "run",
                "--",
                "python3",
                "-c",
                PYTHON_PROXY_PROBE,
            )
        )
        sandbox = Sandbox.create(template=self.template, timeout=SANDBOX_TIMEOUT_SECONDS)
        try:
            sandbox.files.write(CA_STAGING_PATH, self.ca_cert, user="root")
            sandbox.commands.run(
                INSTALL_CA_COMMAND,
                user="root",
                timeout=CA_INSTALL_TIMEOUT_SECONDS,
            )
            last_status = ""
            deadline = monotonic() + PROXY_READY_TIMEOUT_SECONDS
            while True:
                result = sandbox.commands.run(probe, timeout=PROBE_TIMEOUT_SECONDS)
                last_status = result.stdout.strip()
                if last_status == EXPECTED_CONNECT_STATUS:
                    print("sandbox proxy TLS gate passed")
                    return
                if not last_status.startswith(PENDING_CONNECT_STATUS):
                    break
                remaining = deadline - monotonic()
                if remaining <= 0:
                    break
                sleep(min(PROBE_DELAY_SECONDS, remaining))
            raise RuntimeError(
                f"sandbox proxy TLS gate expected CONNECT {EXPECTED_CONNECT_STATUS}, "
                f"got {last_status or 'no status'}"
            )
        finally:
            sandbox.kill()


def main() -> None:
    parser = argparse.ArgumentParser(prog="proxy-gate")
    parser.add_argument("--proxy-url", required=True)
    args = parser.parse_args()
    ca_cert = os.environ.get(EGRESS_CA_CERT_ENV)
    if not ca_cert:
        raise RuntimeError(f"{EGRESS_CA_CERT_ENV} is required")
    templates = os.environ.get(E2B_TEMPLATES_ENV)
    if not templates:
        raise RuntimeError(f"{E2B_TEMPLATES_ENV} is required")
    template = sandbox_templates(templates)[SANDBOX_SIZES[0]]
    ProxyTlsGate(public_url=args.proxy_url, ca_cert=ca_cert, template=template).run()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Deploy gate for the off-cluster sandbox's TLS egress route."""

from __future__ import annotations

import argparse
import os
import shlex
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from e2b import Sandbox
from ufo_ext_e2b import (
    CA_INSTALL_TIMEOUT_SECONDS,
    CA_STAGING_PATH,
    E2B_TEMPLATE_NAME,
    INSTALL_CA_COMMAND,
)

from ufo.sandbox.session import EGRESS_CA_CERT_ENV

PROBE_URL = "https://api.anthropic.com/v1/messages"
EXPECTED_CONNECT_STATUS = "403"
INVALID_RUN_TOKEN = "invalid-run-token"
SANDBOX_TIMEOUT_SECONDS = 300
PROBE_TIMEOUT_SECONDS = 15
PROBE_ATTEMPTS = 12
PROBE_DELAY_SECONDS = 5


@dataclass(frozen=True)
class ProxyTlsGate:
    public_url: str
    ca_cert: str

    def run(self) -> None:
        parsed = urlsplit(self.public_url)
        if parsed.scheme != "https" or parsed.hostname is None:
            raise RuntimeError("sandbox proxy gate requires an HTTPS proxy URL")
        proxy_url = f"https://{INVALID_RUN_TOKEN}:@{parsed.netloc}"
        probe = shlex.join(
            (
                "curl",
                "--silent",
                "--show-error",
                "--connect-timeout",
                "5",
                "--max-time",
                "10",
                "--output",
                "/dev/null",
                "--write-out",
                "%{http_connect}",
                "--proxy",
                proxy_url,
                PROBE_URL,
            )
        )
        sandbox = Sandbox.create(template=E2B_TEMPLATE_NAME, timeout=SANDBOX_TIMEOUT_SECONDS)
        try:
            sandbox.files.write(CA_STAGING_PATH, self.ca_cert, user="root")
            sandbox.commands.run(
                INSTALL_CA_COMMAND,
                user="root",
                timeout=CA_INSTALL_TIMEOUT_SECONDS,
            )
            last_status = ""
            last_error = ""
            for attempt in range(PROBE_ATTEMPTS):
                result = sandbox.commands.run(f"{probe} || true", timeout=PROBE_TIMEOUT_SECONDS)
                last_status = result.stdout.strip()
                last_error = result.stderr.strip()
                if last_status == EXPECTED_CONNECT_STATUS:
                    print("sandbox proxy TLS gate passed")
                    return
                if attempt + 1 < PROBE_ATTEMPTS:
                    time.sleep(PROBE_DELAY_SECONDS)
            raise RuntimeError(
                f"sandbox proxy TLS gate expected CONNECT {EXPECTED_CONNECT_STATUS}, "
                f"got {last_status or 'no status'}: {last_error}"
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
    ProxyTlsGate(public_url=args.proxy_url, ca_cert=ca_cert).run()


if __name__ == "__main__":
    main()

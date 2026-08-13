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

from ufo.sandbox.session import EGRESS_CA_CERT_ENV, SANDBOX_SIZES

PROBE_URL = "https://api.anthropic.com/v1/messages"
EXPECTED_CONNECT_STATUS = "403"
PENDING_CONNECT_STATUS = "000"
INVALID_RUN_TOKEN = "invalid-run-token"
PROBE_TIMEOUT_SECONDS = 15
PROXY_READY_TIMEOUT_SECONDS = 300
PROBE_DELAY_SECONDS = 5
SANDBOX_MARGIN_SECONDS = 30
CURL_PROXY_PENDING_EXIT_CODES = frozenset({5, 7, 28, 56})
SANDBOX_TIMEOUT_SECONDS = (
    CA_INSTALL_TIMEOUT_SECONDS
    + PROXY_READY_TIMEOUT_SECONDS
    + PROBE_TIMEOUT_SECONDS
    + SANDBOX_MARGIN_SECONDS
)


@dataclass(frozen=True)
class ProxyTlsGate:
    public_url: str
    ca_cert: str
    template: str

    def run(self) -> None:
        parsed = urlsplit(self.public_url)
        if parsed.scheme != "https" or parsed.hostname is None:
            raise RuntimeError("sandbox proxy gate requires an HTTPS proxy URL")
        proxy_url = f"https://{INVALID_RUN_TOKEN}:@{parsed.netloc}"
        curl = shlex.join(
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
        probe = f"{curl}; printf '\\n%s' $?"
        sandbox = Sandbox.create(template=self.template, timeout=SANDBOX_TIMEOUT_SECONDS)
        try:
            sandbox.files.write(CA_STAGING_PATH, self.ca_cert, user="root")
            sandbox.commands.run(
                INSTALL_CA_COMMAND,
                user="root",
                timeout=CA_INSTALL_TIMEOUT_SECONDS,
            )
            last_status = ""
            last_exit_code: int | None = None
            last_error = ""
            deadline = monotonic() + PROXY_READY_TIMEOUT_SECONDS
            while True:
                result = sandbox.commands.run(probe, timeout=PROBE_TIMEOUT_SECONDS)
                probe_result = result.stdout.strip().splitlines()
                if len(probe_result) != 2 or not probe_result[1].isdigit():
                    raise RuntimeError(
                        f"sandbox proxy TLS gate got malformed curl result: {result.stdout!r}"
                    )
                last_status, exit_code = probe_result
                last_exit_code = int(exit_code)
                last_error = result.stderr.strip()
                if last_status == EXPECTED_CONNECT_STATUS:
                    print("sandbox proxy TLS gate passed")
                    return
                if (
                    last_status != PENDING_CONNECT_STATUS
                    or last_exit_code not in CURL_PROXY_PENDING_EXIT_CODES
                ):
                    break
                remaining = deadline - monotonic()
                if remaining <= 0:
                    break
                sleep(min(PROBE_DELAY_SECONDS, remaining))
            raise RuntimeError(
                f"sandbox proxy TLS gate expected CONNECT {EXPECTED_CONNECT_STATUS}, "
                f"got {last_status or 'no status'} with curl exit "
                f"{last_exit_code if last_exit_code is not None else 'missing'}: {last_error}"
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

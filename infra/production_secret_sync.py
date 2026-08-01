from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Sequence

EXTERNAL_SECRET_RESOURCE = "externalsecrets.external-secrets.io"
KUBECTL_TIMEOUT_SECONDS = 60
POLL_SECONDS = 1
TIMEOUT_SECONDS = 900


def _kubectl(namespace: str, *arguments: str) -> str:
    return subprocess.run(
        (
            "kubectl",
            "--namespace",
            namespace,
            "--request-timeout=30s",
            *arguments,
        ),
        stdout=subprocess.PIPE,
        text=True,
        check=True,
        timeout=KUBECTL_TIMEOUT_SECONDS,
    ).stdout


def _states(namespace: str) -> dict[str, tuple[str, bool]]:
    resources = _kubectl(
        namespace,
        "api-resources",
        "--api-group=external-secrets.io",
        "--namespaced",
        "--output=name",
    ).splitlines()
    if EXTERNAL_SECRET_RESOURCE not in resources:
        return {}
    document = json.loads(_kubectl(namespace, "get", "externalsecret", "--output=json"))
    return {
        item["metadata"]["name"]: (
            item.get("status", {}).get("syncedResourceVersion", ""),
            any(
                condition.get("type") == "Ready" and condition.get("status") == "True"
                for condition in item.get("status", {}).get("conditions", ())
            ),
        )
        for item in document["items"]
    }


def main(arguments: Sequence[str] = ()) -> None:
    if len(arguments) != 2:
        raise RuntimeError("usage: production_secret_sync.py NAMESPACE DEPLOYMENT_ID")
    namespace, deployment_id = arguments
    previous = _states(namespace)
    if not previous:
        return
    for name in previous:
        _kubectl(
            namespace,
            "annotate",
            f"externalsecret/{name}",
            f"force-sync={deployment_id}",
            "--overwrite",
        )
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while True:
        current = _states(namespace)
        if all(
            name in current and current[name][0] != version and current[name][1]
            for name, (version, _) in previous.items()
        ):
            return
        if time.monotonic() >= deadline:
            raise TimeoutError("production ExternalSecrets did not publish new ready versions")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main(tuple(sys.argv[1:]))

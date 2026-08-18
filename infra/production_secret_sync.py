from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
from collections.abc import Sequence

EXTERNAL_SECRET_RESOURCE = "externalsecrets.external-secrets.io"
KUBECTL_TIMEOUT_SECONDS = 60
AWS_TIMEOUT_SECONDS = 60
POLL_SECONDS = 1
TIMEOUT_SECONDS = 900
SUPPORTED_CONVERSION_STRATEGY = "Default"
SUPPORTED_DECODING_STRATEGY = "None"


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


def _external_secrets(namespace: str) -> list[dict]:
    resources = _kubectl(
        namespace,
        "api-resources",
        "--api-group=external-secrets.io",
        "--namespaced",
        "--output=name",
    ).splitlines()
    if EXTERNAL_SECRET_RESOURCE not in resources:
        return []
    return json.loads(_kubectl(namespace, "get", "externalsecret", "--output=json"))["items"]


def _states(items: Sequence[dict]) -> dict[str, tuple[str, bool]]:
    return {
        item["metadata"]["name"]: (
            item.get("status", {}).get("syncedResourceVersion", ""),
            any(
                condition.get("type") == "Ready" and condition.get("status") == "True"
                for condition in item.get("status", {}).get("conditions", ())
            ),
        )
        for item in items
    }


def _projected(namespace: str, name: str) -> dict[str, str]:
    document = json.loads(_kubectl(namespace, "get", "secret", name, "--output=json"))
    return {
        key: base64.b64decode(value).decode() for key, value in document.get("data", {}).items()
    }


def _source(secret_id: str) -> str:
    return subprocess.run(
        (
            "aws",
            "secretsmanager",
            "get-secret-value",
            "--secret-id",
            secret_id,
            "--query",
            "SecretString",
            "--output",
            "text",
        ),
        stdout=subprocess.PIPE,
        text=True,
        check=True,
        timeout=AWS_TIMEOUT_SECONDS,
    ).stdout.rstrip("\n")


def _expected(sources: dict[str, str], remote_ref: dict) -> str:
    """The value Secrets Manager holds right now for one `remoteRef`.

    A pinned `version`, or either strategy set to anything but its default, would make the byte
    comparison below compare the wrong things — so an unrecognized ref is a hard error, never a
    silently skipped entry."""
    for field, supported in (
        ("conversionStrategy", SUPPORTED_CONVERSION_STRATEGY),
        ("decodingStrategy", SUPPORTED_DECODING_STRATEGY),
    ):
        strategy = remote_ref.get(field, supported)
        if strategy != supported:
            raise RuntimeError(f"unsupported {field} {strategy!r} in {remote_ref}")
    if "version" in remote_ref:
        raise RuntimeError(f"pinned remoteRef version is unsupported: {remote_ref}")
    secret_id = remote_ref["key"]
    if secret_id not in sources:
        sources[secret_id] = _source(secret_id)
    document = sources[secret_id]
    property_name = remote_ref.get("property")
    if property_name is None:
        return document
    return json.loads(document)[property_name]


def _verify(namespace: str, items: Sequence[dict]) -> None:
    """Every projected key equals what Secrets Manager holds, byte for byte.

    A synced version and a Ready condition only say the controller published something — both were
    true for seven hours while `ufo-egress-ca` carried a superseded key encoding, and the pods that
    read it crash-looped. The value itself is the only claim worth checking, so a drifted key stops
    the deploy before it rolls pods against it. Names and refs go in the message, never values."""
    sources: dict[str, str] = {}
    drifted = []
    for item in items:
        name = item["metadata"]["name"]
        specification = item["spec"]
        if "dataFrom" in specification:
            raise RuntimeError(f"dataFrom is unsupported: externalsecret/{name}")
        projected = _projected(namespace, specification["target"]["name"])
        for entry in specification["data"]:
            secret_key = entry["secretKey"]
            remote_ref = entry["remoteRef"]
            if projected.get(secret_key) != _expected(sources, remote_ref):
                drifted.append(f"externalsecret/{name} {secret_key} from {remote_ref['key']}")
    if drifted:
        raise RuntimeError("projected values differ from Secrets Manager: " + ", ".join(drifted))


def main(arguments: Sequence[str] = ()) -> None:
    if len(arguments) != 2:
        raise RuntimeError("usage: production_secret_sync.py NAMESPACE DEPLOYMENT_ID")
    namespace, deployment_id = arguments
    items = _external_secrets(namespace)
    previous = _states(items)
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
        items = _external_secrets(namespace)
        current = _states(items)
        if all(
            name in current and current[name][0] != version and current[name][1]
            for name, (version, _) in previous.items()
        ):
            _verify(namespace, items)
            return
        if time.monotonic() >= deadline:
            raise TimeoutError("production ExternalSecrets did not publish new ready versions")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main(tuple(sys.argv[1:]))

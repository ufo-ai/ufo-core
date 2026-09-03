"""Seat one email on a local stack and open its portal signed in.

The walk is the gateway's own onboarding wire, driven the way the terminal client drives it: the
email, then the console verifier's code, on a fresh session of the `dev` channel. The gateway founds
the workspace on the first walk and joins it on every later one, so the script runs as often as a
slot is rebuilt. The bearer it is handed rides the same browser handoff `ufoctl portal` uses — a
one-shot loopback page that posts it to the portal — so nothing is typed and no URL carries it.

    uv run python dev/signin.py --origin http://ufo-1.localhost:18080 --email alex@simplecasual.com
"""

import argparse
import re
import secrets
import sys

import httpx

from ufo.cli import BrowserHandoff

CONSOLE_CODE = "000000"
CHANNEL = "dev"
PORTAL_PATH = "/surface/web"
TIMEOUT_SECONDS = 30.0


ESCAPES = {"n": "\n", "t": "\t", "\\": "\\"}


def unescape(field: str) -> str:
    return re.sub(r"\\(.)", lambda match: ESCAPES.get(match.group(1), match.group(0)), field)


def directives(body: str) -> list[tuple[str, list[str]]]:
    parsed = []
    for line in body.splitlines():
        verb, _, rest = line.partition("\t")
        fields = [unescape(field) for field in rest.split("\t")] if rest else []
        parsed.append((verb, fields))
    return parsed


def walk(origin: str, email: str) -> tuple[str, str]:
    session = secrets.token_urlsafe(24)
    url = f"{origin}/v1/onboard/{CHANNEL}"
    with httpx.Client(timeout=TIMEOUT_SECONDS, headers={"x-ufo-session": session}) as client:
        client.post(url, content=email).raise_for_status()
        answered = client.post(url, content=CONSOLE_CODE)
        answered.raise_for_status()
    token = workspace = None
    asked: list[str] = []
    for verb, fields in directives(answered.text):
        match verb:
            case "token":
                token = fields[0]
            case "workspace":
                workspace = fields[0]
            case "say":
                print(fields[0])
            case "ask" | "choose":
                asked.append(fields[0])
    if token is None or workspace is None:
        raise SystemExit(f"the walk ended without a bearer; the gateway asked: {asked}")
    return token, workspace


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--origin", required=True, help="the stack's one origin, e.g. http://ufo-1.localhost:18080"
    )
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    token, workspace = walk(args.origin.rstrip("/"), args.email)
    portal_url = f"{workspace.rstrip('/')}{PORTAL_PATH}"
    BrowserHandoff(portal_url=portal_url, token=token).open()
    print(f"opened {portal_url}", file=sys.stderr)


if __name__ == "__main__":
    main()

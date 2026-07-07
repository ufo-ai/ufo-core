"""
Shared helpers for running LibreOffice headless on Linux and macOS.
"""

import os
import platform
import subprocess
from pathlib import Path

MACRO_DIR = {
    "Darwin": "~/Library/Application Support/LibreOffice/4/user/basic/Standard",
    "Linux": "~/.config/libreoffice/4/user/basic/Standard",
}


def soffice_env() -> dict[str, str]:
    env = os.environ.copy()
    env["SAL_USE_VCLPLUGIN"] = "svp"
    return env


def macro_dir() -> Path:
    return Path(
        os.path.expanduser(MACRO_DIR.get(platform.system(), MACRO_DIR["Linux"]))
    )


def run_soffice(
    args: list[str], timeout: int | None = None
) -> subprocess.CompletedProcess[str]:
    cmd = ["soffice", *args]
    return subprocess.run(
        cmd, capture_output=True, text=True, env=soffice_env(), timeout=timeout
    )

"""Shared subprocess forwarding for the thin Omni-Grid command wrappers."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def main(command: str) -> int:
    environment = os.environ.copy()
    source_path = str(REPOSITORY_ROOT / "core")
    existing_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_path if not existing_path else source_path + os.pathsep + existing_path
    )
    completed = subprocess.run(
        [sys.executable, "-m", "omnigrid.cli", command, *sys.argv[1:]],
        env=environment,
        check=False,
    )
    return completed.returncode

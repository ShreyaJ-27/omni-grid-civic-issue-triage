"""Offline skill metadata and wrapper tests; not a real-agent smoke test."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIRECTORY = REPOSITORY_ROOT / "skill" / "omni-grid"
SCRIPTS_DIRECTORY = SKILL_DIRECTORY / "scripts"
WRAPPER_COMMANDS = ("ingest", "evidence", "cluster", "verify", "summarize", "triage")
REQUIRED_SKILL_TERMS = (
    "When to use",
    "Available commands",
    "workflow",
    "human review",
    "deterministic",
    "inconclusive",
)


def run_wrapper(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(REPOSITORY_ROOT / "core") + os.pathsep + (
        environment.get("PYTHONPATH", "")
    )
    return subprocess.run(
        [sys.executable, *args],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_skill_frontmatter_and_required_files_exist() -> None:
    skill_file = SKILL_DIRECTORY / "SKILL.md"
    assert skill_file.is_file()
    assert SCRIPTS_DIRECTORY.is_dir()

    contents = skill_file.read_text(encoding="utf-8")
    assert contents.startswith("---\n")
    _, frontmatter, body = contents.split("---", 2)
    metadata = yaml.safe_load(frontmatter)
    assert metadata["name"] == "omni-grid"
    assert metadata["name"] == SKILL_DIRECTORY.name
    assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", metadata["name"])
    assert len(metadata["description"]) > 40
    assert len(metadata["description"]) <= 1024
    assert all(term.casefold() in body.casefold() for term in REQUIRED_SKILL_TERMS)
    assert (REPOSITORY_ROOT / "docs" / "skill-smoke-test.md").is_file()
    assert all(
        (SCRIPTS_DIRECTORY / f"{name}.py").is_file() for name in WRAPPER_COMMANDS
    )


def test_wrapper_forwards_success_json_from_outside_repository() -> None:
    wrapper = SCRIPTS_DIRECTORY / "ingest.py"
    sample = REPOSITORY_ROOT / "data" / "samples" / "complaint-open.json"

    result = run_wrapper(str(wrapper), str(sample), cwd=Path(sys.prefix))

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    output = json.loads(result.stdout)
    assert len(output) == 1
    assert output[0]["id"] == "complaint-001"


def test_wrapper_forwards_stderr_and_exit_code_from_outside_repository() -> None:
    wrapper = SCRIPTS_DIRECTORY / "ingest.py"

    result = run_wrapper(
        str(wrapper),
        "does-not-exist.json",
        cwd=Path(sys.prefix),
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "input file not found" in result.stderr
    assert "Traceback" not in result.stderr


def test_all_wrappers_delegate_to_matching_cli_command() -> None:
    for command in WRAPPER_COMMANDS:
        wrapper = SCRIPTS_DIRECTORY / f"{command}.py"
        result = run_wrapper(str(wrapper), "--help", cwd=REPOSITORY_ROOT)

        assert result.returncode == 0, (command, result.stderr)
        assert f"omnigrid {command}" in result.stdout
        assert result.stderr == ""

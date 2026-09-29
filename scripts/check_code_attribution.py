#!/usr/bin/env python3
"""Check a Git patch and commit records for visible AI attribution.

Inputs are data files created by the accompanying workflow. This read-only check
cannot detect provider-controlled statistical watermarks or rewrite Git history.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

SOURCE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".css", ".go", ".h", ".html", ".java",
    ".js", ".jsx", ".json", ".kt", ".m", ".md", ".php", ".py",
    ".rb", ".rs", ".sh", ".sql", ".svelte", ".swift", ".toml",
    ".ts", ".tsx", ".vue", ".xml", ".yaml", ".yml",
}
SOURCE_NAMES = {"Dockerfile", "Makefile", "Justfile"}
PROVIDER = r"(?:Anthropic|Claude|OpenAI|ChatGPT|Codex|Google AI|Gemini|Copilot|Qwen|Aider)"
ATTRIBUTION = re.compile(
    rf"\b(?:generated (?:by|with)|written by|authored by)\s+{PROVIDER}\b"
    rf"|\bCo-Authored-By\s*:[^\n]*\b{PROVIDER}\b"
    r"|\bClaude-Session\s*:",
    re.IGNORECASE,
)
IDENTITY = re.compile(rf"\b{PROVIDER}\b|copilot-swe-agent", re.IGNORECASE)
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
PATCH_FILE = Path("/tmp/code-attribution.patch")
COMMIT_RECORDS_FILE = Path("/tmp/code-attribution.commits")


def new_path(header: str) -> str | None:
    if not header.startswith("+++ "):
        return None
    path = header[4:].strip('"')
    if path == "/dev/null":
        return None
    return path[2:] if path.startswith("b/") else path


def source_path(name: str | None) -> bool:
    if not name:
        return False
    path = Path(name)
    return path.suffix.lower() in SOURCE_SUFFIXES or path.name in SOURCE_NAMES


def inspect_added(name: str, number: int, line: str) -> list[str]:
    findings = []
    if ATTRIBUTION.search(line):
        findings.append(f"{name}:{number}: AI attribution text")
    points = sorted({ord(char) for char in line if unicodedata.category(char) == "Cf"})
    if points:
        codes = ",".join(f"U+{point:04X}" for point in points)
        findings.append(f"{name}:{number}: invisible Unicode {codes}")
    return findings


def check_patch(patch: str) -> list[str]:
    findings = []
    name = None
    number = 0
    for line in patch.splitlines():
        if line.startswith("+++ "):
            name = new_path(line)
            continue
        hunk = HUNK.match(line)
        if hunk:
            number = int(hunk.group(1))
            continue
        if not source_path(name):
            continue
        if line.startswith("+") and not line.startswith("+++"):
            findings.extend(inspect_added(name, number, line[1:]))
            number += 1
        elif line.startswith(" "):
            number += 1
    return findings


def check_commits(records: str) -> list[str]:
    findings = []
    for record in records.split("\0\0"):
        fields = record.lstrip("\n").split("\0", 5)
        if len(fields) != 6:
            continue
        sha, author, author_email, committer, committer_email, message = fields
        identities = (author, author_email, committer, committer_email)
        if any(IDENTITY.search(value) for value in identities):
            findings.append(f"commit {sha[:12]}: AI author or committer identity")
        if any(ATTRIBUTION.search(line) for line in message.splitlines()):
            findings.append(f"commit {sha[:12]}: AI attribution in message")
    return findings


def main() -> int:
    try:
        patch = PATCH_FILE.read_text(encoding="utf-8", errors="replace")
        records = COMMIT_RECORDS_FILE.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        print(f"Attribution check could not read its inputs: {error}")
        return 2
    findings = check_patch(patch) + check_commits(records)
    for finding in findings:
        print(finding)
    if findings:
        print(f"Found {len(findings)} new attribution or invisible Unicode issue(s).")
        return 1
    print("No new AI attribution or invisible Unicode found in source changes or commits.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Fail on new AI attribution or invisible Unicode in a proposed Git change.

Checks added source lines and commit metadata between two revisions. It does not
rewrite history or claim to detect statistical watermarks in generated text.
"""

from __future__ import annotations

import re
import subprocess
import sys
import unicodedata
from pathlib import Path


SOURCE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".css", ".go", ".h", ".html", ".java",
    ".js", ".jsx", ".kt", ".m", ".php", ".py", ".rb", ".rs",
    ".sh", ".svelte", ".swift", ".ts", ".tsx", ".vue",
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


def git(*args: str) -> bytes:
    result = subprocess.run(
        ["git", *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    return result.stdout


def source_paths(base: str, head: str):
    names = git("diff", "--name-only", "-z", "--diff-filter=ACMR", base, head)
    for raw in names.split(b"\0"):
        if not raw:
            continue
        name = raw.decode("utf-8", "surrogateescape")
        path = Path(name)
        if path.suffix.lower() in SOURCE_SUFFIXES or path.name in SOURCE_NAMES:
            yield name


def check_added_lines(base: str, head: str):
    findings = []
    for name in source_paths(base, head):
        patch = git("diff", "--unified=0", "--no-ext-diff", "--no-color", base, head, "--", name)
        line_number = 0
        for line in patch.decode("utf-8", "replace").splitlines():
            hunk = HUNK.match(line)
            if hunk:
                line_number = int(hunk.group(1))
            elif line.startswith("+") and not line.startswith("+++"):
                added = line[1:]
                if ATTRIBUTION.search(added):
                    findings.append(f"{name}:{line_number}: AI attribution text")
                points = sorted({ord(char) for char in added if unicodedata.category(char) == "Cf"})
                if points:
                    codes = ",".join(f"U+{point:04X}" for point in points)
                    findings.append(f"{name}:{line_number}: invisible Unicode {codes}")
                line_number += 1
            elif line.startswith(" "):
                line_number += 1
    return findings


def check_commits(base: str, head: str):
    findings = []
    hashes = git("rev-list", f"{base}..{head}").decode("ascii").splitlines()
    for sha in hashes:
        metadata = git("show", "-s", "--format=%an%x00%ae%x00%cn%x00%ce%x00%B", sha)
        fields = metadata.decode("utf-8", "replace").split("\0", 4)
        if len(fields) != 5:
            raise ValueError(f"could not parse commit {sha[:12]}")
        authors, message = fields[:4], fields[4]
        if any(IDENTITY.search(value) for value in authors):
            findings.append(f"commit {sha[:12]}: AI author or committer identity")
        if any(ATTRIBUTION.search(line) for line in message.splitlines()):
            findings.append(f"commit {sha[:12]}: AI attribution in message")
    return findings


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: check_code_attribution.py BASE_SHA HEAD_SHA", file=sys.stderr)
        return 2
    base, head = sys.argv[1:]
    try:
        findings = check_added_lines(base, head) + check_commits(base, head)
    except (subprocess.CalledProcessError, ValueError) as error:
        print(f"Attribution check could not complete: {error}", file=sys.stderr)
        return 2
    for finding in findings:
        print(finding)
    if findings:
        print(f"Found {len(findings)} new attribution or invisible Unicode issue(s).")
        return 1
    print("No new AI attribution or invisible Unicode found in source changes or commits.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

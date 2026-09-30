"""Scan every line ever added in git history for secret-shaped strings, WITHOUT printing the values.

Run from the repository root:

    python docs/hardening/tools/scan_history_for_secrets.py

For each hit it prints the pattern name, the file, how many commits added it, the first commit,
and a masked sample (first 8 characters and the length). Database URLs are classified by their
password (a known development default, a placeholder, or "OTHER"), and the one AWS access key id
that appears in AWS's own documentation is recognised as such.

Paths the campaign rules forbid reading (historical apply_*/verify_* scripts, evidence folders,
arch07_/arch08_ files, PDFs, certification reports) are excluded with git pathspecs, so their
content is never read. That is a limit: a secret committed in one of them is NOT found here (see
docs/hardening/NEEDS-OWNER.md N-016). Binary files (such as the deleted stripe.exe) are not scanned.
"""

from __future__ import annotations

import re
import subprocess
import sys

PATTERNS = {
    "stripe-secret-or-restricted-key": r"\b(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{16,}",
    "stripe-webhook-secret": r"\bwhsec_[0-9A-Za-z+/=]{24,}",
    "aws-access-key-id": r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    "groq-api-key": r"\bgsk_[0-9A-Za-z]{30,}",
    "google-api-key": r"\bAIza[0-9A-Za-z_\-]{35}",
    "private-key-block": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "github-or-slack-token": r"\b(?:ghp_[0-9A-Za-z]{36}|github_pat_[0-9A-Za-z_]{40,}|xox[baprs]-[0-9A-Za-z\-]{10,})",
    "jwt": r"\beyJ[0-9A-Za-z_\-]{15,}\.eyJ[0-9A-Za-z_\-]{15,}\.[0-9A-Za-z_\-]{10,}",
    "openai-or-anthropic-style-key": r"\bsk-(?:ant-)?[0-9A-Za-z_\-]{32,}",
    "db-url-with-password": r"postgres(?:ql)?(?:\+\w+)?://[^:\s/@]+:[^@\s/]{4,}@[^\s/]+",
    "assigned-secret-literal": r"""(?i)\b(?:password|passwd|secret|api[_-]?key|token|pepper)\w*\s*[:=]\s*['"][^'"\s${<{(]{16,}['"]""",
}

EXCLUDE = [
    ":!**/apply_*.py", ":!**/verify_*.py", ":!backend/evidence", ":!**/arch07_*", ":!**/arch08_*",
    ":!*.pdf", ":!*-FINAL-CERTIFICATION.md", ":!**/package-lock.json", ":!**/node_modules",
]

AWS_DOCUMENTATION_EXAMPLE = "AKIAIOSFODNN7EXAMPLE"
KNOWN_DEV_PASSWORDS = {"postgres", "flowpilot", "password", "changeme", "test", "secret"}
DB_URL = re.compile(r"postgres(?:ql)?(?:\+\w+)?://([^:\s/@]+):([^@\s/]+)@")


def main() -> int:
    cmd = ["git", "log", "--all", "-p", "--no-color", "--format=@@COMMIT %h", "--", ".", *EXCLUDE]
    compiled = {name: re.compile(rx) for name, rx in PATTERNS.items()}
    commit = file = ""
    hits: dict[tuple[str, str], list[str]] = {}
    samples: dict[tuple[str, str], str] = {}
    lines_scanned = 0

    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, errors="replace") as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines_scanned += 1
            if line.startswith("@@COMMIT "):
                commit = line.split()[1]
            elif line.startswith("+++ b/"):
                file = line[6:].strip()
            elif line.startswith("+") and not line.startswith("+++"):
                for name, rx in compiled.items():
                    match = rx.search(line[:4000])
                    if not match:
                        continue
                    text = match.group(0)
                    label = name
                    if name == "aws-access-key-id" and text == AWS_DOCUMENTATION_EXAMPLE:
                        label = "aws-access-key-id (AWS's documented example)"
                    if name == "db-url-with-password":
                        password = (DB_URL.search(text) or [None, None, ""])[2].strip("'\"")
                        placeholder = password.startswith(("$", "{", "<", "%", "@"))
                        label = "db-url-with-password (" + (
                            "placeholder" if placeholder else "known dev default" if password in KNOWN_DEV_PASSWORDS
                            else f"OTHER, length {len(password)}"
                        ) + ")"
                    key = (label, file)
                    hits.setdefault(key, [])
                    if commit not in hits[key]:
                        hits[key].append(commit)
                    samples.setdefault(key, f"{text[:8]}... (length {len(text)})")

    print(f"scanned {lines_scanned} diff lines (forbidden paths excluded)")
    for (label, file), commits in sorted(hits.items()):
        print(f"{label:52} {file:66} commits={len(commits):<3} first={commits[0]} sample={samples[(label, file)]}")
    print(f"\n{len(hits)} (pattern, file) groups. Review each; 'OTHER' and any key/token pattern needs a human look.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

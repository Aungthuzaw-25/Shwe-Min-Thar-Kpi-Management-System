#!/usr/bin/env python3
"""Fail if the repo tracks a real secret.

This catches *accidental* commits (a copied .env, a pasted key) — the
repo's actual risk, since this repo is public. It is not a defence
against a determined attacker rewriting this script in their own PR:
the push-to-main run after a merge re-checks with main's version of
this script, so a bypass would still be caught (as a failed run) then.

Rules (never prints a value, only file:line and the variable name):
  1. No tracked file may be a dotenv file other than the two examples:
     `.env.example`, `.env.qa.example`. (.gitignore already blocks them;
     this is the backstop for `git add -f` and for files added before
     the ignore rule.)
  2. No tracked file may assign a real-looking value to a known secret
     name (JWT_SECRET_KEY, DATABASE_URL, RESEND_API_KEY, S3_SECRET_KEY,
     *_PASSWORD, *_CLIENT_SECRET, TOTP_ENCRYPTION_KEY, ...). Empty
     values, env references (${...}, os.getenv, environ) and obvious
     placeholders (CHANGE_ME, PLACEHOLDER, REPLACE_WITH, EXAMPLE,
     YOUR_*, CI-ONLY-*, CI-PLACEHOLDER, localhost, example.com) pass.
     Test fixtures are exempt: tests/ holds synthetic values and is
     excluded from the Docker image, but rule 1 still applies there.
  3. No tracked file may contain a high-confidence token prefix
     (ghp_, github_pat_, AKIA, sk-live, xoxb-). This script itself
     and the scan workflow are exempt from this rule only, because
     they list the patterns they search for.

Usage: python3 scripts/check_no_secrets.py [--root DIR]
Exit 0 = clean, exit 1 = findings (listed without values).
"""

import re
import subprocess
import sys
from pathlib import Path

SECRET_NAMES = (
    "JWT_SECRET_KEY",
    "SECRET_KEY",
    "DATABASE_URL",
    "RESEND_API_KEY",
    "SMTP_PASSWORD",
    "S3_SECRET_KEY",
    # NB: S3_ACCESS_KEY is an identifier (like a username), not a secret,
    # so it is deliberately not in this list; S3_SECRET_KEY is the secret.
    "MINIO_ROOT_PASSWORD",
    "TOTP_ENCRYPTION_KEY",
    "GOOGLE_CLIENT_SECRET",
    "MICROSOFT_CLIENT_SECRET",
    "BOOTSTRAP_SUPERADMIN_PASSWORD",
    "SEED_SUPERADMIN_PASSWORD",
    "SEED_SUPER_ADMIN_PASSWORD",
    "SEED_DEMO_PASSWORD",
    "SEED_L2_PASSWORD",
    "SEED_L0_PASSWORD",
    "SEED_L1_PASSWORD",
    "SEED_TEST_SUPERADMIN_PASSWORD",
    "SEED_TEST_ADMIN_PASSWORD",
    "SEED_TEST_FIRST_LOGIN_PASSWORD",
    "SEED_TEST_L0_PASSWORD",
    "SEED_TEST_L1_PASSWORD",
    "SEED_TEST_L2_PASSWORD",
    "QA_SEED_PASSWORD",
    "QA_POSTGRES_PASSWORD",
    "QA_MINIO_ROOT_PASSWORD",
)

PLACEHOLDER_SUBSTRINGS = (
    "change_me",
    "change-me",
    "placeholder",
    "replace_with",
    "replace-with",
    "example",
    "your_",
    "your-",
    "ci-only",
    "ci-placeholder",
    "localhost",
    "127.0.0.1",
    "example.com",
)

# A value that IS exactly one of these words is a public weak default,
# not a credential (e.g. a local-QA example). A real secret is never
# exactly the word "password"; password strength is a separate check.
WEAK_WORDS = {"password", "changeme", "change-me", "secret", "test", "demo"}

# Python type annotations look like assignments to the regex
# (DATABASE_URL: str) but declare a field, they do not set a value.
TYPE_WORDS = {"str", "int", "float", "bool", "None", "True", "False"}

# password part of a URL: postgresql://user:PASSWORD@host/db
URL_PASSWORD_RE = re.compile(r"://[^:/@]+:([^@/]+)@")

TOKEN_PREFIXES = ("ghp_", "github_pat_", "AKIA", "sk-live", "xoxb-")

SKIP_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2",
    ".ttf", ".eot", ".pdf", ".zip", ".gz", ".lock", ".min.js", ".map",
}

# name = value  /  name: value  /  "name": value  (env, YAML, JSON, Python)
ASSIGN_RE = re.compile(
    r"(?P<name>[A-Z][A-Z0-9_]*)\s*[:=]\s*"
    r"(?P<quote>[\"']?)(?P<value>[^\"'\s,}\]]+)"
)


def is_placeholder(value: str) -> bool:
    v = value.strip()
    if not v:
        return True
    low = v.lower()
    if low in WEAK_WORDS or low in TYPE_WORDS:
        return True
    if low.startswith("${") or "os.getenv" in low or "getenv(" in low or "environ" in low:
        return True
    return any(p in low for p in PLACEHOLDER_SUBSTRINGS)


def database_url_is_safe(value: str) -> bool:
    """A DATABASE_URL is only a leak if its password part is real.

    Templates (no password, ${...} reference, literal "password",
    localhost/example hosts) pass; a URL carrying any other password
    fails, even if the username/host look innocent.
    """
    v = value.strip().strip("\"'")
    if "${" in v:
        return True
    m = URL_PASSWORD_RE.search(v)
    if not m:
        return True
    password = m.group(1)
    if is_placeholder(password):
        return True
    host = v.split("@", 1)[1].split("/")[0].lower() if "@" in v else ""
    return host in ("localhost", "127.0.0.1") or "example" in host


def is_dotenv_file(path: str) -> bool:
    name = Path(path).name
    return name == ".env" or name.startswith(".env.")


def main() -> int:
    root = Path(sys.argv[sys.argv.index("--root") + 1]) if "--root" in sys.argv else Path(".")
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files"],
        check=True, capture_output=True, text=True,
    ).stdout.splitlines()
    findings: list[str] = []

    for rel in tracked:
        path = root / rel
        if is_dotenv_file(rel) and rel not in (".env.example", ".env.qa.example"):
            findings.append(f"{rel}: tracked dotenv file (only .env.example/.env.qa.example are allowed)")
            continue
        if path.suffix in SKIP_EXTENSIONS or rel.endswith(".min.js"):
            continue
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="strict")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable: not a place secrets hide in text
        in_tests = rel.startswith("tests/") or rel.startswith("web/tests/")
        for lineno, line in enumerate(text.splitlines(), 1):
            # Strip ${...} and $VAR references first: they point at the
            # environment, they are not values in this file.
            clean = re.sub(r"\$\{[^}]*\}", "", line)
            clean = re.sub(r"\$[A-Za-z_][A-Za-z0-9_]*", "", clean)
            if not in_tests:
                for name in SECRET_NAMES:
                    m = re.search(rf"\b{name}\b\s*[:=]\s*[\"']?([^\"'\s,}}\]]+)", clean)
                    if not m:
                        continue
                    value = m.group(1)
                    if name == "DATABASE_URL":
                        if not database_url_is_safe(value):
                            findings.append(f"{rel}:{lineno}: {name} has a real-looking value")
                    elif not is_placeholder(value):
                        findings.append(f"{rel}:{lineno}: {name} has a real-looking value")
            if rel not in (".github/workflows/secret-scan.yml", "scripts/check_no_secrets.py"):
                for prefix in TOKEN_PREFIXES:
                    if prefix in line:
                        findings.append(f"{rel}:{lineno}: token prefix {prefix} found")
                        break

    if findings:
        print("SECRET SCAN FAILED:")
        for finding in findings:
            print(f"  - {finding}")
        print("Remove the value from the repo (and rotate it if it was ever real).")
        return 1
    print(f"Secret scan clean ({len(tracked)} tracked files checked, values never printed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

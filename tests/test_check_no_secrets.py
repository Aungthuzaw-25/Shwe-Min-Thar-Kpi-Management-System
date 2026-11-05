"""Secret-scanner rules (scripts/check_no_secrets.py).

Run against temp git repos so the real CLI path (git
ls-files) is exercised. Fixture values are synthetic and
contain no token prefix, so this file itself scans clean.
"""

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_no_secrets.py"
FAKE = "some-real-looking-value-123"


def _repo(tmp_path, files):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "t@t.test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "t"], check=True
    )
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        subprocess.run(["git", "-C", str(root), "add", rel], check=True)
    return root


def _scan(root):
    proc = subprocess.run(
        ["python3", str(SCRIPT), "--root", str(root)],
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout


def test_json_quoted_key_secret_is_caught(tmp_path):
    root = _repo(
        tmp_path,
        {"config.json": '{\n  "JWT_SECRET_KEY": "' + FAKE + '"\n}\n'},
    )
    code, out = _scan(root)
    assert code == 1
    assert "JWT_SECRET_KEY" in out


def test_unlisted_password_and_secret_suffixes_are_caught(tmp_path):
    root = _repo(
        tmp_path,
        {
            "app.py": f'FOO_PASSWORD = "{FAKE}"\n',
            "other.py": f'BAR_CLIENT_SECRET = "{FAKE}"\n',
        },
    )
    code, out = _scan(root)
    assert code == 1
    assert "FOO_PASSWORD" in out
    assert "BAR_CLIENT_SECRET" in out


def test_placeholders_env_refs_and_annotations_still_pass(tmp_path):
    root = _repo(
        tmp_path,
        {
            "config.json": '{\n  "JWT_SECRET_KEY": "CHANGE_ME"\n}\n',
            "app.py": (
                f'FOO_PASSWORD = "placeholder-value"\n'
                "BAR_PASSWORD = ${BAR_PASSWORD}\n"
                "BAZ_PASSWORD: str\n"
            ),
        },
    )
    code, out = _scan(root)
    assert code == 0, out


def test_exempted_qa_lines_pass(tmp_path):
    # The exact owner-approved file+name combinations (Zenn,
    # 2026-10-30): QA-local test-pack credentials in usage docs.
    # Values here are synthetic; the real lines are covered by
    # test_full_tracked_tree_scans_clean below.
    root = _repo(
        tmp_path,
        {
            "scripts/README-test-pack.md": (
                f"export TEST_PACK_PASSWORD={FAKE}\n"
                f"export TEST_SMOKE_SA_PASSWORD={FAKE}\n"
            ),
            "scripts/load_smoke.py": (
                f"export TEST_SMOKE_SA_PASSWORD={FAKE}\n"
            ),
            "scripts/test_data_pack.py": (
                f"export TEST_PACK_PASSWORD={FAKE}\n"
            ),
        },
    )
    code, out = _scan(root)
    assert code == 0, out


def test_exemption_does_not_leak_to_other_names_in_same_files(tmp_path):
    root = _repo(
        tmp_path,
        {
            "scripts/README-test-pack.md": (
                f"export TEST_PACK_PASSWORD={FAKE}\n"
                f"export OTHER_PACK_PASSWORD={FAKE}\n"
            ),
        },
    )
    code, out = _scan(root)
    assert code == 1
    assert "OTHER_PACK_PASSWORD" in out
    assert "TEST_PACK_PASSWORD" not in out


def test_exemption_does_not_leak_to_same_name_in_other_files(tmp_path):
    root = _repo(
        tmp_path,
        {"other.md": f"export TEST_PACK_PASSWORD={FAKE}\n"},
    )
    code, out = _scan(root)
    assert code == 1
    assert "TEST_PACK_PASSWORD" in out


def test_full_tracked_tree_scans_clean(tmp_path):
    # The real tree, real CLI, real exemption: the four QA-doc
    # lines are the only rule-2 findings the fixed scanner ever
    # reported, and all four are exempt — so the tree must scan
    # clean. (This test reads the worktree it runs in; it fails
    # closed if anyone commits a new real-looking secret.)
    repo_root = Path(__file__).resolve().parents[1]
    code, out = _scan(repo_root)
    assert code == 0, out


def test_token_prefixes_still_caught_and_tests_exempt(tmp_path):
    # Built dynamically so this file itself never contains the
    # contiguous prefix (rule 3 scans tests/ too).
    prefix = "ghp" + "_"
    root = _repo(
        tmp_path,
        {
            "app.py": f'token = "{prefix}" + "x" * 36\n',
            "tests/test_fixture.py": f'JWT_SECRET_KEY = "{FAKE}"\n',
        },
    )
    code, out = _scan(root)
    assert code == 1
    assert prefix in out
    # The tests/ fixture is exempt from rule 2 (not from rule 3).
    assert "test_fixture" not in out

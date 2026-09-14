"""HANDOFF.md must stay well-formed so every agent can rely on it. See AGENTS.md."""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_handoff.py"


def test_handoff_document_is_well_formed():
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, (
        "HANDOFF.md failed validation. Fix the problems below, then re-run.\n"
        + result.stdout
        + result.stderr
    )


def test_agent_entry_points_reference_handoff():
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "HANDOFF.md" in agents
    assert "@AGENTS.md" in claude


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=agent@example.com", "-c", "user.name=Agent", *args],
                   cwd=cwd, check=True, capture_output=True)


def test_branch_check_requires_a_handoff_update_for_code_changes(tmp_path):
    for relative in ("scripts/check_handoff.py", "scripts/roadmap.py", "HANDOFF.md", "ROADMAP.md"):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / relative, target)
    (tmp_path / "app.py").write_text("print(1)\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "branch", "base")

    def check():
        return subprocess.run([sys.executable, "scripts/check_handoff.py", "--base", "base"], cwd=tmp_path,
                              capture_output=True, text=True, encoding="utf-8")

    (tmp_path / "app.py").write_text("print(2)\n", encoding="utf-8")
    _git(tmp_path, "commit", "-q", "-am", "code without a handoff update")
    result = check()
    assert result.returncode == 1 and "but not HANDOFF.md" in result.stdout, result.stdout + result.stderr

    with open(tmp_path / "HANDOFF.md", "a", encoding="utf-8") as handle:
        handle.write("\n")
    _git(tmp_path, "commit", "-q", "-am", "record the work")
    result = check()
    assert result.returncode == 0, result.stdout + result.stderr

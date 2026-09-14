"""HANDOFF.md must stay well-formed so every agent can rely on it. See AGENTS.md."""

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

"""Import-cost regression coverage for UPG-HOOK-SUBPROCESS-IMPORT-TAX.

`vectr hook <event>` is spawned as a fresh subprocess on every
SessionStart/UserPromptSubmit/PreToolUse/PreCompact — its own import cost is
paid on every single one. These tests run a real subprocess (not a mock)
against `sys.modules` so a future change that reintroduces a heavy import
onto this path (agent.config's ~40-section surface, dotenv, or httpx, which
alone pulls in `rich`/`pygments`) fails CI immediately instead of only
showing up as a wall-clock regression someone notices much later.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_HEAVY_MODULES = ("httpx", "dotenv", "agent.config", "agent.version_stamp")


def _run(script: str, stdin: str = "{}") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(_REPO_ROOT), input=stdin, capture_output=True, text=True, timeout=10,
    )


class TestHookCliModuleImportCost:
    def test_agent_hook_cli_import_pulls_in_no_heavy_deps(self):
        script = (
            "import sys\n"
            "import agent.hook_cli\n"
            f"heavy = sorted(m for m in {_HEAVY_MODULES!r} if m in sys.modules)\n"
            "print(','.join(heavy))\n"
        )
        result = _run(script)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""


class TestRealHookSubcommandFastDispatch:
    """Exercises the actual `sys.argv[1] == "hook"` short-circuit at the top
    of main.py — the code path the installed `vectr` console-script binary
    runs for every real hook invocation (`from main import main`, which
    executes main.py's module body, exactly like this test's `import main`
    does)."""

    def test_short_circuits_before_reaching_heavy_imports(self):
        script = (
            "import sys\n"
            "sys.argv = ['vectr', 'hook', 'session-start']\n"
            "try:\n"
            "    import main\n"
            "except SystemExit as e:\n"
            f"    heavy = sorted(m for m in {_HEAVY_MODULES!r} if m in sys.modules)\n"
            "    print('EXIT_CODE=' + str(e.code))\n"
            "    print('HEAVY=' + ','.join(heavy))\n"
            "else:\n"
            "    print('NO_EXIT_RAISED')\n"
        )
        result = _run(script)
        assert result.returncode == 0, result.stderr
        assert "EXIT_CODE=0" in result.stdout
        assert "HEAVY=\n" in result.stdout or result.stdout.strip().endswith("HEAVY=")

    def test_other_subcommands_are_unaffected_and_still_import_normally(self):
        """The fast-dispatch guard must never fire for anything other than
        the exact well-formed `hook <event>` shape — e.g. `vectr status`
        still reaches the normal argparse-driven path (and its imports)."""
        script = (
            "import sys\n"
            "sys.argv = ['vectr', 'status']\n"
            "import main\n"
            "print('agent.config' in sys.modules)\n"
        )
        result = _run(script, stdin="")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "True"

    def test_hook_with_invalid_event_falls_through_to_argparse_validation(self):
        """An out-of-band hook_event doesn't match the fast path's closed
        set, so it falls through unchanged to argparse's own `choices=[...]`
        validation (same behavior as before this change, byte-for-byte).
        Mirrors the installed console-script shim exactly: `from main import
        main; sys.exit(main())` — `import main` alone only runs module-level
        code (including the fast-dispatch guard), not the argparse-driven
        `main()` function itself."""
        script = (
            "import sys\n"
            "sys.argv = ['vectr', 'hook', 'not-a-real-event']\n"
            "from main import main\n"
            "sys.exit(main())\n"
        )
        result = _run(script, stdin="")
        assert result.returncode == 2
        assert "invalid choice" in result.stderr

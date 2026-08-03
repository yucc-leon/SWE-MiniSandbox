"""mini-swe-agent Environment adapter backed by a MiniSandbox SWESBEnv session.

Lets mini-swe-agent (DefaultAgent) run its bash actions inside a fully set-up
MiniSandbox deployment (repo at /testbed, shared venv, chroot/no-chroot session)
instead of Docker. Implements the minisweagent Environment protocol:
    execute(action: dict, cwd="") -> {"output", "returncode", "exception_info"}
    get_template_vars(**kwargs) -> dict
    serialize() -> dict

Generation output (the agent's `submission`) is the `git diff`, which drops
straight into the existing MiniSandbox scoring (preds.json -> model_patch).
"""
from __future__ import annotations

import os
import platform
from typing import Any

# Submit marker: when a command's first output line is exactly this and it
# exited 0, mini-swe-agent treats the rest of the output as the final submission.
_SUBMIT_MARKER = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"


class MiniSandboxEnvironment:
    def __init__(self, swesbenv, *, cwd: str = "/testbed", default_timeout: int = 60, **kwargs):
        """Wrap an already-started SWESBEnv (deployment + session + repo ready)."""
        self.env = swesbenv
        # Resolve the in-session repo path (/testbed in chroot; root_dir/testbed otherwise)
        self.cwd = self.env.deployment.sandbox_path(cwd)
        self.default_timeout = default_timeout
        # Persistent session: start commands from the repo dir.
        try:
            self.env.communicate(f"cd {self.cwd}", check="ignore", timeout=30)
        except Exception:
            pass

    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None, **_) -> dict[str, Any]:
        command = action.get("command", "") if isinstance(action, dict) else str(action)
        to = timeout or self.default_timeout
        try:
            out = self.env.communicate(command, timeout=to, check="ignore")
            result = {"output": out, "returncode": 0, "exception_info": ""}
        except Exception as e:
            raw = getattr(e, "output", None) or ""
            result = {
                "output": raw if isinstance(raw, str) else "",
                "returncode": -1,
                "exception_info": f"An error occurred while executing the command: {e}",
                "extra": {"exception_type": type(e).__name__, "exception": str(e)},
            }
        self._check_finished(result)
        return result

    def _check_finished(self, output: dict) -> None:
        """Raise Submitted (with the git diff) when the agent emits the submit marker.

        CANONICAL CAPTURE (aligns with upstream mini-swe-agent: submission = the patch, captured
        AT SUBMIT TIME via the marker, while the agent's edits are live in the working tree).
        The submission is whatever the agent printed after the marker (upstream style:
        `echo MARKER && git diff`). If the agent printed ONLY the marker (e.g. a bare
        `echo MARKER` submit command), we AUTO-CAPTURE `git diff HEAD` from the env here so the
        submission is always the real working-tree patch — regardless of how the submit command
        was phrased. This removes the whole post-hoc-capture bug class (the harness used to run
        `git diff HEAD` AFTER agent.run() returned, which intermittently came back empty)."""
        lines = output.get("output", "").lstrip().splitlines(keepends=True)
        if lines and lines[0].strip() == _SUBMIT_MARKER and output.get("returncode") == 0:
            from minisweagent.exceptions import Submitted

            submission = "".join(lines[1:])
            if "diff --git " not in submission:
                # agent emitted a bare marker (or no diff) -> capture the working-tree diff now,
                # at submit time, while edits are in place. This is the canonical patch.
                try:
                    submission = self.env.communicate("cd /testbed && git diff HEAD", timeout=60, check="ignore") or submission
                except Exception:
                    pass
            raise Submitted(
                {
                    "role": "exit",
                    "content": submission,
                    "extra": {"exit_status": "Submitted", "submission": submission},
                }
            )

    def get_template_vars(self, **kwargs) -> dict[str, Any]:
        # Templates may reference cwd / env; merge conservatively.
        base = {"cwd": self.cwd}
        try:
            base |= platform.uname()._asdict()
        except Exception:
            pass
        return base | dict(os.environ) | kwargs

    def serialize(self) -> dict:
        return {
            "info": {
                "config": {
                    "environment_type": f"{self.__class__.__module__}.{self.__class__.__name__}",
                    "cwd": self.cwd,
                }
            }
        }

"""Regression tests for no-chroot default-shell recovery."""

import asyncio
import sys
from types import SimpleNamespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for rel in ("SWE-ReX/src", "SWE-agent", "sandboxdev"):
    path = str(ROOT / rel)
    if path not in sys.path:
        sys.path.insert(0, path)

from swerex.exceptions import SessionNotInitializedError
from swerex.runtime.abstract import BashObservation

from sweagent.environment.swe_sbenv import SWEsbEnv
from swesandbox.sandbox_deployment import SandboxDeployment


class _FakeRuntime:
    def __init__(self):
        self.create_session_calls = 0
        self.close_session_calls = 0
        self._run_calls = 0

    async def create_session(self, request):
        self.create_session_calls += 1
        return None

    async def close_session(self, request):
        self.close_session_calls += 1
        return None

    async def run_in_session(self, action):
        self._run_calls += 1
        if self._run_calls == 1:
            raise SessionNotInitializedError("shell not initialized")
        if self._run_calls == 2:
            return BashObservation(output="", exit_code=0)
        return BashObservation(output="hello\n", exit_code=0)


class _FakeDeployment:
    def __init__(self):
        self._config = SimpleNamespace(root_dir="/tmp/fake-sandbox", use_chroot=False)
        self.runtime = _FakeRuntime()
        self.install_commands_calls = 0

    def _rewrite_script_paths(self, command: str) -> str:
        return command

    def rewrite_observation_paths(self, output: str) -> str:
        return output

    def startup(self) -> str:
        return "/bin/bash --noprofile --norc\n"

    def _install_commands(self):
        self.install_commands_calls += 1


class _FakeCloseRuntime:
    def __init__(self):
        self.close_session_calls = 0

    async def close_session(self, request):  # noqa: ARG002
        self.close_session_calls += 1
        return None


def test_communicate_recovers_lost_default_shell():
    env = SWEsbEnv(
        deployment=_FakeDeployment(),
        repo=None,
        post_startup_commands=[],
    )

    output = env.communicate("echo hello", check="raise")

    assert output == "hello"
    assert env.deployment.runtime.close_session_calls == 1
    assert env.deployment.runtime.create_session_calls == 1
    assert env.deployment.install_commands_calls == 1


def test_bashsession_run_raises_typed_session_error():
    from swerex.runtime.abstract import BashAction, CreateSandboxBashSessionRequest
    from swerex.runtime.sandbox import BashSession

    session = BashSession(CreateSandboxBashSessionRequest(startup_cmd="", startup_timeout=1))

    try:
        asyncio.run(session.run(BashAction(command="echo hello", timeout=1, check="raise")))
    except SessionNotInitializedError:
        return
    raise AssertionError("Expected SessionNotInitializedError")


def test_bashsession_exit_code_parsing_tolerates_echoed_command():
    from swerex.runtime.abstract import BashAction, CreateSandboxBashSessionRequest
    from swerex.runtime.sandbox import BashSession
    import swerex.runtime.sandbox as sandbox_runtime

    class _FakeShell:
        def __init__(self, marker: str, ps1: str):
            self.before = ""
            self.after = ""
            self._marker = marker
            self._ps1 = ps1
            self._step = 0

        def sendline(self, _command: str) -> None:
            return None

        def expect(self, patterns, timeout=None):  # noqa: ARG002
            self._step += 1
            if self._step == 1:
                self.before = "command-output\n"
                return patterns.index(self._ps1)
            if self._step == 2:
                self.before = (
                    f"\n{self._ps1}printf '{self._marker}%s\\n' $?\n"
                )
                self.after = f"{self._marker}0"
                return 0
            if self._step == 3:
                self.before = "\n"
                return patterns.index(self._ps1)
            raise AssertionError("unexpected expect() call")

    session = BashSession(CreateSandboxBashSessionRequest(startup_cmd="", startup_timeout=1))
    marker = session._exit_code_marker()
    session._shell = _FakeShell(marker, session._ps1)

    original_check = sandbox_runtime._check_bash_command
    original_split = sandbox_runtime._split_bash_command
    sandbox_runtime._check_bash_command = lambda _command: None
    sandbox_runtime._split_bash_command = lambda command: [command]
    try:
        result = asyncio.run(session._run_normal(BashAction(command="echo hello", timeout=1, check="raise")))
    finally:
        sandbox_runtime._check_bash_command = original_check
        sandbox_runtime._split_bash_command = original_split

    assert result.exit_code == 0
    assert "command-output" in result.output
    assert "printf" not in result.output
    assert marker not in result.output


def test_reward_failure_recovers_default_shell_before_reset():
    env = SWEsbEnv(
        deployment=_FakeDeployment(),
        repo=None,
        post_startup_commands=[],
    )
    recovered = []

    def _raise_reward(**_kwargs):
        raise TimeoutError("reward timed out")

    def _recover():
        recovered.append(True)

    env.deployment._calculate_reward = _raise_reward
    env._recover_default_session = _recover

    reward, f2p, p2p, output = env._calculate_reward()

    assert (reward, f2p, p2p, output) == (0.0, {}, {}, "")
    assert recovered == [True]


def test_swebench_reward_timeout_discards_dirty_session():
    deployment = object.__new__(SandboxDeployment)
    deployment._config = SimpleNamespace(data_type="swebench", eval_timeout=1)
    deployment._runtime = _FakeCloseRuntime()
    deployment.logger = SimpleNamespace(error=lambda *_args, **_kwargs: None)

    def _raise_reward(**_kwargs):
        raise TimeoutError("eval timed out")

    deployment._calculate_reward_swebench = _raise_reward

    assert deployment._calculate_reward() == (0.0, {}, {}, "")
    assert deployment.runtime.close_session_calls == 1


if __name__ == "__main__":
    test_communicate_recovers_lost_default_shell()
    test_bashsession_run_raises_typed_session_error()
    test_bashsession_exit_code_parsing_tolerates_echoed_command()
    test_reward_failure_recovers_default_shell_before_reset()
    test_swebench_reward_timeout_discards_dirty_session()
    print("ok")

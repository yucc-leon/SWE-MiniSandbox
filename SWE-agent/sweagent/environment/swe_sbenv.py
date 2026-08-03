
import asyncio
import logging
import contextlib
import os
from os import path
import shlex
from pathlib import PurePath
from typing import Literal, Self

from swerex.exceptions import SessionDoesNotExistError, SessionNotInitializedError
from pydantic import BaseModel, ConfigDict, Field
from swerex.deployment.abstract import AbstractDeployment
from swerex.deployment.config import DeploymentConfig, DockerDeploymentConfig, get_deployment
from swerex.runtime.abstract import (
    BashAction,
    BashInterruptAction,
    CloseBashSessionRequest,
    CreateBashSessionRequest,
    CreateSandboxBashSessionRequest,
    ReadFileRequest,
    WriteFileRequest,
)
from swerex.runtime.abstract import Command as RexCommand
import time
from sweagent.environment.hooks.abstract import CombinedEnvHooks, EnvHook
from sweagent.environment.repo import Repo, RepoConfig
from sweagent.utils.log import get_logger
from swesandbox.sandbox_deployment import get_deployment_from_config
from swesandbox.utils import apply_patch

class EnvironmentConfig(BaseModel):
    """Configure data sources and setup instructions for the environment in which we solve the tasks."""

    deployment: DeploymentConfig = Field(
        default_factory=lambda: DockerDeploymentConfig(image="python:3.11"),
        description="Deployment options.",
    )
    repo: RepoConfig | None = Field(
        default=None,
        description="Repository options.",
    )
    post_startup_commands: list[str] = []
    """Execute these commands before starting to run the agent but after all other setup steps.
    They will be executed in the same shell as the agent.
    Note: Every command is passed as a string, not a list of arguments.
    """
    post_startup_command_timeout: int = 500
    """Timeout for the post-startup commands.
    NOTE: The timeout applies to every command in `post_startup_commands` separately.
    """

    # pydantic config
    model_config = ConfigDict(extra="forbid")

    name: str = "main"


class SWEsbEnv:
    def __init__(
        self,
        *,
        deployment: AbstractDeployment,
        repo: Repo | RepoConfig | None,
        post_startup_commands: list[str],
        post_startup_command_timeout: int = 500,
        hooks: list[EnvHook] | None = None,
        name: str = "main",
    ):
        """This class represents the environment in which we solve the tasks.

        Attributes:
            deployment: MiniSandbox deployment
            repo: Repository configuration object, or anything following the `Repo` protocol
            post_startup_commands: Commands to execute before starting the agent
            hooks: Environment hooks (used to inject custom functionality)
                Equivalent to calling `add_hook` for each hook after initialization.
            name: Name of the environment
        """
        super().__init__()
        self.deployment = deployment
        self.repo = repo
        self._post_startup_commands = post_startup_commands
        self.post_startup_command_timeout = post_startup_command_timeout
        self.logger = get_logger("swea-env", emoji="🪴")
        self.name = name
        self.clean_multi_line_functions = lambda x: x
        self._chook = CombinedEnvHooks()
        for hook in hooks or []:
            self.add_hook(hook)

    @classmethod
    def from_config(cls, ds,bundles,config: EnvironmentConfig) -> Self:
        """Create an environment instance from a configuration object.
        We change the creation of deployment here to support our custom deployment creation.

        Attributes:
            ds: a data item from dataset
            bundles: list of tool bundles to be installed in the environment
        """
        # Always copy config to avoid shared state between different instances
        config = config.model_copy(deep=True)
        #check class type of deployment

        return cls(
            deployment=get_deployment_from_config(config=config.deployment,ds=ds,bundles=bundles),
            repo=config.repo,
            post_startup_commands=config.post_startup_commands,
            post_startup_command_timeout=config.post_startup_command_timeout,
            name=config.name,
        )

    def add_hook(self, hook: EnvHook) -> None:

        hook.on_init(env=self)
        self._chook.add_hook(hook)

    def start(self) -> None:
        """Start the environment and reset it to a clean state.
            Step 1 : initialize deployment (session creation)
            Step 2 : reset environment (repo copy/reset)
            Step 3 : post init (venv preparation, tool installation, etc.)
        """
        Time_data={}
        time_data=self._init_deployment()
        Time_data['init_deployment']=time_data
        reset_time = time.time()
        self.reset()
        reset_end_time = time.time()
        reset_data={"reset_duration": reset_end_time - reset_time,"reset_start_time":reset_time,"reset_end_time":reset_end_time}
        Time_data['reset_deployment']=reset_data
        self._chook.on_post_init()
        post_init_time_data=self.deployment.post_init(self)

        Time_data.update(post_init_time_data)
        return Time_data


        # for command in self._post_startup_commands:
        #     self.communicate(command, check="raise", timeout=self.post_startup_command_timeout)
    def _copy_repo(self):
        """Clone/copy repository/codebase in mini-sandbox environment."""
        if self.repo is None:
            return True

        self._chook.on_copy_repo_started(repo=self.repo)
        return self.repo.copy2(deployment=self.deployment,try_count=3,local_path=self.deployment.cached_git_dir)

    def hard_reset(self):
        """Resets the environment and deployment, i.e., completely restarts the
        deployment.
        """
        self.close()
        self.start()

    def reset(self):
        """Reset the environment to a clean state.
        Gets called by `start`, but can also be called independently to reset the
        environment to a clean state before a new attempt.

        """
       #self.communicate(input="cd /", check="raise")

        if not self._copy_repo():
            self._reset_repository()

        self._chook.on_environment_startup()
    def apply_test_patch(self):
        """apply test patch only"""

        test_patch=self.deployment.ds.get('test_patch',None)
        if test_patch is not None:
            apply_patch(sandbox_root=self.deployment.root_dir,git_folder=self.deployment.git_folder,patch_str=test_patch,instance_id=self.deployment.root_dir,reverse=False)
    def pre_check(self):
        """This funtion is used to apply a golden patch if provided and run eval scripts right after repo is copied and
        reset and installed. (mainly used to check if the env is valid before running
        any agent steps.)
        Note that for swesmith data_type, the patch is applied reversely
        for swebench data_type, the patch is applied normally
        """
        # if self.deployment.eval_dir!="":
        #     path= f"{self.deployment.eval_dir}/preds.json"
        #     # load json
        #     import json
        #     with open(path, 'r') as f:
        #         preds = json.load(f)
        #     current_pred = preds.get(self.deployment.ds['instance_id'],None)
        #     if current_pred is not None:
        #         patch = current_pred.get('patch',None)
        #         if patch=='':
        #             patch = None
        #     else:
        #         patch = None
        # else:
        patch=self.deployment.ds.get('patch',None)
        test_patch=self.deployment.ds.get('test_patch',None)

        # Diagnostic: SWE_SANDBOX_PRECHECK_NO_GOLD=1 skips applying the gold fix so the repo
        # is graded in its BUGGY state. Used to verify the reward discriminates (buggy F2P
        # must FAIL -> reward 0); with gold applied F2P passes -> reward 1.
        _skip_gold = os.environ.get("SWE_SANDBOX_PRECHECK_NO_GOLD") == "1"
        self.logger.info("PRECHECK_NO_GOLD=%r -> _skip_gold=%s (data_type=%s, patch_present=%s)",
                         os.environ.get("SWE_SANDBOX_PRECHECK_NO_GOLD"), _skip_gold,
                         self.deployment._config.data_type, patch is not None)
        if self.deployment._config.data_type=='swesmith':
            # Direction matters for swesmith. The instance `patch` is the injected BUG, so the
            # GOLD fix = apply it REVERSE. But when SCORING A MODEL PATCH (injected via
            # model_patch_file, which overwrites `patch` with the model's forward fix), it must be
            # applied FORWARD. SWE_SANDBOX_SCORE_MODEL_PATCH=1 selects model-patch scoring.
            # (Without this, swesmith model patches were applied reverse -> un-applying the fix ->
            # bogus reward. There was no correct swesmith model-patch scoring path before.)
            _model_fwd = os.environ.get("SWE_SANDBOX_SCORE_MODEL_PATCH") == "1"
            if patch is not None and patch != '' and not _skip_gold:
                apply_patch(sandbox_root=self.deployment.root_dir,git_folder=self.deployment.git_folder,patch_str=patch,instance_id=self.deployment.root_dir,reverse=(not _model_fwd))
            if test_patch is not None:
                apply_patch(sandbox_root=self.deployment.root_dir,git_folder=self.deployment.git_folder,patch_str=test_patch,instance_id=self.deployment.root_dir,reverse=False)
            self.logger.info("Applied %s patch to repository (reverse=%s)", "MODEL" if _model_fwd else "GOLD", not _model_fwd)
        elif self.deployment._config.data_type=='swebench':
            if patch is not None and patch !='':
                apply_patch(sandbox_root=self.deployment.root_dir,git_folder=self.deployment.git_folder,patch_str=patch,instance_id=self.deployment.root_dir,reverse=False)

            if test_patch is not None:
                apply_patch(sandbox_root=self.deployment.root_dir,git_folder=self.deployment.git_folder,patch_str=test_patch,instance_id=self.deployment.root_dir,reverse=False)
            self.logger.info("Applied golden patch to repository")
        reward,f2p_dic,p2p_dic,output=self._calculate_reward(p2p=0,f2p=0)
        self.logger.debug("Pre-check reward: %s", reward)
        self._reset_repository()
        self.deployment.reset()
        return reward,f2p_dic,p2p_dic,output
        # if reward==0:
        #     print("Pre-check failed: f2p_dic:",f2p_dic,"p2p_dic:",p2p_dic)
        #     raise RuntimeError("Pre-check failed")
        # else:
        #     print("Pre-check passed")

        # reset the repository again to remove any changes made by the eval scripts


    def _reset_repository(self) -> None:
        """Clean repository of any modifications + Checkout base commit"""
        if self.repo is not None:
            self.logger.debug("Resetting repository %s to commit %s", self.repo.repo_name, self.repo.base_commit)
            # todo: Currently has swe-ft specific change: The original repo.copy isn't called, because the repo is already
            # present. However, reset --hard <BRANCH> also doesn't work. So modified it here to do a checkout instead.
            startup_commands = [
                f"cd {self.deployment.sandbox_path('/' + self.repo.git_folder)}",
                "export ROOT=$(pwd -P)",
                *self.repo.get_reset_commands(),
            ]
            try_count=6
            count=0
            while count<try_count:
                try:
                    self.communicate(
                        input=" && ".join(startup_commands),
                        check="raise",
                        error_msg="Failed to clean repository",
                        # Sometimes this is slow because it rebuilds some index
                        timeout=120,
                    )
                    break
                except Exception as e:
                    count+=1
                    if count==try_count:
                        raise e


        # if self.deployment._config.data_type=='swesmith':
        #     patch=self.deployment.ds['patch']
        #     apply_patch(sandbox_root=self.deployment.root_dir,git_folder=self.deployment.git_folder,patch_str=patch,instance_id=self.deployment.root_dir,reverse=False)
        #     self.logger.info("Applied patch to repository")
        clean_diff_commands = [
        f"cd {self.deployment.sandbox_path('/' + self.deployment.git_folder)}",
        "git config user.email setup@swebench.config",
        "git config user.name SWE-bench",
        "git commit --allow-empty -am SWE-bench",
        ]
        self.communicate(
            input=" && ".join(clean_diff_commands),
            check="raise",
            error_msg="Failed to clean repository",
            # Sometimes this is slow because it rebuilds some index
            timeout=120,
        )
    def close(self) -> None:

        self.logger.info("Beginning environment shutdown...")
        asyncio.run(self.deployment.stop())
        self._chook.on_close()

    # MARK: Helper functions #

    def _init_deployment(
        self,
    ) -> None:
        """
        Handles initialization. Creates the runtime and starts a session.
        """
        session_create_time = time.time()
        #self._chook.on_start_deployment()
        asyncio.run(self.deployment.start())
        startup_cmd = self.deployment.startup()
        self._create_default_session(startup_cmd)
        session_end_time = time.time()
        time_data={"session_duration": session_end_time - session_create_time,"session_start_time":session_create_time,"session_end_time":session_end_time}

        env_setup_time = time.time()
        env_defaults = self._default_shell_env()
        # Under heavier no-chroot parallelism, we occasionally see the interactive
        # shell exit immediately after create_session() but before the first command.
        # Recreate the default session once before giving up so transient EOFs do
        # not turn into permanent per-instance failures.
        for attempt in range(2 if not self.deployment._config.use_chroot else 1):
            try:
                self._apply_default_shell_env(env_defaults)
                break
            except Exception:
                if attempt >= 1 or self.deployment._config.use_chroot:
                    raise
                self.logger.warning("Default shell died during env bootstrap, recreating session once")
                self._recover_default_session(startup_cmd=startup_cmd)
        env_setup_end_time = time.time()
        time_data["env_setup_duration"]=env_setup_end_time - env_setup_time
        self.logger.info("Environment Initialized")
        return time_data

    def _create_default_session(self, startup_cmd: str) -> None:
        startup_source = ["/path/to/root/.bashrc"] if self.deployment._config.use_chroot else []
        asyncio.run(
            self.deployment.runtime.create_session(
                CreateSandboxBashSessionRequest(
                    startup_source=startup_source,
                    startup_timeout=60,
                    startup_cmd=startup_cmd,
                )
            )
        )

    def _close_default_session(self) -> None:
        with contextlib.suppress(Exception):
            asyncio.run(self.deployment.runtime.close_session(CloseBashSessionRequest()))

    def _default_shell_env(self) -> dict[str, str]:
        return {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PIP_PROGRESS_BAR": "off", "PAGER": "cat"}

    def _apply_default_shell_env(self, env_variables: dict[str, str]) -> None:
        if not env_variables:
            return
        _env_setters = [f"export {k}={shlex.quote(str(v))}" for k, v in env_variables.items()]
        command = " && ".join(_env_setters)
        asyncio.run(self.deployment.runtime.run_in_session(BashAction(command=command, timeout=25, check="raise")))

    def _recover_default_session(self, *, startup_cmd: str | None = None) -> None:
        if startup_cmd is None:
            startup_cmd = self.deployment.startup()
        self._close_default_session()
        self._create_default_session(startup_cmd)
        self._apply_default_shell_env(self._default_shell_env())
        install_commands = getattr(self.deployment, "_install_commands", None)
        if callable(install_commands):
            with contextlib.suppress(Exception):
                install_commands()

    @staticmethod
    def _is_default_session_lost_error(exc: Exception) -> bool:
        if isinstance(exc, (SessionNotInitializedError, SessionDoesNotExistError)):
            return True
        return "shell not initialized" in str(exc).lower()
    def interrupt_session(self):
        self.logger.info("Interrupting session")
        asyncio.run(self.deployment.runtime.run_in_session(BashInterruptAction()))

    # todo: return exit code?
    def communicate(
        self,
        input: str,
        timeout: int | float = 25,
        *,
        check: Literal["warn", "ignore", "raise"] = "ignore",
        error_msg: str = "Command failed",
    ) -> str:
        """Executes a command in the running shell. The details of this are handled by
        the SWE-ReX deployment/runtime.

        Args:
            input: input to send to container
            timeout_duration: duration to wait for output
            check: `ignore`: do not extract exit code (more stable), `warn`: extract exit code and log error if
                exit code is non-zero, `raise`: raise error if exit code is non-zero
            error_msg: error message to raise if the command fails

        Returns:
            output: output from gym environment
        """
        root_dir = self.deployment._config.root_dir.rstrip("/")
        if root_dir not in input:
            input = self.deployment._rewrite_script_paths(input)
        self.logger.log(logging.TRACE, "Input:\n%s", input)  # type: ignore
        rex_check = "ignore" if check == "ignore" else "silent"
        action = BashAction(command=input, timeout=timeout, check=rex_check)
        try:
            r = asyncio.run(self.deployment.runtime.run_in_session(action))
        except Exception as e:
            if not self._is_default_session_lost_error(e):
                raise
            self.logger.warning("Default shell unavailable during command execution, recreating session once")
            self._recover_default_session()
            r = asyncio.run(self.deployment.runtime.run_in_session(action))
        output ='\n'.join(r.output.split('\n')[:-1])
        output = self.deployment.rewrite_observation_paths(output)

        self.logger.log(logging.TRACE, "Output:\n%s", output)  # type: ignore
        if check != "ignore" and r.exit_code != 0:
            self.logger.error(f"{error_msg}:\n{output}")
            msg = f"Command {input!r} failed ({r.exit_code=}): {error_msg}"
            self.logger.error(msg)
            if check == "raise":
                self.close()
                raise RuntimeError(msg)
        return output
    def get_patch(self,dir="/res.patch"):
        """Get patch from mini-sandbox environment"""
        self.deployment.get_patch(dir)

    def read_file(self, path: str | PurePath, encoding: str | None = None, errors: str | None = None) -> str:
        tg_path = self.deployment.sandbox_path(str(path))
        r = asyncio.run(
            self.deployment.runtime.read_file(ReadFileRequest(path=str(tg_path), encoding=encoding, errors=errors))
        )
        return r.content

    def write_file(self, path: str | PurePath, content: str) -> None:
        tg_path = self.deployment.sandbox_path(str(path))
        asyncio.run(self.deployment.runtime.write_file(WriteFileRequest(path=str(tg_path), content=content)))

    def set_env_variables(self, env_variables: dict[str, str]) -> None:
        """Set environment variables in the environment."""
        if not env_variables:
            self.logger.debug("No environment variables to set")
            return
        _env_setters = [f"export {k}={shlex.quote(str(v))}" for k, v in env_variables.items()]
        command = " && ".join(_env_setters)
        self.communicate(command, check="raise")
    def _calculate_reward(self,p2p=1,f2p=1):
        """Calculate reward for the environment"""
        try:
            return self.deployment._calculate_reward(p2p=p2p,f2p=f2p)
        except Exception:
            self.logger.warning("Reward calculation failed; recovering default shell before repository reset")
            with contextlib.suppress(Exception):
                self._recover_default_session()
            return 0.0,{},{},''
    def execute_command(
        self,
        command: str,
        shell: bool = True,
        check: bool = False,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:

        asyncio.run(
            self.deployment.runtime.execute(RexCommand(command=command, shell=shell, check=check, env=env, cwd=cwd))
        )

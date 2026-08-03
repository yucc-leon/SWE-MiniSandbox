"""SkyRL-Gym environment wrapping SWE-MiniSandbox's SWEsbEnv (venv+conda+chroot, container-free)
for RL on swesmith-style instances.

Lives under SWE-MiniSandbox/sh/rl/ (SkyRL tree is read-only); register at runtime via
skyrl_gym.register(id="swe_minisandbox", entry_point="swe_minisandbox_env:SWEMiniSandboxEnv")
with this dir on PYTHONPATH (see register_swe_env.py).

Design (memory rl-skyrl-wiring-spec):
- One rollout = one instance. Policy LM emits ```mswea_bash_command``` blocks; we execute them in
  the per-instance SWEsbEnv, feed stdout back as observation (mini-swe-agent semantics).
- Per-step reward = 0. Final reward (submit / max_turns) = score the agent patch via the SAME path
  as eval/data-gen: reset repo -> inject patch as ds['patch'] -> pre_check() with
  SWE_SANDBOX_SCORE_MODEL_PATCH=1 -> _calculate_reward (== swesmith_score.sh; needs the FIXED scorer).
- Instances + tool bundles loaded ONCE (module cache) via the RunBatchConfig path used by
  sh/run_minisweagent_gen.py, looked up by instance_id from `extras`.
"""
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

try:
    from omegaconf import DictConfig  # SkyRL passes an OmegaConf DictConfig at train time
except ImportError:  # standalone tests may run without omegaconf; a plain dict works identically
    DictConfig = dict  # type: ignore
from skyrl_gym.envs.base_text_env import BaseTextEnv, BaseTextEnvStepOutput, ConversationType

logger = logging.getLogger(__name__)

SUBMIT_MARKER = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
_BASH_RE = re.compile(r"```(?:mswea_bash_command|bash)\s*\n(.*?)```", re.DOTALL)

_INSTANCES: Optional[Dict[str, Any]] = None
_BUNDLES: Optional[List[Any]] = None


def _ensure_loaded(env_config: DictConfig) -> None:
    global _INSTANCES, _BUNDLES
    if _INSTANCES is not None:
        return
    from sweagent.run.run_batch import RunBatchConfig
    from sweagent.run.common import BasicCLI

    args = [
        "--config", env_config["eval_config_path"],
        "--instances.type", "swesmith",
        "--instances.path", env_config["instances_path"],
        "--instances.split", env_config.get("instances_split", "train"),
        "--instances.deployment.data_type", "swesmith",
        "--instances.start", "0",
        "--instances.end", str(env_config.get("instances_end", 59136)),
        "--instances.slice", env_config.get("instances_slice", ":100000"),
        "--instances.filter", env_config.get("instances_filter", ".*"),
        "--output_dir", env_config.get("output_dir", "/tmp/swe_rl_env"),
        "--instances.deployment.root_base", env_config["root_base"],
        "--instances.deployment.git_base_path", env_config["git_base_path"],
        "--instances.deployment.shared_venv", env_config["shared_venv"],
        "--instances.deployment.wheelhouse", env_config["wheelhouse"],
        "--instances.deployment.conda_env", env_config["conda_env"],
        "--instances.deployment.tool_path", env_config["tool_path"],
        "--instances.deployment.use_chroot", str(env_config.get("use_chroot", True)).lower(),
    ]
    config = BasicCLI(RunBatchConfig).get_config(args)
    insts = config.instances.get_instance_configs()
    _INSTANCES = {inst.problem_statement.id: inst for inst in insts}
    _BUNDLES = config.agent.tools.bundles
    logger.info(f"[swe_minisandbox] loaded {len(_INSTANCES)} instances; bundles={len(_BUNDLES)}")


class SWEMiniSandboxEnv(BaseTextEnv):
    def __init__(self, env_config: DictConfig, extras: Dict[str, Any] = {}):
        super().__init__()
        self.env_config = env_config
        self.instance_id = extras.get("instance_id") or extras.get("reward_spec", {}).get("ground_truth")
        self.max_turns = int(extras.get("max_turns", 40))
        self.step_timeout = int(env_config.get("step_timeout", 120))
        self._sbenv = None
        self._msb = None
        self._submitted_patch: Optional[str] = None
        self.chat_history: ConversationType = []

    def init(self, prompt: ConversationType) -> Tuple[ConversationType, Dict[str, Any]]:
        _ensure_loaded(self.env_config)
        if self.instance_id not in _INSTANCES:
            raise KeyError(f"instance_id {self.instance_id!r} not loaded ({len(_INSTANCES)} known)")
        from sweagent.environment.swe_sbenv import SWEsbEnv
        from swesandbox.minisweagent_env import MiniSandboxEnvironment

        inst = _INSTANCES[self.instance_id]
        self._sbenv = SWEsbEnv.from_config(ds=inst.ds, bundles=_BUNDLES, config=inst.env)
        self._sbenv.start()
        self._msb = MiniSandboxEnvironment(self._sbenv)
        self.chat_history = list(prompt)
        return prompt, {"instance_id": self.instance_id}

    def close(self):
        if self._sbenv is not None:
            try:
                self._sbenv.close()
            except Exception as e:
                logger.warning(f"[swe_minisandbox] close failed: {e}")
        self._sbenv = None
        self._msb = None

    def _parse_bash(self, action: str) -> Optional[str]:
        m = _BASH_RE.search(action)
        return m.group(1).strip() if m else None

    def step(self, action: str) -> BaseTextEnvStepOutput:
        self.turns += 1
        self.chat_history.append({"role": "assistant", "content": action})

        done = False
        observation = ""
        cmd = self._parse_bash(action)
        if cmd is None:
            observation = ("No bash command found. Respond with exactly one "
                           "```mswea_bash_command\\n<cmd>\\n``` block.")
        else:
            try:
                result = self._msb.execute({"command": cmd}, timeout=self.step_timeout)
                observation = result.get("output", "") or ""
            except Exception as e:
                if type(e).__name__ == "Submitted":
                    self._submitted_patch = str((getattr(e, "args", [""]) or [""])[0] or "")
                    done = True
                    observation = "[submitted]"
                else:
                    observation = f"[error] {type(e).__name__}: {e}"

        if self.turns >= self.max_turns:
            done = True

        reward = self._score() if done else 0.0
        new_obs = {"role": "user", "content": observation}
        if not done:
            self.chat_history.append(new_obs)
        return BaseTextEnvStepOutput(
            observations=[new_obs] if not done else [],
            reward=reward,
            done=done,
            metadata={"instance_id": self.instance_id, "turns": self.turns},
        )

    def _extract_patch(self) -> str:
        if self._submitted_patch and "diff --git" in self._submitted_patch:
            return self._submitted_patch[self._submitted_patch.index("diff --git"):]
        try:
            out = self._msb.execute({"command": "cd /testbed && git diff HEAD"}, timeout=60).get("output", "")
            return out[out.index("diff --git"):] if "diff --git" in out else ""
        except Exception as e:
            logger.warning(f"[swe_minisandbox] patch extract failed: {e}")
            return ""

    def _score(self) -> float:
        patch = self._extract_patch()
        if not patch.strip():
            return 0.0
        try:
            self._sbenv._reset_repository()
            self._sbenv.deployment.ds["patch"] = patch
            r, _f2p, _p2p, _out = self._sbenv.pre_check()
            return float(r)
        except Exception as e:
            logger.warning(f"[swe_minisandbox] scoring failed for {self.instance_id}: {e}")
            return 0.0

    def get_metrics(self) -> Dict[str, Any]:
        return {"num_steps": self.turns}

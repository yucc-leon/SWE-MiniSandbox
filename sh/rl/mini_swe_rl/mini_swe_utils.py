"""Adapted from SkyRL examples/mini_swe_agent/mini_swe_utils.py.

Adds a CONTAINER-FREE branch (environment_class == "minisandbox") that runs the rollout in our
SWEsbEnv (venv+conda+chroot) and scores via our faithfulness pre_check path (the FIXED scorer:
empty patch -> 0), instead of docker + an eval_script. The original docker/singularity path is
preserved for SWE-Gym/SWE-bench.

Config (sweagent_config["minisandbox"]) carries the deployment params; instances are loaded ONCE
via the same RunBatchConfig path as sh/run_minisweagent_gen.py and looked up by instance_id.
"""
from typing import TypedDict, Optional, Dict, Any
import os
import traceback
import uuid

from loguru import logger
from jinja2 import Template

from minisweagent.environments import Environment, get_environment


class MiniSWEEvaluationResult(TypedDict):
    instance_id: str
    resolved: bool
    eval_error: Optional[str]


# ---- container-free (minisandbox) instance loading: built once, shared across rollouts ----
_INSTANCES: Optional[Dict[str, Any]] = None
_BUNDLES = None


def _ensure_loaded(ms: dict) -> None:
    global _INSTANCES, _BUNDLES
    if _INSTANCES is not None:
        return
    from sweagent.run.run_batch import RunBatchConfig
    from sweagent.run.common import BasicCLI

    args = [
        "--config", ms["eval_config_path"],
        "--instances.type", "swesmith",
        "--instances.path", ms["instances_path"],
        "--instances.split", ms.get("instances_split", "train"),
        "--instances.deployment.data_type", "swesmith",
        "--instances.start", "0", "--instances.end", str(ms.get("instances_end", 59136)),
        "--instances.slice", ms.get("instances_slice", ":100000"),
        "--instances.filter", ms.get("instances_filter", ".*"),
        "--output_dir", ms.get("output_dir", "/tmp/swe_rl_env"),
        "--instances.deployment.root_base", ms["root_base"],
        "--instances.deployment.git_base_path", ms["git_base_path"],
        "--instances.deployment.shared_venv", ms["shared_venv"],
        "--instances.deployment.wheelhouse", ms["wheelhouse"],
        "--instances.deployment.conda_env", ms["conda_env"],
        "--instances.deployment.tool_path", ms["tool_path"],
        "--instances.deployment.use_chroot", str(ms.get("use_chroot", True)).lower(),
    ]
    config = BasicCLI(RunBatchConfig).get_config(args)
    insts = config.instances.get_instance_configs()
    _INSTANCES = {i.problem_statement.id: i for i in insts}
    _BUNDLES = config.agent.tools.bundles
    logger.info(f"[minisandbox] loaded {len(_INSTANCES)} instances; bundles={len(_BUNDLES)}")


def _make_minisandbox_env(config: dict, instance: dict):
    from sweagent.environment.swe_sbenv import SWEsbEnv
    from swesandbox.minisweagent_env import MiniSandboxEnvironment

    _ensure_loaded(config["minisandbox"])
    inst = _INSTANCES[instance["instance_id"]]
    sbenv = SWEsbEnv.from_config(ds=inst.ds, bundles=_BUNDLES, config=inst.env)
    sbenv.start()
    msb = MiniSandboxEnvironment(sbenv)
    msb._sbenv = sbenv
    return msb


def get_sb_environment(config: dict, instance: dict, data_source: str) -> Environment:
    env_config = config.setdefault("environment", {})
    env_config["environment_class"] = env_config.get("environment_class", "docker")

    if env_config["environment_class"] == "minisandbox":
        return _make_minisandbox_env(config, instance)

    # ---- original docker / singularity path (SWE-Gym / SWE-bench) ----
    image_name = get_docker_image_name(instance, data_source)
    if env_config["environment_class"] == "docker":
        env_config["image"] = image_name
    elif env_config["environment_class"] == "singularity":
        env_config["image"] = f"docker://{image_name}"
    env = get_environment(env_config)
    if startup_command := config.get("run", {}).get("env_startup_command"):
        startup_command = Template(startup_command).render(**instance)
        out = env.execute(startup_command)
        if out["returncode"] != 0:
            raise RuntimeError(f"Error executing startup command: {out}")
    return env


def get_docker_image_name(instance: dict, data_source: str) -> str:
    image_name = instance.get("image_name", None)
    if image_name is None:
        iid = instance["instance_id"]
        if "swe-gym" in data_source.lower():
            id_docker_compatible = iid.replace("__", "_s_")
            image_name = f"docker.io/xingyaoww/sweb.eval.x86_64.{id_docker_compatible}:latest".lower()
        elif "swe-bench" in data_source.lower():
            id_docker_compatible = iid.replace("__", "_1776_")
            image_name = f"docker.io/swebench/sweb.eval.x86_64.{id_docker_compatible}:latest".lower()
        else:
            raise NotImplementedError(f"Data source: {data_source} is not supported")
    return image_name


def evaluate_trajectory(
    instance: Dict[str, Any], model_patch: str, sweagent_config: dict, data_source: str
) -> MiniSWEEvaluationResult:
    ret = MiniSWEEvaluationResult(instance_id=instance["instance_id"], resolved=False, eval_error=None)
    env_class = sweagent_config.get("environment", {}).get("environment_class", "docker")

    # ---- container-free scorer: fresh SWEsbEnv + inject patch + pre_check (== swesmith_score.sh) ----
    if env_class == "minisandbox":
        from sweagent.environment.swe_sbenv import SWEsbEnv
        os.environ["SWE_SANDBOX_SCORE_MODEL_PATCH"] = "1"
        os.environ.setdefault("SWE_RELINK_EDITABLE", "1")
        _ensure_loaded(sweagent_config["minisandbox"])
        inst = _INSTANCES[instance["instance_id"]]
        sbenv = None
        try:
            sbenv = SWEsbEnv.from_config(ds=inst.ds, bundles=_BUNDLES, config=inst.env)
            sbenv.start()
            sbenv.deployment.ds["patch"] = model_patch or ""  # empty -> not applied -> buggy -> 0 (canary)
            r, _f2p, _p2p, _out = sbenv.pre_check()
            ret["resolved"] = bool(int(r) == 1)
        except Exception as e:
            ret["eval_error"] = f"{type(e).__name__}: {e}"
            logger.debug(f"[minisandbox] eval failed: {traceback.format_exc()}")
        finally:
            if sbenv is not None:
                try:
                    sbenv.close()
                except Exception:
                    pass
        return ret

    # ---- original docker eval path ----
    env = None
    try:
        env = get_sb_environment(sweagent_config, instance, data_source)
    except Exception as e:
        ret["eval_error"] = f"Env creation failed with {e}"
        return ret
    delimiter = f"PATCH_{uuid.uuid4().hex}"
    command = f"git apply <<'{delimiter}'\n{model_patch}\n{delimiter}"
    obs = env.execute(command)
    if obs["returncode"] != 0:
        ret["eval_error"] = obs["output"]
    else:
        eval_script = instance["eval_script"]
        eval_cmd = f"bash <<'EOF'\n{eval_script}\nEOF"
        obs = env.execute(eval_cmd, timeout=3600)
        ret["resolved"] = obs["returncode"] == 0
        ret["eval_error"] = (f"(truncated)\n{obs['output'][-1000:]}" if not ret["resolved"] else None)
    return ret

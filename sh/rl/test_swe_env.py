"""Standalone test of SWEMiniSandboxEnv (no SkyRL trainer / no NPU).
Validates: build -> init(deploy) -> step(exec bash) -> observation flow -> done(submit) -> reward path.
Empty-fix submit MUST score 0 (canary). Run via tp_rl_envtest.yaml."""
import os
import sys

ROOT = "/path/to/SWE-MiniSandbox"
sys.path.insert(0, f"{ROOT}/sh/rl")
os.environ.setdefault("SWE_SANDBOX_SCORE_MODEL_PATCH", "1")
os.environ.setdefault("SWE_RELINK_EDITABLE", "1")
os.environ.setdefault("SWESMITH_FULL_DEPS", "1")

from swe_minisandbox_env import SWEMiniSandboxEnv

INST = os.environ.get("INST", "Mimino666__langdetect.a1598f1a.combine_file__c24mxoqs")
POOL = os.environ.get("POOL", f"{ROOT}/vendor/swesmith-cache/rl-envtest")
hsh = INST.rsplit("__", 1)[1]

env_config = ({
    "eval_config_path": f"{ROOT}/config/swesmith_infer.yaml",
    "instances_path": f"{ROOT}/dataset/SWE-smith",
    "instances_filter": f".*__({hsh})",
    "instances_slice": ":3",
    "root_base": f"{POOL}/sandbox", "git_base_path": f"{POOL}/gitcache",
    "shared_venv": f"{POOL}/shared_venv", "wheelhouse": f"{ROOT}/vendor/minisandbox-wheelhouse",
    "conda_env": "/path/to/workspace/minisandbox-conda", "tool_path": f"{ROOT}/SWE-agent/tools",
    "use_chroot": True, "output_dir": f"{POOL}/out", "step_timeout": 120,
})

print(f"=== TEST env on {INST} ===", flush=True)
env = SWEMiniSandboxEnv(env_config, {"instance_id": INST, "max_turns": 5})
prompt = [{"role": "user", "content": "Fix the bug in the repo at /testbed."}]
obs, meta = env.init(prompt)
print(f"[init] ok meta={meta}", flush=True)

o1 = env.step("```mswea_bash_command\nls /testbed && echo '--- explore OK ---'\n```")
c1 = o1["observations"][0]["content"][:200] if o1["observations"] else ""
print(f"[step1] done={o1['done']} reward={o1['reward']} obs={c1!r}", flush=True)

# submit with NO real fix -> reward MUST be 0 (negative-control canary)
o2 = env.step("```mswea_bash_command\ncd /testbed && echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && git diff HEAD\n```")
print(f"[step2/submit] done={o2['done']} reward={o2['reward']}", flush=True)
env.close()

ok = (meta.get("instance_id") == INST) and ("explore OK" in c1) and o2["done"] and (o2["reward"] == 0.0)
print(f"[RESULT] env-mechanics={'PASS' if ('explore OK' in c1 and o2['done']) else 'FAIL'} "
      f"| canary(empty->0)={'PASS' if o2['reward']==0.0 else 'FAIL got '+str(o2['reward'])} "
      f"| OVERALL={'PASS' if ok else 'FAIL'}", flush=True)

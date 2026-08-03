"""Serial pre-warm of per-instance venvs before an NROLL>1 RL run.

Root cause it fixes: NROLL=8 fires 8 same-instance rollouts concurrently; each runs
`python -m venv` + editable relink into the SAME shared_venv path → stomp (Errno 39 Directory
not empty / venv exit 1) → 29/32 rollouts fail → empty trajectories → GRPO count mismatch.

This builds each instance's env ONCE, serially (no concurrency), so the build+venv cache is
populated. The subsequent concurrent RL rollouts then find the venv ready and skip the build.

Reads the same minisandbox config the RL run uses; deploys each train+val instance once via
SWEsbEnv.start() then closes. Idempotent: already-built venvs are reused.
"""
import os
import sys
import time

ROOT = "/path/to/SWE-MiniSandbox"
os.environ.setdefault("SWESMITH_FULL_DEPS", "1")
os.environ.setdefault("SWE_RELINK_EDITABLE", "1")

import yaml
from sweagent.run.run_batch import RunBatchConfig
from sweagent.run.common import BasicCLI

cfg = yaml.safe_load(open(f"{ROOT}/sh/rl/mini_swe_rl/minisandbox_swesmith.yaml"))
ms = cfg["minisandbox"]
ids = [l.strip() for l in open(f"{ROOT}/vendor/rl-data-ids-train.txt") if l.strip()]
ids += [l.strip() for l in open(f"{ROOT}/vendor/rl-data-ids-val.txt") if l.strip()]
ids = list(dict.fromkeys(ids))  # dedup, keep order
hsh = "|".join(i.rsplit("__", 1)[1] for i in ids)

args = [
    "--config", ms["eval_config_path"],
    "--instances.type", "swesmith",
    "--instances.path", ms["instances_path"],
    "--instances.split", ms.get("instances_split", "train"),
    "--instances.deployment.data_type", "swesmith",
    "--instances.start", "0", "--instances.end", str(ms.get("instances_end", 59136)),
    "--instances.slice", f":{len(ids)+5}",
    "--instances.filter", f".*__({hsh})",
    "--output_dir", f"{ms.get('output_dir','/tmp/swe_rl_env')}/prewarm",
    "--instances.deployment.root_base", ms["root_base"],
    "--instances.deployment.git_base_path", ms["git_base_path"],
    "--instances.deployment.shared_venv", ms["shared_venv"],
    "--instances.deployment.wheelhouse", ms["wheelhouse"],
    "--instances.deployment.conda_env", ms["conda_env"],
    "--instances.deployment.tool_path", ms["tool_path"],
    "--instances.deployment.use_chroot", str(ms.get("use_chroot", False)).lower(),
]
config = BasicCLI(RunBatchConfig).get_config(args)
insts = {i.problem_statement.id: i for i in config.instances.get_instance_configs()}
bundles = config.agent.tools.bundles
print(f"[prewarm] {len(ids)} instances to warm; loaded {len(insts)}", flush=True)

from sweagent.environment.swe_sbenv import SWEsbEnv

ok, fail = 0, 0
for n, iid in enumerate(ids, 1):
    inst = insts.get(iid)
    if inst is None:
        print(f"[prewarm] {n}/{len(ids)} MISSING {iid}", flush=True); fail += 1; continue
    t0 = time.time()
    env = None
    try:
        env = SWEsbEnv.from_config(ds=inst.ds, bundles=bundles, config=inst.env)
        env.start()  # builds venv + relink -> populates the shared cache
        ok += 1
        print(f"[prewarm] {n}/{len(ids)} OK {iid.split('.')[0]} ({time.time()-t0:.0f}s)", flush=True)
    except Exception as e:
        fail += 1
        print(f"[prewarm] {n}/{len(ids)} FAIL {iid.split('.')[0]}: {type(e).__name__}: {str(e)[:120]}", flush=True)
    finally:
        if env is not None:
            try: env.close()
            except Exception: pass
print(f"[prewarm] DONE ok={ok} fail={fail}", flush=True)

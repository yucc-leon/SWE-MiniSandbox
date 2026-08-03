"""Build SkyRL-format RL data parquet from our faithful swesmith instances.
Row schema (matches upstream preprocess_swegym.py):
  {data_source, prompt:[{role:user,content:problem_statement}], env_class:"null", instance:{...}}
The mini_swe generator reads `instance` (needs instance_id + problem_statement); our adapted
get_sb_environment/evaluate_trajectory load the full instance by instance_id via RunBatchConfig.
Run locally in swe-sandbox (loads instance metadata only, no deploy)."""
import os
import sys

ROOT = "/path/to/SWE-MiniSandbox"
import pandas as pd

train_ids = [l.strip() for l in open(f"{ROOT}/vendor/rl-data-ids-train.txt") if l.strip()]
val_ids = [l.strip() for l in open(f"{ROOT}/vendor/rl-data-ids-val.txt") if l.strip()]
all_ids = train_ids + val_ids
hsh = "|".join(i.rsplit("__", 1)[1] for i in all_ids)

from sweagent.run.run_batch import RunBatchConfig
from sweagent.run.common import BasicCLI

args = [
    "--config", f"{ROOT}/config/swesmith_infer.yaml",
    "--instances.type", "swesmith",
    "--instances.path", f"{ROOT}/dataset/SWE-smith",
    "--instances.split", "train",
    "--instances.deployment.data_type", "swesmith",
    "--instances.start", "0", "--instances.end", "59136",
    "--instances.slice", f":{len(all_ids)+5}",
    "--instances.filter", f".*__({hsh})",
    "--output_dir", "/tmp/rl_data_build",
    "--instances.deployment.root_base", "/tmp/p/sandbox",
    "--instances.deployment.git_base_path", "/tmp/p/gitcache",
    "--instances.deployment.shared_venv", "/tmp/p/shared_venv",
    "--instances.deployment.wheelhouse", f"{ROOT}/vendor/minisandbox-wheelhouse",
    "--instances.deployment.conda_env", "/path/to/workspace/minisandbox-conda",
    "--instances.deployment.tool_path", f"{ROOT}/SWE-agent/tools",
]
config = BasicCLI(RunBatchConfig).get_config(args)
insts = {i.problem_statement.id: i for i in config.instances.get_instance_configs()}
print(f"loaded {len(insts)} instances for {len(all_ids)} requested")


def rows(ids, data_source):
    out = []
    for iid in ids:
        inst = insts.get(iid)
        if inst is None:
            print(f"  MISSING {iid}")
            continue
        ps = inst.problem_statement.get_problem_statement()
        out.append({
            "data_source": data_source,
            "prompt": [{"role": "user", "content": ps}],
            "env_class": "null",
            "instance": {"instance_id": iid, "problem_statement": ps},
        })
    return out


outdir = f"{ROOT}/vendor/rl-data"
os.makedirs(outdir, exist_ok=True)
tr = rows(train_ids, "swesmith")
va = rows(val_ids, "swesmith")
pd.DataFrame(tr).to_parquet(f"{outdir}/train.parquet")
pd.DataFrame(va).to_parquet(f"{outdir}/validation.parquet")
print(f"WROTE train={len(tr)} val={len(va)} -> {outdir}/{{train,validation}}.parquet")
print("sample prompt[:120]:", tr[0]["prompt"][0]["content"][:120] if tr else "EMPTY")

#!/usr/bin/env python
"""Diagnose the post-rollout `_reset_repository` failure (exit 128) and find a reset that
ACTUALLY cleans the tree, then time a reset+test cycle (the RL per-rollout env cost).
Reuses run_minisweagent_gen's config-build for 1 instance. Read env: EVAL_CONFIG_PATH,
INSTANCE_PATH, INSTANCE_FILTER, RUNTIME_ROOT, GITCACHE_ROOT, SHARED_VENV_ROOT,
WHEELHOUSE_ROOT, CONDA_BACKEND_ROOT.
"""
import os, time
from pathlib import Path
ROOT = Path("/path/to/SWE-MiniSandbox")

def main():
    from sweagent.run.run_batch import RunBatchConfig
    from sweagent.run.common import BasicCLI
    from sweagent.environment.swe_sbenv import SWEsbEnv

    rt = Path(os.environ["RUNTIME_ROOT"])
    args = ["--config", os.environ["EVAL_CONFIG_PATH"],
            "--instances.type", "swesmith", "--instances.path", os.environ["INSTANCE_PATH"],
            "--instances.split", "train", "--instances.deployment.data_type", "swesmith",
            "--instances.start", "0", "--instances.end", "59136", "--instances.slice", ":1",
            "--instances.filter", os.environ["INSTANCE_FILTER"],
            "--output_dir", str(rt / "out"),
            "--instances.deployment.root_base", str(rt / "sandbox"),
            "--instances.deployment.git_base_path", os.environ["GITCACHE_ROOT"],
            "--instances.deployment.shared_venv", os.environ["SHARED_VENV_ROOT"],
            "--instances.deployment.wheelhouse", os.environ["WHEELHOUSE_ROOT"],
            "--instances.deployment.conda_env", os.environ["CONDA_BACKEND_ROOT"],
            "--instances.deployment.tool_path", str(ROOT / "SWE-agent/tools"),
            "--instances.deployment.use_chroot", "true"]
    config = BasicCLI(RunBatchConfig).get_config(args)
    instances = config.instances.get_instance_configs()
    bundles = config.agent.tools.bundles
    inst = instances[0]
    print(f"[diag] instance={inst.problem_statement.id}", flush=True)
    env = SWEsbEnv.from_config(ds=inst.ds, bundles=bundles, config=inst.env)
    t0 = time.time(); env.start(); print(f"[diag] deploy(start) took {time.time()-t0:.1f}s", flush=True)
    base = env.repo.base_commit
    print(f"[diag] base_commit={base}", flush=True)

    def sh(cmd):
        try:
            return env.communicate(input=cmd, timeout=120, check="ignore")
        except Exception as e:
            return f"<COMMUNICATE-ERR {type(e).__name__}: {e}>"

    print("=== INITIAL state ===\n" + sh("cd /testbed && git rev-parse --abbrev-ref HEAD; git status --short|head -5; git log --oneline -1"), flush=True)

    # ---- Scenario 1: agent edits tracked + leaves untracked scratch (no commit) ----
    print("\n=== MUCK-1: edit tracked + untracked scratch (no commit) ===", flush=True)
    print(sh("cd /testbed && f=$(git ls-files '*.py'|head -1); echo '# agentedit' >> \"$f\"; echo x>reproduce.py; echo y>scratch.txt; git status --short|head"), flush=True)
    print("--- standard _reset_repository() ---", flush=True)
    t = time.time()
    try:
        env._reset_repository(); print(f"STD RESET OK {time.time()-t:.1f}s | clean? ->" + sh("cd /testbed && git status --short|head"), flush=True)
    except Exception as e:
        print(f"STD RESET FAIL: {type(e).__name__}: {str(e)[:300]}", flush=True)
        print("git state now:\n" + sh("cd /testbed && git status|head -15; git log --oneline -3"), flush=True)

    # ---- Scenario 2: agent also COMMITS (some agents do git add+commit) ----
    print("\n=== MUCK-2: edit + git add -A + commit ===", flush=True)
    print(sh("cd /testbed && f=$(git ls-files '*.py'|head -1); echo '# e2' >> \"$f\"; echo x>repro2.py; git add -A; git -c user.email=a@b.c -c user.name=a commit -q -m wip; echo committed; git log --oneline -2"), flush=True)
    print("--- standard _reset_repository() ---", flush=True)
    t = time.time()
    std_ok = False
    try:
        env._reset_repository(); std_ok = True; print(f"STD RESET OK {time.time()-t:.1f}s | clean? ->" + sh("cd /testbed && git status --short|head"), flush=True)
    except Exception as e:
        print(f"STD RESET FAIL: {type(e).__name__}: {str(e)[:300]}", flush=True)
        print("git state:\n" + sh("cd /testbed && git status|head; git log --oneline -3; git branch -a|head"), flush=True)

    # ---- Candidate FORCEFUL reset (test if it cleans where standard failed) ----
    if not std_ok:
        print("\n=== FORCEFUL reset candidate ===", flush=True)
        print(sh(f"cd /testbed && git checkout -f {base} 2>&1|tail -2; echo checkout_rc=$?; git reset --hard {base} 2>&1|tail -1; echo reset_rc=$?; git clean -fdx 2>&1|tail -2; echo clean_rc=$?"), flush=True)
        print("clean now? (should be empty):\n" + sh("cd /testbed && git status --short|head; git log --oneline -1"), flush=True)

    # ---- Time a reset+test cycle (RL per-rollout env cost) using the canonical path ----
    print("\n=== TIME reset_swesmith_tests + reward (per-rollout env cost) ===", flush=True)
    try:
        t = time.time(); env.deployment.reset_swesmith_tests()
        r, f2p, p2p, _ = env._calculate_reward(p2p=0, f2p=0)
        print(f"reset_tests+calc_reward took {time.time()-t:.1f}s (reward={r})", flush=True)
    except Exception as e:
        print(f"reward-cycle err: {type(e).__name__}: {str(e)[:200]}", flush=True)
    env.close(); print("[diag] DONE", flush=True)

if __name__ == "__main__":
    main()

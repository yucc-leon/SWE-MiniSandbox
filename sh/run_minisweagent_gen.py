#!/usr/bin/env python
"""Generation harness: mini-swe-agent (DefaultAgent) running inside MiniSandbox.

Reuses the existing SWE-agent batch config to build a per-instance SWESBEnv
(repo at /testbed + venv + chroot), wraps it as a mini-swe-agent Environment,
and runs the minimalist bash-only agent against a local vLLM OpenAI endpoint.
Outputs preds.json (instance_id -> {model_patch}) + per-instance trajectory,
compatible with the existing MiniSandbox scoring.

Env vars:
  EVAL_CONFIG_PATH   SWE-agent batch config (chroot infer yaml)
  DATASET_DIR        SWE-bench Verified dataset dir
  INSTANCE_SLICE     e.g. ":1"
  OUT_DIR            output dir for preds.json + trajectories
  API_BASE           vLLM OpenAI endpoint (default http://127.0.0.1:8001/v1)
  MODEL_NAME         litellm model name (default openai/Qwen3-4B-Instruct-2507)
  MSWEA_AGENT_CONFIG mini-swe-agent agent yaml (default swebench_backticks.yaml)
"""
import json
import os
import sys
import traceback
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

EVAL_CONFIG_PATH = os.environ["EVAL_CONFIG_PATH"]
DATASET_DIR = os.environ.get("DATASET_DIR", str(ROOT / "dataset/SWE-bench/SWE-bench_Verified/data"))
INSTANCE_SLICE = os.environ.get("INSTANCE_SLICE", ":1")
INSTANCE_FILTER = os.environ.get("INSTANCE_FILTER", ".*")
OUT_DIR = Path(os.environ["OUT_DIR"])
API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8001/v1")
MODEL_NAME = os.environ.get("MODEL_NAME", "openai/Qwen3-4B-Instruct-2507")
MSWEA_AGENT_CONFIG = os.environ.get(
    "MSWEA_AGENT_CONFIG",
    str(ROOT / "mini-swe-agent/src/minisweagent/config/benchmarks/swebench_backticks.yaml"),
)
STEP_LIMIT = int(os.environ.get("STEP_LIMIT", "40"))
NUM_WORKERS = int(os.environ.get("NUM_WORKERS", "8"))
RUNTIME_ROOT = Path(os.environ.get("RUNTIME_ROOT", str(OUT_DIR.parent)))
WHEELHOUSE_ROOT = os.environ.get("WHEELHOUSE_ROOT", str(ROOT / "vendor/minisandbox-wheelhouse"))
CONDA_BACKEND_ROOT = os.environ.get("CONDA_BACKEND_ROOT", "/path/to/workspace/minisandbox-conda")
# Pods are offline -> repo/venv must come from a warm local cache (else deployment git-fetches github and fails).
GITCACHE_ROOT = os.environ.get("GITCACHE_ROOT", str(RUNTIME_ROOT / "gitcache"))
SHARED_VENV_ROOT = os.environ.get("SHARED_VENV_ROOT", str(RUNTIME_ROOT / "shared_venv"))
# Instance source: "swebench" (default, --instances.database file) or "swesmith"
# (--instances.type swesmith --instances.path <dir> --instances.split <split>). swesmith is
# the teacher-sampling train source; swebench Verified is the eval source.
INSTANCE_TYPE = os.environ.get("INSTANCE_TYPE", "swebench")
INSTANCE_PATH = os.environ.get("INSTANCE_PATH", str(ROOT / "dataset/SWE-smith"))
INSTANCE_SPLIT = os.environ.get("INSTANCE_SPLIT", "train")
INSTANCE_END = os.environ.get("INSTANCE_END", "")  # swesmith loader: load 0:END before filter

# litellm has no price-map entry for external teachers (e.g. glm-5.2) -> its post-response
# _calculate_cost raises RuntimeError AFTER a valid completion, killing the step before the
# patch is captured (observed: 3/3 empty patches, model was actually responding fine). Tell
# mini-swe-agent to ignore cost-tracking errors so unmapped models work.
os.environ.setdefault("MSWEA_COST_TRACKING", "ignore_errors")

OUT_DIR.mkdir(parents=True, exist_ok=True)


def log(msg):
    print(f"[mswea-gen] {msg}", flush=True)


def _extract_diff(text):
    """Return a clean unified diff from `text` (working-tree git diff OR the agent's patch.txt
    submission), or '' if it has no diff. Slices from the first 'diff --git' so any submit-echo
    preamble (e.g. 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT') or trailing shell prompt is dropped."""
    if not text or not text.strip():
        return ""
    idx = text.find("diff --git ")
    if idx == -1:
        return ""
    return text[idx:].strip() + "\n"


def _last_diff_in_messages(messages):
    """Fallback capture: scan tool-result messages for the LAST clean source diff (the agent's
    own `git diff` output near the end of the rollout). Needed because the post-run `git diff HEAD`
    capture sometimes comes back EMPTY even though the agent's edit IS in the working tree and its
    own mid-rollout `git diff` showed it (confirmed: clean-protocol gate sample captured only 3/18
    live but messages held real diffs that scored 16/18 resolve). Skips diffs touching scratch
    files (patch.txt/reproduce/test_repro) the submission protocol forbids. Scoring is the final
    filter, so an occasional stale grab is harmless (won't apply -> not counted resolved)."""
    best = ""
    for m in messages:
        c = m.get("content", "")
        if not isinstance(c, str):
            continue
        d = _extract_diff(c)
        if not d:
            continue
        if any(f"a/{x}" in d.split("\n", 1)[0] or f"+++ b/{x}" in d
               for x in ("patch.txt", "reproduce", "test_repro")):
            continue
        best = d  # keep the LAST (final-state) clean diff
    return best


def main():
    from sweagent.run.run_batch import RunBatchConfig
    from sweagent.run.common import BasicCLI
    from sweagent.environment.swe_sbenv import SWEsbEnv
    from swesandbox.minisweagent_env import MiniSandboxEnvironment
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.models import get_model

    log(f"loading batch config: {EVAL_CONFIG_PATH} (instance_type={INSTANCE_TYPE})")
    args = ["--config", EVAL_CONFIG_PATH]
    if INSTANCE_TYPE == "swesmith":
        args += [
            "--instances.type", "swesmith",
            "--instances.path", INSTANCE_PATH,
            "--instances.split", INSTANCE_SPLIT,
            "--instances.deployment.data_type", "swesmith",
        ]
        if INSTANCE_END:
            args += ["--instances.start", "0", "--instances.end", INSTANCE_END]
    else:
        args += ["--instances.database", DATASET_DIR]
    args += [
        "--instances.slice", INSTANCE_SLICE,
        "--instances.filter", INSTANCE_FILTER,
        "--output_dir", str(OUT_DIR),
        "--instances.deployment.root_base", str(RUNTIME_ROOT / "sandbox"),
        "--instances.deployment.git_base_path", GITCACHE_ROOT,
        "--instances.deployment.shared_venv", SHARED_VENV_ROOT,
        "--instances.deployment.wheelhouse", WHEELHOUSE_ROOT,
        "--instances.deployment.conda_env", CONDA_BACKEND_ROOT,
        "--instances.deployment.tool_path", str(ROOT / "SWE-agent/tools"),
    ]
    config = BasicCLI(RunBatchConfig).get_config(args)
    instances = config.instances.get_instance_configs()
    bundles = config.agent.tools.bundles
    log(f"instances loaded: {len(instances)}; bundles: {[getattr(b,'path',b) for b in bundles]}")

    # mini-swe-agent agent + model config from the backticks yaml (text-based, NO tool-calling).
    # Must use LitellmTextbasedModel: it sends no `tools` (so vLLM doesn't need a tool-call
    # parser) and regex-parses ```mswea_bash_command``` blocks, matching the backticks agent.
    agent_yaml = yaml.safe_load(open(MSWEA_AGENT_CONFIG))
    agent_cfg = agent_yaml.get("agent", {})
    agent_cfg["step_limit"] = STEP_LIMIT
    log(f"agent config keys: {list(agent_cfg)}")
    model_cfg = dict(agent_yaml.get("model", {}))
    model_cfg["model_class"] = os.environ.get(
        "MSWEA_MODEL_CLASS", "minisweagent.models.litellm_textbased_model.LitellmTextbasedModel"
    )
    model_cfg["model_name"] = MODEL_NAME
    # api_key/temperature env-configurable: "local" works for the local vLLM OpenAI shim;
    # an external API teacher (e.g. GLM) needs a real key + often temperature>0 for diverse
    # rejection-sampling rollouts.
    model_cfg["model_kwargs"] = {
        **model_cfg.get("model_kwargs", {}),
        "api_base": API_BASE,
        "api_key": os.environ.get("MSWEA_API_KEY", "local"),
        "temperature": float(os.environ.get("MSWEA_TEMPERATURE", "0.0")),
        # top_p: GLM-5.2 agentic-coding rec is temp=1.0/top_p=0.95; only sent if set.
        **({"top_p": float(os.environ["MSWEA_TOP_P"])} if os.environ.get("MSWEA_TOP_P") else {}),
        # Reasoning teachers (e.g. glm-5.2) burn tokens on hidden reasoning before the action;
        # give generous headroom (endpoint supports up to 128k). Per-step budget.
        "max_tokens": int(os.environ.get("MSWEA_MAX_TOKENS", "32768")),
        "drop_params": True,
        # The GLM-5.2 endpoint is a SHARED internal gateway with a rate limit; a 40-step
        # rollout fires many requests, so even modest concurrency can trip 429s. litellm does
        # exponential backoff across num_retries -> a throttled step waits & retries instead of
        # failing the step (which would yield an empty patch). Keep NUM_WORKERS low too.
        "num_retries": int(os.environ.get("MSWEA_NUM_RETRIES", "6")),
    }
    log(f"model_class={model_cfg['model_class']} model_name={MODEL_NAME} api_base={API_BASE} temp={model_cfg['model_kwargs']['temperature']}")
    model = get_model(config=model_cfg)

    import threading
    # call-level GLM stats come from the instrumented model (set via MSWEA_MODEL_CLASS=
    # swesandbox.glm_instrumented_model.GLMInstrumentedModel). litellm's own callbacks did NOT
    # fire for sync completion, so we wrap the call site in the model subclass instead.
    try:
        from swesandbox.glm_instrumented_model import glm_summary as _glm_summary
        log("[glm-instr] using GLMInstrumentedModel call-site stats")
    except Exception as _e:
        _glm_summary = None
        log(f"[glm-instr] glm_summary unavailable: {_e}")

    from concurrent.futures import ThreadPoolExecutor, as_completed

    preds_path = OUT_DIR / "preds.json"
    preds = {}
    if preds_path.exists():
        preds = json.loads(preds_path.read_text())
    lock = threading.Lock()

    def process(inst):
        """Build a per-instance sandbox, run the agent, capture the real git-diff patch.
        Each instance gets its own SWESBEnv (unique root_dir); the vLLM server handles
        concurrent requests, so instances run in parallel across worker threads."""
        iid = inst.problem_statement.id
        env = None
        submission, exit_status = "", None
        reward = None
        try:
            env = SWEsbEnv.from_config(ds=inst.ds, bundles=bundles, config=inst.env)
            log(f"[{iid}] start (deploy repo+venv+chroot)")
            env.start()
            msb = MiniSandboxEnvironment(env)
            # LOCKDOWN experiment (NO_NET=1): close BOTH leak vectors so we measure the pure
            # recall+reasoning floor. (1) git-history leak is LOCAL (Initial 'clean' commit sits in
            # the cached clone — `git show Initial:src` works offline), so we `rm -rf .git && git init`
            # to a single commit (current buggy HEAD state) — drops Initial/BugPatch + the remote.
            # (2) pip/PyPI leak needs net, so we unset the proxy in the sandbox session. GLM teacher
            # uses an internal IP (NO_PROXY) so its calls are unaffected. Scoring re-deploys a fresh
            # FULL-history env, so test-restoration (HEAD^) is unaffected. `git diff HEAD` patch
            # capture still works (HEAD = the new single base commit).
            if os.environ.get("NO_NET") == "1":
                try:
                    msb.execute({"command":
                        "cd /testbed && rm -rf .git && git init -q && "
                        "git -c user.email=a@b.c -c user.name=x add -A && "
                        "git -c user.email=a@b.c -c user.name=x commit -qm base && "
                        "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY 2>/dev/null; true"})
                    log(f"[{iid}] LOCKDOWN: git history wiped (no Initial/remote) + proxy unset")
                except Exception as e:
                    log(f"[{iid}] LOCKDOWN hook failed: {e}")
            agent = DefaultAgent(model, msb, **agent_cfg)
            info = agent.run(inst.problem_statement.get_problem_statement())
            exit_status = info.get("exit_status")
            agent_submission = info.get("submission", "") or ""
            # Patch capture has TWO valid sources; prefer the working-tree diff, fall back to the
            # agent's submitted patch.txt:
            #  (1) `git diff HEAD` of /testbed -- clean by construction (tracked source edits only,
            #      NOT `git add -A` which folds scratch files like patch.txt/reproduce.py into the
            #      diff -> apply failures on the scorer's fresh checkout). Best when the agent left
            #      its fix in the working tree.
            #  (2) the agent's `submission` -- the swebench_backticks config's submit protocol is
            #      `git diff -- <files> > patch.txt` then `cat patch.txt`, so `submission` IS a clean
            #      source-only diff. CRITICAL: ~48% of rollouts revert/re-apply the working tree
            #      before submitting (the agent thinks like SWE-agent: package a patch, clean the
            #      tree) -> `git diff HEAD` comes back EMPTY even though the fix is correct and lives
            #      in `submission`. Falling back to it recovers those (validated: empty-Submitted
            #      trajs whose patch.txt held the right diff). For scoring it's equivalent -- the
            #      scorer applies model_patch to a fresh checkout regardless of final tree state.
            # Both are git diffs, so guard with a "diff --git" sanity check and slice from there.
            try:
                diff = msb.execute({"command": "cd /testbed && git diff HEAD"})["output"]
            except Exception as e:
                diff = ""
                log(f"[{iid}] git-diff capture failed: {e}")
            # PREFER agent_submission: the env now captures `git diff HEAD` AT SUBMIT TIME via the
            # marker (canonical, edits live in tree) — this is the upstream-aligned source. The
            # post-hoc `git diff HEAD` (run after agent.run returned) is only a fallback because it
            # intermittently came back empty; the message scan is a last resort.
            submission = _extract_diff(agent_submission) or _extract_diff(diff) or _last_diff_in_messages(agent.messages)
            src = ("submission" if _extract_diff(agent_submission)
                   else "worktree" if _extract_diff(diff)
                   else "messages" if submission else "none")
            log(f"[{iid}] exit={exit_status} git_diff_len={len(diff)} agent_submit_len={len(agent_submission)} -> patch_len={len(submission)} src={src}")
            # INLINE SCORING (one-pass): score the extracted submission against the faithfulness
            # gate IN THE SAME WARM ENV — no second deploy. Reuses env.pre_check() so the verdict is
            # identical-by-construction to swesmith_score.sh (apply model patch fwd -> _calculate_reward
            # -> reset). This reward IS the RL reward hook. Requires process env:
            # SWE_SANDBOX_SCORE_MODEL_PATCH=1 (forward) + SWE_RELINK_EDITABLE=1. Gate off with SCORE_INLINE=0.
            if os.environ.get("SCORE_INLINE", "0") == "1" and submission.strip():
                try:
                    env._reset_repository()                  # reset --hard + checkout base + clean -fdq -> clean tree
                    env.deployment.ds["patch"] = submission   # inject model patch (scored FORWARD via the env flag)
                    reward, _f2p, _p2p, _ = env.pre_check()
                    log(f"[{iid}] inline reward={reward}")
                except Exception as e:
                    log(f"[{iid}] inline scoring failed: {e}")
            try:
                (OUT_DIR / f"{iid}.traj.json").write_text(json.dumps({
                    "messages": agent.messages,
                    "info": {"exit_status": exit_status, "submission": submission,
                             "agent_submission": agent_submission, "reward": reward},
                    "instance_id": iid,
                }, indent=2))
            except Exception as e:
                log(f"[{iid}] traj save failed: {e}")
        except Exception as e:
            exit_status = type(e).__name__
            log(f"[{iid}] ERROR: {e}\n{traceback.format_exc()}")
        finally:
            if env is not None:
                try:
                    env.close()
                except Exception:
                    pass
        rec = {"model_name_or_path": MODEL_NAME, "instance_id": iid, "model_patch": submission, "reward": reward}
        with lock:
            preds[iid] = rec
            preds_path.write_text(json.dumps(preds, indent=2))
            log(f"[{iid}] written ({len(preds)}/{len(instances)} done)")
        return iid

    import time as _time
    t0 = _time.time()
    log(f"[throughput] START {len(instances)} instances | concurrency(workers)={NUM_WORKERS} @ {_time.strftime('%H:%M:%S')}")
    done_n = 0
    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as ex:
        futs = [ex.submit(process, inst) for inst in instances]
        for f in as_completed(futs):
            try:
                f.result()
            except Exception as e:
                log(f"worker error: {e}")
            done_n += 1
            if done_n % 10 == 0 or done_n == len(instances):
                el = _time.time() - t0
                rate = done_n / el * 3600 if el > 0 else 0
                rsv = sum(1 for v in preds.values() if v.get("reward") == 1)
                gs = (" || " + _glm_summary()) if _glm_summary else ""
                log(f"[throughput] {done_n}/{len(instances)} | {el/60:.1f}min | {rate:.0f} inst/hr | resolved={rsv} | workers={NUM_WORKERS}{gs}")

    el = _time.time() - t0
    nonempty = sum(1 for v in preds.values() if (v.get("model_patch") or "").strip())
    resolved = sum(1 for v in preds.values() if v.get("reward") == 1)
    rate = len(preds) / el * 3600 if el > 0 else 0
    log(f"[throughput] DONE instances={len(preds)} nonempty={nonempty} resolved={resolved} "
        f"wall={el/60:.1f}min concurrency={NUM_WORKERS} throughput={rate:.0f} inst/hr "
        f"resolve_rate={100*resolved/max(len(preds),1):.0f}% -> {preds_path}")
    if _glm_summary:
        log(f"[glm-instr] FINAL {_glm_summary()}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)

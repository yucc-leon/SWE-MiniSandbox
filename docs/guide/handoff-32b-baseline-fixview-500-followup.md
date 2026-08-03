# Follow-up: 32B Baseline Fixview 500 Evaluation

This note records the final state of the resumed 500-instance run from
`docs/guide/handoff-32b-baseline-fixview-500.md`, plus the next workstreams.

## Final outcome

Runtime roots:

```text
eval:  .runtime/ascend-eval-sweagent-32b-baseline-fixview-500-20260422-045851
score: .runtime/ascend-score-sweagent-32b-baseline-fixview-500-20260422-045851
```

Generation ended at:

```text
traj files:            500
pred files:            500
preds.json entries:    500
non-empty patches:     467
empty patches:          33
```

Scoring ended at:

```text
submitted:             500
completed/scored:      484
errors:                 16
resolved:              142
unresolved:            342
resolved_rate:       28.4%
```

Pipeline final state:

```text
next_shard_index:       69
running_ids:             0
scored_ids:            500
failed_ids:              0
```

## What had to be fixed to reach closure

This run did not drain cleanly without intervention.

1. `sh/pipeline_scoring_ascend.py` had a stale-state bug in `load_state()`.
   Persisted `running_ids/scored_ids/failed_ids` were trusted across restarts,
   so interrupted shards could leave instances permanently hidden from
   `_select_pending_batch()`.
2. `run_scoring_shard()` had a logging bug referencing an undefined
   `instance_ids` variable.
3. Stale shards `000043`, `000044`, and later `000067` were left marked
   `running` after their worker processes were gone. They had to be normalized
   to `aborted` and resubmitted.
4. Recovery shards `000065`, `000066`, and `000068` were then used to drain the
   skipped instances.

The patched file is:

- [sh/pipeline_scoring_ascend.py](/path/to/SWE-MiniSandbox/sh/pipeline_scoring_ascend.py)

## Error breakdown

The 16 scoring errors are not one class of failure.

### 11 `shell not initialized`

These are runtime/session failures inside SWE-ReX during scoring precheck or
command execution:

- `django__django-10097`
- `django__django-11749`
- `django__django-13023`
- `django__django-13809`
- `django__django-13837`
- `django__django-14311`
- `django__django-14434`
- `django__django-14771`
- `django__django-15741`
- `django__django-7530`
- `psf__requests-2317`

Representative traceback points at
`SWE-ReX/src/swerex/runtime/sandbox.py` raising `RuntimeError: shell not initialized`.

### 4 patch-apply failures

These are scoring-time replay failures where the predicted patch could not be
applied back onto the evaluation repo:

- `astropy__astropy-7166`
- `astropy__astropy-7336`
- `django__django-16145`
- `sphinx-doc__sphinx-7440`

Representative traceback ends in
`swesandbox.utils.EvaluationError: Failed to apply patch ... with all methods`.

The final single-instance rerun for `django__django-16145` is especially useful:
it showed `git apply`, `git apply --reject`, and `patch --fuzz=5` all failing.
That instance was not a scorer hang. The patch itself was not replayable.

### 1 environment install timeout

- `matplotlib__matplotlib-23476`

This failed in `install_env.sh` after a `700.0s` timeout during scoring.

## Eval-side caveat

One generation instance, `django__django-16145`, originally ended with:

- `.traj` present
- `.patch` present
- no `.pred`
- `submission=""`
- `exit_status=None`

The root cause was an eval-side exception during autosubmission patch capture,
not the absence of a patch artifact. Its `.pred` had to be backfilled manually
from the saved `.patch` so the run could be closed and scored.

This means the final `500 pred` count is a repaired end state, not a clean
first-pass generation result.

## Precision gap versus upstream

This run is now complete enough to compare behaviorally against upstream. It is
not on the same evaluation stack as the upstream `40%+` 32B result.

### 1. Agent policy is materially different

Current baseline is single-pass `sandbox`:

- [config/sweagent_infer_ascend_minisandbox_baseline.yaml](/path/to/SWE-MiniSandbox/config/sweagent_infer_ascend_minisandbox_baseline.yaml:18)
- [config/sweagent_infer_ascend_minisandbox_baseline.yaml](/path/to/SWE-MiniSandbox/config/sweagent_infer_ascend_minisandbox_baseline.yaml:21)

Upstream benchmark is `retry + chooser + max_attempts=10`:

- [SWE-agent/config/benchmarks/250212_sweagent_heavy_sbl.yaml](/path/to/SWE-MiniSandbox/SWE-agent/config/benchmarks/250212_sweagent_heavy_sbl.yaml:7)
- [SWE-agent/config/benchmarks/250212_sweagent_heavy_sbl.yaml](/path/to/SWE-MiniSandbox/SWE-agent/config/benchmarks/250212_sweagent_heavy_sbl.yaml:135)

This is the largest precision-gap suspect.

### 2. Tool stack is not aligned

Current baseline uses:

- `tools/registry`
- `tools/edit_anthropic`
- `tools/submit`
- `xml_function_calling`

See:

- [config/sweagent_infer_ascend_minisandbox_baseline.yaml](/path/to/SWE-MiniSandbox/config/sweagent_infer_ascend_minisandbox_baseline.yaml:115)
- [config/sweagent_infer_ascend_minisandbox_baseline.yaml](/path/to/SWE-MiniSandbox/config/sweagent_infer_ascend_minisandbox_baseline.yaml:123)

Upstream default / benchmark uses:

- `review_on_submit_m`
- `diff_state`
- `function_calling`

See:

- [SWE-agent/config/default.yaml](/path/to/SWE-MiniSandbox/SWE-agent/config/default.yaml:41)
- [SWE-agent/config/default.yaml](/path/to/SWE-MiniSandbox/SWE-agent/config/default.yaml:65)
- [SWE-agent/config/benchmarks/250212_sweagent_heavy_sbl.yaml](/path/to/SWE-MiniSandbox/SWE-agent/config/benchmarks/250212_sweagent_heavy_sbl.yaml:49)
- [SWE-agent/config/benchmarks/250212_sweagent_heavy_sbl.yaml](/path/to/SWE-MiniSandbox/SWE-agent/config/benchmarks/250212_sweagent_heavy_sbl.yaml:55)

### 3. Runtime is MiniSandbox no-chroot, not upstream Docker/chroot

The repo explicitly documents that this baseline is still not the official
reproduction path:

- [docs/guide/eval-modes.md](/path/to/SWE-MiniSandbox/docs/guide/eval-modes.md:116)

Concrete no-chroot behavior differences include:

- plain bash startup under host `root_dir`
- instance-local `TMPDIR`
- hardcoded path rewriting for `/testbed`, `/tools`, `/root`
- install script rewriting and `--no-build-isolation` fallback

See:

- [sandboxdev/swesandbox/sandbox_deployment.py](/path/to/SWE-MiniSandbox/sandboxdev/swesandbox/sandbox_deployment.py:295)
- [sandboxdev/swesandbox/sandbox_deployment.py](/path/to/SWE-MiniSandbox/sandboxdev/swesandbox/sandbox_deployment.py:867)
- [sandboxdev/swesandbox/sandbox_deployment.py](/path/to/SWE-MiniSandbox/sandboxdev/swesandbox/sandbox_deployment.py:911)

### 4. Submit / patch semantics are different

MiniSandbox `submit` only emits the marker:

- [SWE-agent/tools/submit/bin/submit](/path/to/SWE-MiniSandbox/SWE-agent/tools/submit/bin/submit:1)

The original submit script emits the actual patch directly:

- [SWE-agent/tool/submit/bin/submit](/path/to/SWE-MiniSandbox/SWE-agent/tool/submit/bin/submit:1)

The docs explicitly state that true patch generation moved into the deployment:

- [docs/guide/api/sweagent/tool.md](/path/to/SWE-MiniSandbox/docs/guide/api/sweagent/tool.md:29)

Current capture path:

- [SWE-agent/sweagent/agent/agents.py](/path/to/SWE-MiniSandbox/SWE-agent/sweagent/agent/agents.py:777)
- [sandboxdev/swesandbox/sandbox_deployment.py](/path/to/SWE-MiniSandbox/sandboxdev/swesandbox/sandbox_deployment.py:1436)

### 5. Core execution chain has drifted heavily

Relative to `upstream/main`, this branch is:

```text
behind: 0
ahead:  23
```

Critical diffstat:

```text
SWE-agent/sweagent/agent/agents.py              +205/-?
SWE-agent/sweagent/agent/models.py              +424/-?
SWE-agent/sweagent/environment/repo.py          +240/-?
SWE-agent/sweagent/tools/parsing.py             +151/-?
sandboxdev/swesandbox/sandbox_deployment.py     +785/-?
sandboxdev/swesandbox/utils.py                  +107/-?
sh/pipeline_scoring_ascend.py                   +616 new-ish logic
```

This is enough drift that the current result should not be attributed to model
quality alone.

## Efficiency and stability takeaways

Generation timing summary from
[timing_summary.json](/path/to/SWE-MiniSandbox/.runtime/ascend-eval-sweagent-32b-baseline-fixview-500-20260422-045851/timing_summary.json):

```text
model_response avg:   537.7s
model_response p95:  1174.6s
tool_execution avg:    68.9s
tool_execution p95:   342.6s
step_count avg:        51.6
step_count p95:       176.2
```

Scoring-side observations from the final recovery work:

1. Environment setup cost is high even for single-instance reruns.
   The final shard for `django__django-16145` spent about:
   - `19.81s` creating a fresh shared venv
   - `129.09s` in the install script
   - `10.82s` repacking the shared venv cache
2. The dominant recurrent scoring failure is not test timeout; it is runtime
   shell/session instability.
3. Patch replay failures are a separate class and should be tracked apart from
   environment failures, otherwise scoring noise gets mixed with agent quality.

## Recommended next actions

### 2026-04-24 update

After the follow-up reruns, the conservative scoring-side recovery picture is:

```text
Original final 500-run:
  completed: 484
  error:      16
  resolved:  142

Verified recoveries:
  shell/session + gitcache/wheelhouse: +11 completed, +3 resolved, -11 error
  matplotlib install timeout:          +1 completed,  +1 resolved, -1 error
  binary patch diff sanitization:      +3 completed,  +0 resolved, -3 error

Conservative merged estimate:
  completed: 499 / 500
  error:       1 / 500
  resolved:  146 / 500
  resolved rate: 29.2%
```

Validated rerun outputs:

- `.runtime/ascend-score-rerun-shell11fix-20260423-1714/results.json`
- `.runtime/ascend-score-rerun-gitcache4fix-wheelhouse-20260423-1756/results.json`
- `.runtime/ascend-score-rerun-matplotlib23476-20260424-0001/results.json`
- `.runtime/ascend-score-rerun-patch4-binarysanitize-20260424-0001/results.json`

Additional timeout-reset recovery validated on 2026-04-24:

- A full 500 scoring rerun was intentionally stopped after the first sampled
  instances showed the dominant bottleneck was shared-venv extraction and
  timeout/reset cleanup, not model generation.
- `django__django-11211`, `django__django-11333`, and `django__django-13821`
  reproduced the failure mode where a timed-out eval command left the default
  shell busy, then the post-reward repository reset became an uncaught runtime
  error.
- The fix closes the default shell session when reward calculation catches an
  eval exception; the existing environment recovery path recreates the shell
  before reset. Recovery also re-applies the tool PATH setup after session
  recreation.
- Validated outputs:
  - `.runtime/ascend-score-rerun-django11211-timeoutfix-20260424-153000/results.json`
  - `.runtime/ascend-score-rerun-timeoutreset2fix-20260424-154500/results.json`
- Combined result for those three instances: `3/3` completed/scored,
  `0/3` error, `0/3` resolved.

This does not change prompts, tool definitions, or agent interaction policy. It
only prevents eval timeouts from poisoning the next reset command in no-chroot
scoring.

### Reproducibility notes for a cold environment

The recovery work above should not depend on the debug machine's warmed caches.
The expected cold-start contract is:

1. `GITCACHE_ROOT` may be empty. The deployment should build `testbed.tar` on
   first use, and later runs may reuse it.
2. `SHARED_VENV_ROOT` may be empty. The first run for an image may create and
   pack `venv.tar`; later runs may extract it to `venv.cache` and symlink both
   the shared path and sandbox path to that cache.
3. `WHEELHOUSE_ROOT` may be empty, but no-chroot scoring is more reproducible
   after running `sh/bootstrap_minisandbox_wheelhouse.sh` for the Python
   versions present under `CONDA_BACKEND_ROOT`. The default bootstrap package
   set now includes Django runtime deps and `chardet`, matching the local-only
   prepare step used before `run_tests.sh`.
4. A warmed `.runtime/.../shared_venv/**/venv.cache` is an optimization only.
   It must not be treated as a required artifact when moving to a new machine.

Reproducible scoring invocation shape:

```bash
MINIFORGE_ROOT=/path/to/miniforge3 \
CONDA_BACKEND_ROOT=/path/to/minisandbox-conda \
WHEELHOUSE_ROOT=/path/to/minisandbox-wheelhouse \
bash sh/bootstrap_minisandbox_wheelhouse.sh

RUNTIME_ROOT=/path/to/new-runtime \
GITCACHE_ROOT=/path/to/new-runtime/gitcache \
SHARED_VENV_ROOT=/path/to/new-runtime/shared_venv \
WHEELHOUSE_ROOT=/path/to/minisandbox-wheelhouse \
PREDICTIONS_PATH=/path/to/preds.json \
CONFIG_PATH=config/sweagent_score_ascend_minisandbox_baseline.yaml \
INSTANCE_FILTER='^some__instance$' \
INSTANCE_SLICE=':500' \
NUM_WORKERS=1 \
POSTPROCESS_SCORING=1 \
bash sh/run_swebench_scoring_ascend.sh
```

If this fails on a clean environment but passes after manually copying `.runtime`
caches, treat it as a reproducibility bug rather than an acceptable setup
requirement.

Remaining confirmed error:

- `django__django-16145`: model patch source hunks do not match the checked-out
  baseline around `django/forms/models.py`; this is not a binary diff or
  runtime/session failure.

Given the current goal of staying close to official evaluation behavior, do not
change prompts, tool interfaces, function-calling settings, or agent interaction
policy as part of the next fix pass. Keep that as a separate controlled
experiment.

### Precision alignment

1. Keep prompt/tool/agent interaction settings frozen for the mainline until
   scoring reliability is no longer the dominant source of noise.
2. Compare remaining precision gaps against upstream with read-only diffs first:
   submission patch capture, patch post-processing, dataset slicing, scoring
   harness behavior, and runtime environment setup.
3. Only run an `upstream-like` prompt/tool experiment on a fixed small slice
   after it is separated from the mainline scoring recovery work.

### Efficiency / stability

1. Treat `shell not initialized` as fixed enough for the next rerun, but keep
   the recovery path covered by tests.
2. Keep binary/generated artifact diff sanitization in scoring-side patch
   application; it converted `3/4` patch-apply errors into scored unresolved
   instances.
3. Continue profiling environment installation and shared-venv reuse before
   increasing scoring concurrency further. `django__django-16145` still spent
   about two minutes in install for a fresh shared venv.
4. Consider expanding the local wheelhouse before enabling stricter offline pip
   behavior. The current wheelhouse is sparse for Python 3.9 binary packages.

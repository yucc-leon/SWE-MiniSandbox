#!/usr/bin/env python
"""
R2E-Gym faithful-reconstruction SCREENER (container-free, no-docker).

Goal: measure the faithful-reconstruction YIELD of R2E-Gym instances in our
container-free stack, to compare against swesmith's ~50%.

For each of N instances it:
  1. load   : R2E-Gym-Lite HF row -> our instance dict  (reuses r2e_poc.make_instance)
  2. build  : clone repo @ buggy base, fetch fix obj, checkout base, rm -rf .git,
              copy install_utils helpers, write graded tests, run install.sh (uv venv).
  3. gate   : score BUGGY state (expect reward 0.0) and GOLD state (expect reward 1.0).
              An instance is FAITHFUL iff  gold==1.0 AND buggy==0.0  (the f2p gate).

Efficiency: we clone+install ONCE at the buggy base, score buggy, then overlay the
gold source files in place (editable install picks up .py changes) and score gold.
This halves clone/install cost. CAVEAT: if a gold file is a compiled extension
(.pyx/.c/.pyi-built), the in-place overlay may not be recompiled; such instances are
flagged (gold_has_native) so the gate result can be discounted. Use --rebuild-gold to
do a second full clone+install for the gold variant instead (slow, fully faithful).

This is a STANDALONE screener; it does NOT import into the main package and does NOT
edit sandboxdev/swesandbox/*. It reuses sh/rl/r2e_poc.py for the load/score logic.

NOTE on chroot: the production faithfulness gate in sandbox_deployment.py runs inside a
chroot (needs a privileged tp pod). This screener runs gold/buggy tests in the plain uv
venv WITHOUT chroot -- adequate to measure the f2p gate (gold passes / buggy fails) since
the gate is about test outcomes, not isolation. See bottom of file for the tp-pod recipe.

Usage (conda env swe-sandbox):
  PY=/path/to/workspace/miniforge3/envs/swe-sandbox/bin/python
  $PY sh/rl/r2e_screen.py --n 20 --workdir /tmp/r2e_screen --out /tmp/r2e_screen/results.jsonl
  $PY sh/rl/r2e_screen.py --n 20 --repos aiohttp,tornado,pyramid,coveragepy --workers 4
"""
import argparse
import collections
import json
import os
import shutil
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "sh" / "rl"))
sys.path.insert(0, str(ROOT / "R2E-Gym" / "src"))

import r2e_poc  # reuse make_instance / run / score / parsing  # noqa: E402
from r2egym.repo_analysis.execution_log_parser import (  # noqa: E402
    parse_log_fn, decolor_dict_keys,
)

PASS = "PASSED"
# Repos whose gold/buggy source includes compiled C/Cython extensions: on aarch64
# these may fail to *build* for reasons unrelated to the scoring policy. Reported
# separately as "screen-out candidates" rather than counted against the policy.
NATIVE_BUILD_REPOS = {"numpy", "pillow", "orange3"}

# Canonical upstream URLs for the 13 R2E-Gym repos (repo_name -> git url).
REPO_GIT_URL = {
    "aiohttp": "https://github.com/aio-libs/aiohttp.git",
    "bokeh": "https://github.com/bokeh/bokeh.git",
    "coveragepy": "https://github.com/nedbat/coveragepy.git",
    "datalad": "https://github.com/datalad/datalad.git",
    "numpy": "https://github.com/numpy/numpy.git",
    "orange3": "https://github.com/biolab/orange3.git",
    "pandas": "https://github.com/pandas-dev/pandas.git",
    "pillow": "https://github.com/python-pillow/Pillow.git",
    "pyramid": "https://github.com/Pylons/pyramid.git",
    "scrapy": "https://github.com/scrapy/scrapy.git",
    "sympy": "https://github.com/sympy/sympy.git",
    "tornado": "https://github.com/tornadoweb/tornado.git",
}
# inject our full URL map into the reused poc module so make_instance/build fill git_url
r2e_poc.REPO_GIT_URL.update(REPO_GIT_URL)

INSTALL_UTILS = ROOT / "R2E-Gym" / "src" / "r2egym" / "install_utils"
NATIVE_EXT = (".pyx", ".pxd", ".c", ".cc", ".cpp", ".pyf")


def proxy_env():
    proxy = (ROOT / "vendor/.proxy_url").read_text().strip()
    return {
        "http_proxy": proxy, "https_proxy": proxy,
        "HTTP_PROXY": proxy, "HTTPS_PROXY": proxy,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "",  # never prompt
        "NO_PROXY": "localhost,127.0.0.1,192.168.0.0/16,10.0.0.0/8",
        "UV_PROJECT_ENVIRONMENT": ".venv",
    }


def setup_git_proxy():
    """git config global http.proxyAuthMethod basic (pattern used elsewhere in repo)."""
    r2e_poc.run("git config --global http.proxyAuthMethod basic")


def clone_and_install(inst, dst: Path, penv: dict, log: list, install_timeout: int = 3600) -> dict:
    """Clone @ buggy base, strip .git, write tests, run install.sh. Returns status dict."""
    repo = inst["repo"]
    url = inst["git_url"] or REPO_GIT_URL[repo]
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    r = r2e_poc.run(f"git clone {url} {dst}", timeout=1200, env=penv)
    if r.returncode != 0:
        return {"ok": False, "stage": "clone", "err": r.stderr[-1500:]}
    fix = inst["fix_commit"]
    r2e_poc.run(f"git fetch origin {fix}", cwd=str(dst), timeout=900, env=penv)
    r = r2e_poc.run(f"git checkout -f {inst['base_commit']}", cwd=str(dst), env=penv)
    if r.returncode != 0:
        return {"ok": False, "stage": "checkout", "err": r.stderr[-1500:]}
    resolved = r2e_poc.run("git rev-parse HEAD", cwd=str(dst)).stdout.strip()

    # LEAK DEFENSE: strip git history (the fix is reachable in forward history)
    shutil.rmtree(dst / ".git", ignore_errors=True)

    # write graded tests
    tdir = dst / "r2e_tests"
    tdir.mkdir(exist_ok=True)
    (tdir / "__init__.py").write_text("")
    for name, code in inst["test_files"].items():
        (tdir / name).write_text(code)

    # copy ALL install_utils helper .py files (install scripts reference some of them)
    for helper in INSTALL_UTILS.glob("*.py"):
        shutil.copy(helper, dst / helper.name)

    # install.sh: strip CRLF (shipped scripts have CRLF -> `source .../activate\r` breaks)
    install_sh = INSTALL_UTILS / f"{repo}_install.sh"
    if not install_sh.exists():
        return {"ok": False, "stage": "install", "err": f"no install.sh for {repo}"}
    raw = install_sh.read_text().replace("\r\n", "\n").replace("\r", "\n")
    (dst / "install.sh").write_text(raw)

    t0 = time.time()
    r = r2e_poc.run("bash install.sh", cwd=str(dst), timeout=install_timeout, env=penv)
    log.append(f"    install.sh rc={r.returncode} {time.time()-t0:.0f}s")
    log.append("    install stderr tail: " + r.stderr[-600:].replace("\n", " | "))
    venv_py = dst / ".venv" / "bin" / "python"
    if not venv_py.exists():
        return {"ok": False, "stage": "install", "rc": r.returncode,
                "err": "no .venv/bin/python after install\n" + r.stderr[-1200:]}
    (dst / "run_tests.sh").write_text(inst["test_cmd"] + "\n")
    return {"ok": True, "resolved_base": resolved, "install_rc": r.returncode}


def apply_gold(inst, dst: Path):
    for path, content in inst["gold_files"].items():
        fp = dst / path
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)


# ============================================================================
# SCORING POLICY
# ----------------------------------------------------------------------------
# Two policies are supported (selected by --scoring):
#
#   "full"  (legacy): exact-match the ENTIRE recorded pytest suite against the
#           gold oracle (expected_output_json). count must match AND every test
#           status must match. This is what the original screener used; it tanks
#           instances where ONE unrelated/optional dep drops dozens of tests
#           (pyramid: missing optional dep collapses an 800-test suite) or where
#           a single test's status differs only by ERROR-vs-FAILED (tornado).
#
#   "f2p"   (new, default, the SANE policy): score only the DISCRIMINATING tests
#           -- the fail-to-pass subset, i.e. tests whose oracle status DIFFERS
#           between buggy and gold (PASS on gold, NOT-PASS on buggy). The gate is:
#             * GOLD reconstruction: every discriminating test must be PASSED.
#             * BUGGY reconstruction: every discriminating test must NOT pass
#               (FAILED / ERROR / not-collected all count -- ERROR is normalized
#               to FAILED on the buggy side, since a test that errors on buggy
#               still demonstrates the bug).
#           Tests outside the discriminating subset (the rest of the suite) are
#           IGNORED -- their flakiness/dep-sensitivity no longer fails the gate.
#
# The oracle buggy statuses come from execution_result_content.old_commit_res_stdout
# in the raw HF row; the oracle gold statuses are expected_output_json.
# ============================================================================

def _normmap(d: dict) -> dict:
    """Decolor + normalize test keys (strip ' - <err>' suffix), matching r2e_poc.score."""
    d = decolor_dict_keys(d)
    return {k.split(" - ")[0]: d[k] for k in sorted(d) if k.split(" - ")[0]}


def run_and_parse(inst: dict, repo_dir: str, timeout: int = 900):
    """Run the graded test_cmd once and return (normalized_status_map, returncode)."""
    r = r2e_poc.run(inst["test_cmd"], cwd=repo_dir, timeout=timeout)
    parsed = parse_log_fn(inst["repo"])(r.stdout + "\n" + r.stderr)
    return _normmap(parsed), r.returncode


def full_verdict(parsed: dict, oracle_gold: dict):
    """Legacy full-suite exact-match (mirrors r2e_poc.score). Returns (reward, reason)."""
    if len(parsed) != len(oracle_gold):
        return 0.0, f"count mismatch parsed={len(parsed)} expected={len(oracle_gold)}"
    mism = [(k, oracle_gold.get(k), parsed[k]) for k in parsed if parsed[k] != oracle_gold.get(k)]
    return (1.0 if not mism else 0.0), ("all match" if not mism else f"{len(mism)} mismatch(es): {mism[:5]}")


def f2p_verdict(buggy_parsed: dict, gold_parsed: dict, oracle_buggy: dict, oracle_gold: dict) -> dict:
    """Saner gate: discriminating (fail-to-pass) tests only.

    discriminating = {t : oracle says PASS on gold AND NOT-PASS on buggy}.
    Gate: gold run PASSES all of them AND buggy run does NOT pass any of them
    (FAILED/ERROR/not-collected on buggy all count; ERROR is normalized to FAILED).
    """
    disc = sorted(t for t in oracle_gold
                  if oracle_gold[t] == PASS and oracle_buggy.get(t) != PASS)
    if not disc:
        return {"faithful": False, "n_disc": 0,
                "reason": "no discriminating f2p tests in oracle (cannot gate)"}
    gold_bad = [t for t in disc if gold_parsed.get(t) != PASS]                 # gold MUST pass
    buggy_bad = [t for t in disc if buggy_parsed.get(t, "MISSING") == PASS]    # buggy must NOT pass
    faithful = (not gold_bad) and (not buggy_bad)
    reason = "ok" if faithful else (
        (f"gold-not-pass={gold_bad[:5]} " if gold_bad else "")
        + (f"buggy-still-pass={buggy_bad[:5]}" if buggy_bad else "")).strip()
    return {"faithful": faithful, "n_disc": len(disc),
            "gold_fail_disc": len(gold_bad), "buggy_pass_disc": len(buggy_bad),
            "reason": reason}


def oracle_buggy_from_row(row: dict, repo: str) -> dict:
    """Parse the recorded BUGGY-side log (old_commit_res_stdout) into a status map."""
    er = row.get("execution_result_content")
    if not er:
        return {}
    er = json.loads(er) if isinstance(er, str) else er
    raw = er.get("old_commit_res_stdout")
    return _normmap(parse_log_fn(repo)(raw)) if raw else {}


def screen_one(row: dict, workdir: str, rebuild_gold: bool, install_timeout: int = 3600,
               scoring: str = "f2p") -> dict:
    log = []
    try:
        inst = r2e_poc.make_instance(row)
    except Exception as e:
        return {"instance_id": "?", "repo": row.get("repo_name"),
                "faithful": False, "stage": "load", "err": repr(e)}
    rid = inst["instance_id"]
    res = {"instance_id": rid, "repo": inst["repo"],
           "n_tests": len(inst["test_files"]), "n_gold_files": len(inst["gold_files"]),
           "n_expected": len(inst["expected_output_json"]),
           "gold_has_native": any(p.endswith(NATIVE_EXT) for p in inst["gold_files"]),
           "native_repo": inst["repo"] in NATIVE_BUILD_REPOS,
           "scoring": scoring, "faithful": False, "log": log}
    if not inst["test_files"] or not inst["expected_output_json"]:
        res["stage"] = "load"; res["err"] = "no tests / no oracle"
        return res
    penv = proxy_env()
    base = Path(workdir) / rid

    try:
        # ---- BUGGY build (clone+install once) ----
        bdst = base.with_name(base.name + "__buggy")
        st = clone_and_install(inst, bdst, penv, log, install_timeout)
        res["build_buggy"] = {k: v for k, v in st.items() if k != "err"}
        if not st["ok"]:
            res["stage"] = "build_buggy"; res["err"] = st.get("err", "")[:800]
            return res
        buggy_parsed, buggy_rc = run_and_parse(inst, str(bdst), timeout=900)

        # ---- GOLD: overlay gold files in place (or full rebuild) ----
        if rebuild_gold:
            gdst = base.with_name(base.name + "__gold")
            st2 = clone_and_install(inst, gdst, penv, log, install_timeout)
            res["build_gold"] = {k: v for k, v in st2.items() if k != "err"}
            if not st2["ok"]:
                res["stage"] = "build_gold"; res["err"] = st2.get("err", "")[:800]
                return res
            apply_gold(inst, gdst)
            score_dir = str(gdst)
        else:
            apply_gold(inst, bdst)
            score_dir = str(bdst)
        gold_parsed, gold_rc = run_and_parse(inst, score_dir, timeout=900)

        # ---- compute BOTH verdicts (transparency); gate on the active --scoring ----
        oracle_gold = _normmap(inst["expected_output_json"])
        oracle_buggy = oracle_buggy_from_row(row, inst["repo"])
        fb_r, fb_reason = full_verdict(buggy_parsed, oracle_gold)
        fg_r, fg_reason = full_verdict(gold_parsed, oracle_gold)
        res["buggy"] = {"reward": fb_r, "reason": fb_reason, "n_parsed": len(buggy_parsed), "rc": buggy_rc}
        res["gold"] = {"reward": fg_r, "reason": fg_reason, "n_parsed": len(gold_parsed), "rc": gold_rc}
        res["full_gate"] = (fg_r == 1.0 and fb_r == 0.0)
        res["n_oracle_gold"] = len(oracle_gold)
        res["n_oracle_buggy"] = len(oracle_buggy)
        f2p = f2p_verdict(buggy_parsed, gold_parsed, oracle_buggy, oracle_gold)
        res["f2p"] = f2p

        res["faithful"] = f2p["faithful"] if scoring == "f2p" else res["full_gate"]
        res["stage"] = "scored"
        # free disk: keep only failed builds for inspection
        if res["faithful"]:
            shutil.rmtree(base.parent / (base.name + "__buggy"), ignore_errors=True)
            shutil.rmtree(base.parent / (base.name + "__gold"), ignore_errors=True)
    except Exception as e:
        res["stage"] = res.get("stage", "exception")
        res["err"] = f"{e!r}\n{traceback.format_exc()[-800:]}"
    return res


HF_SNAPSHOT_GLOB = (
    "/home/claude/.cache/huggingface/hub/datasets--R2E-Gym--R2E-Gym-Lite/"
    "snapshots/*/data/train-*.parquet"
)


def load_rows():
    """Load R2E-Gym-Lite rows. Prefer cached parquet shards (fast, avoids the
    HF builder-lock hang); fall back to datasets.load_dataset if no local cache."""
    import glob
    shards = sorted(glob.glob(HF_SNAPSHOT_GLOB))
    if shards:
        import pyarrow.parquet as pq
        rows = []
        for s in shards:
            rows += pq.read_table(s).to_pylist()
        print(f"loaded {len(rows)} rows from {len(shards)} cached parquet shards", flush=True)
        return rows
    # fallback: HF datasets (downloads via proxy if not cached)
    from datasets import load_dataset
    ds = load_dataset("R2E-Gym/R2E-Gym-Lite", split="train")
    return [dict(r) for r in ds]


def pick_rows(rows, n, repos):
    """Pick up to n rows; if repos given, filter; else spread across repos round-robin."""
    by_repo = collections.defaultdict(list)
    for i, r in enumerate(rows):
        by_repo[r["repo_name"]].append(i)
    if repos:
        want = [r.strip() for r in repos.split(",")]
        idxs = []
        for r in want:
            idxs += by_repo.get(r, [])
        idxs = idxs[: n] if n else idxs
        return idxs
    # round-robin across repos for diversity
    order = sorted(by_repo, key=lambda r: -len(by_repo[r]))
    picked, k = [], 0
    while len(picked) < n and any(by_repo[r][k:] for r in order):
        for r in order:
            if k < len(by_repo[r]):
                picked.append(by_repo[r][k])
                if len(picked) >= n:
                    break
        k += 1
    return picked[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--repos", default="", help="comma list to restrict repos")
    ap.add_argument("--workdir", default="/tmp/r2e_screen")
    ap.add_argument("--out", default="")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--install-timeout", type=int, default=1500)
    ap.add_argument("--rebuild-gold", action="store_true",
                    help="full re-clone+install for gold (slow, fully faithful for native)")
    ap.add_argument("--scoring", choices=["f2p", "full"], default="f2p",
                    help="f2p (default, sane): gate only the discriminating fail-to-pass "
                         "tests (gold-pass & buggy-not-pass, ERROR~FAILED on buggy). "
                         "full (legacy): exact-match the entire recorded suite.")
    ap.add_argument("--split", default="train")
    a = ap.parse_args()
    out = a.out or str(Path(a.workdir) / "results.jsonl")
    Path(a.workdir).mkdir(parents=True, exist_ok=True)

    setup_git_proxy()
    proxy = (ROOT / "vendor/.proxy_url").read_text().strip()
    os.environ.setdefault("HF_ENDPOINT", "https://huggingface.co")
    os.environ.setdefault("http_proxy", proxy)
    os.environ.setdefault("https_proxy", proxy)
    print("loading R2E-Gym-Lite ...", flush=True)
    rows = load_rows()
    idxs = pick_rows(rows, a.n, a.repos)
    print(f"selected {len(idxs)} rows: repos={collections.Counter(rows[i]['repo_name'] for i in idxs)}", flush=True)

    results = []
    fout = open(out, "w")
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(screen_one, rows[i], a.workdir, a.rebuild_gold, a.install_timeout, a.scoring): i for i in idxs}
        for fut in as_completed(futs):
            r = fut.result()
            results.append(r)
            fout.write(json.dumps(r) + "\n"); fout.flush()
            mark = "FAITHFUL" if r["faithful"] else f"FAIL@{r.get('stage')}"
            extra = ""
            if "buggy" in r and "gold" in r:
                extra = (f" full[buggy={r['buggy']['reward']} gold={r['gold']['reward']}]"
                         f" f2p[disc={r.get('f2p',{}).get('n_disc')} "
                         f"gold_fail={r.get('f2p',{}).get('gold_fail_disc')} "
                         f"buggy_pass={r.get('f2p',{}).get('buggy_pass_disc')}]")
            print(f"  [{len(results)}/{len(idxs)}] {r['instance_id']:42s} {mark}{extra}", flush=True)
            for ln in r.get("log", []):
                print(ln, flush=True)
    fout.close()

    faithful = sum(1 for r in results if r["faithful"])
    print("\n========== YIELD ==========")
    print(f"  scoring policy: {a.scoring}")
    print(f"  faithful-reconstruction: {faithful}/{len(results)} "
          f"= {100*faithful/max(1,len(results)):.0f}%")

    # ---- excluding aarch64-build-blocked NATIVE repos (numpy/pillow/orange3) ----
    def build_blocked(r):
        return r["repo"] in NATIVE_BUILD_REPOS and r.get("stage") in ("build_buggy", "build_gold")
    native_blocked = [r for r in results if build_blocked(r)]
    non_blocked = [r for r in results if not build_blocked(r)]
    nb_faithful = sum(1 for r in non_blocked if r["faithful"])
    print(f"  excluding aarch64-build-blocked native repos: {nb_faithful}/{len(non_blocked)} "
          f"= {100*nb_faithful/max(1,len(non_blocked)):.0f}%  "
          f"(excluded {len(native_blocked)} native build failures)")

    byrepo = collections.defaultdict(lambda: [0, 0])
    for r in results:
        byrepo[r["repo"]][0] += int(r["faithful"]); byrepo[r["repo"]][1] += 1
    print("  per-repo (faithful/total):")
    for repo, (f, t) in sorted(byrepo.items()):
        tag = " [NATIVE/C-ext]" if repo in NATIVE_BUILD_REPOS else ""
        print(f"    {repo:12s} {f}/{t}{tag}")
    stages = collections.Counter(r.get("stage") for r in results if not r["faithful"])
    print("  fail stages:", dict(stages))
    if native_blocked:
        print("  SCREEN-OUT CANDIDATES (native repo, aarch64 build failed -- "
              "not counted against scoring policy):")
        for r in native_blocked:
            print(f"    {r['instance_id']:42s} stage={r.get('stage')}")
    print(f"  results -> {out}")


# =============================================================================
# tp-pod recipe for the CHROOT version of the gate (documented, not executed here)
# -----------------------------------------------------------------------------
# The plain-venv gate above measures gold-pass/buggy-fail correctly. To run the
# PRODUCTION gate (tests inside a chroot, as sandbox_deployment.py does) you need a
# privileged pod. Minimal yaml + launch:
#
#   apiVersion: v1
#   kind: Pod
#   metadata: {name: r2e-screen-chroot}
#   spec:
#     containers:
#     - name: main
#       image: <aarch64 base with conda swe-sandbox + uv>
#       securityContext: {privileged: true}        # chroot needs CAP_SYS_CHROOT
#       command: ["sleep", "infinity"]
#       volumeMounts:
#       - {name: shared, mountPath: /sharedata}
#     volumes:
#     - {name: shared, hostPath: {path: /sharedata}}
#
#   # launch:
#   kubectl apply -f r2e_screen_chroot.yaml
#   kubectl exec -it r2e-screen-chroot -- bash -lc '
#     PY=/path/to/workspace/miniforge3/envs/swe-sandbox/bin/python
#     cd /path/to/SWE-MiniSandbox &&
#     $PY sh/rl/r2e_screen.py --n 20 --workers 4'
#
# For the real integration the chroot wrap lives in sandbox_deployment.py's
# scoring path (data_type="r2e" branch) -- see report for the required diff.
# =============================================================================

if __name__ == "__main__":
    main()

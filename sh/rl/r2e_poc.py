#!/usr/bin/env python
"""
R2E-Gym -> SWE-MiniSandbox container-free PoC (single instance: aiohttp).

Proves the integration path end-to-end:
  1. load   : R2E-Gym HF row (or saved JSON) -> our instance dict.
  2. build  : clone repo @ buggy base, rm -rf .git, write graded tests, install deps (uv venv).
  3. score  : run graded tests -> parse_log_fn -> exact-match vs expected_output_json.
              gold (apply fix) -> 1.0 ; buggy (no patch) -> 0.0 ; empty patch -> 0.0 (sanity gate).

This is a STANDALONE prototype. It mirrors what setup_env_swesmith / reset_swesmith_tests /
_calculate_reward_swesmith do in sandboxdev/swesandbox/sandbox_deployment.py, but for data_type="r2e".
Do NOT import this into the main package; it is the spec/diff source for the real integration.

Usage (conda env swe-sandbox):
  python sh/rl/r2e_poc.py load   --src vendor/r2e_sample_instance.json --out /tmp/r2e_inst.json
  python sh/rl/r2e_poc.py build  --inst /tmp/r2e_inst.json --workdir <dir> --variant gold|buggy
  python sh/rl/r2e_poc.py score  --inst /tmp/r2e_inst.json --repo <built_repo_dir>
  python sh/rl/r2e_poc.py all    --src vendor/r2e_sample_instance.json --workdir <dir>   # full e2e both variants
  python sh/rl/r2e_poc.py logcheck --src vendor/r2e_sample_instance.json   # validate scorer on recorded logs (no build)
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

R2E_SRC = Path(__file__).resolve().parents[2] / "R2E-Gym" / "src"
if str(R2E_SRC) not in sys.path:
    sys.path.insert(0, str(R2E_SRC))

from r2egym.repo_analysis.execution_log_parser import parse_log_fn, decolor_dict_keys  # noqa: E402

REPO_GIT_URL = {
    "aiohttp": "https://github.com/aio-libs/aiohttp.git",
    # other 12 repos: pandas, numpy, sympy, pillow, scrapy, pyramid, tornado,
    # datalad, coveragepy, orange3, bokeh, ... -> fill upstream URLs.
}
INSTALL_UTILS = R2E_SRC / "r2egym" / "install_utils"
# default test command (repo_analysis_args.tests_cmd); a handful of repos override it.
DEFAULT_TEST_CMD = (
    "PYTHONWARNINGS='ignore::UserWarning,ignore::SyntaxWarning' "
    ".venv/bin/python -W ignore -m pytest -rA r2e_tests"
)


# ----------------------------------------------------------------------------- load
def load_row(src: str) -> dict:
    """src = path to saved JSON row, OR 'hf:<index>' to pull from HF R2E-Gym-Lite."""
    if src.startswith("hf:"):
        idx = int(src.split(":", 1)[1])
        os.environ.setdefault("HF_ENDPOINT", "https://huggingface.co")
        proxy = (Path(__file__).resolve().parents[2] / "vendor/.proxy_url").read_text().strip()
        os.environ.setdefault("http_proxy", proxy)
        os.environ.setdefault("https_proxy", proxy)
        from datasets import load_dataset
        ds = load_dataset("R2E-Gym/R2E-Gym-Lite", split="train")
        return dict(ds[idx])
    return json.load(open(src))


def make_instance(row: dict) -> dict:
    """R2E-Gym row -> our instance dict (the loader, deliverable #1)."""
    pc = json.loads(row["parsed_commit_content"])
    repo = row["repo_name"]
    base_commit = pc["old_commit_hash"]  # e.g. '<fix>^' -> resolved later by git

    # --- graded test files (authoritative: execution_result_content.test_file_*) ---
    test_files = {}
    er = row.get("execution_result_content")
    if er:
        er = json.loads(er)
        for name, code in zip(er["test_file_names"], er["test_file_codes"]):
            test_files[name] = code
    else:
        # fallback: derive from the TEST file diffs' new_file_content
        for i, fd in enumerate(pc["file_diffs"], 1):
            p = fd["header"]["file"]["path"]
            if "test" in p.lower():
                test_files[f"test_{i}.py"] = fd["new_file_content"]

    # --- gold patch: non-test source files, buggy(old)->fixed(new) ---
    gold_files = {}  # path -> new_file_content
    for fd in pc["file_diffs"]:
        p = fd["header"]["file"]["path"]
        if fd.get("is_binary_file"):
            continue
        if "test" in p.lower() or p.endswith((".rst", ".txt", ".md")):
            continue  # skip test files and changelog/doc noise
        gold_files[p] = fd["new_file_content"]

    return {
        "instance_id": f"r2e__{repo}__{row['commit_hash'][:12]}",
        "repo": repo,
        "git_url": REPO_GIT_URL.get(repo),
        "base_commit": base_commit,
        "fix_commit": row["commit_hash"],
        "problem_statement": row["problem_statement"],
        "test_files": test_files,            # name -> code (written into r2e_tests/)
        "gold_files": gold_files,            # path -> fixed content (the gold patch)
        "expected_output_json": json.loads(row["expected_output_json"]),  # oracle
        "test_cmd": DEFAULT_TEST_CMD,
    }


# ----------------------------------------------------------------------------- build
def run(cmd, cwd=None, timeout=1800, env=None):
    print(f"  $ {cmd}  (cwd={cwd})", flush=True)
    e = dict(os.environ)
    if env:
        e.update(env)
    r = subprocess.run(cmd, cwd=cwd, shell=True, env=e, timeout=timeout,
                       capture_output=True, text=True)
    return r


def build_repo(inst: dict, workdir: str, variant: str) -> str:
    """Reconstruct the repo at the buggy base, write tests, install deps. variant: gold|buggy."""
    repo = inst["repo"]
    url = inst["git_url"] or REPO_GIT_URL[repo]
    proxy = (Path(__file__).resolve().parents[2] / "vendor/.proxy_url").read_text().strip()
    penv = {"http_proxy": proxy, "https_proxy": proxy, "GIT_TERMINAL_PROMPT": "0",
            # force uv to target the repo-local .venv, never a sibling discovered up the tree
            "UV_PROJECT_ENVIRONMENT": ".venv"}

    dst = Path(workdir) / f"{inst['instance_id']}__{variant}"
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    # 1. clone + checkout buggy base (build-at-path, like swesmith)
    r = run(f"git clone {url} {dst}", timeout=900, env=penv)
    assert r.returncode == 0, f"clone failed:\n{r.stderr[-2000:]}"
    fix = inst["fix_commit"]
    # fetch the fix commit object so that '<fix>^' resolves even if shallow/missing
    run(f"git fetch origin {fix}", cwd=str(dst), timeout=600, env=penv)
    r = run(f"git checkout -f {inst['base_commit']}", cwd=str(dst), env=penv)
    assert r.returncode == 0, f"checkout {inst['base_commit']} failed:\n{r.stderr[-2000:]}"
    resolved = run("git rev-parse HEAD", cwd=str(dst)).stdout.strip()
    print(f"  base_commit {inst['base_commit']} -> {resolved}")

    # 2. gold variant: overwrite non-test source files with fixed content (apply gold patch)
    if variant == "gold":
        for path, content in inst["gold_files"].items():
            fp = dst / path
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content)
            print(f"  [gold] wrote {path} ({len(content)} bytes)")

    # 3. LEAK DEFENSE: strip git history (the fix is reachable in forward history)
    shutil.rmtree(dst / ".git", ignore_errors=True)

    # 4. write graded tests into r2e_tests/
    tdir = dst / "r2e_tests"
    tdir.mkdir(exist_ok=True)
    (tdir / "__init__.py").write_text("")
    for name, code in inst["test_files"].items():
        (tdir / name).write_text(code)
    print(f"  wrote {len(inst['test_files'])} graded test files into r2e_tests/")

    # 5. install deps via the repo's install.sh (uv venv) + aiohttp post-process
    install_sh = INSTALL_UTILS / f"{repo}_install.sh"
    # NOTE: the shipped install scripts have CRLF line endings -> 'source .venv/bin/activate\r'
    # fails, VIRTUAL_ENV never gets set, and uv silently falls back to a sibling .venv up the
    # tree. Strip CRLF + force uv to target THIS repo's .venv.
    raw = install_sh.read_text().replace("\r\n", "\n").replace("\r", "\n")
    (dst / "install.sh").write_text(raw)
    if repo == "aiohttp":
        shutil.copy(INSTALL_UTILS / "process_aiohttp_updateasyncio.py",
                    dst / "process_aiohttp_updateasyncio.py")
    r = run("bash install.sh", cwd=str(dst), timeout=2400, env=penv)
    print("---- install.sh stdout tail ----")
    print(r.stdout[-1500:])
    print("---- install.sh stderr tail ----")
    print(r.stderr[-1500:])
    if r.returncode != 0:
        print(f"  !! install.sh returncode={r.returncode}")
    (dst / "run_tests.sh").write_text(inst["test_cmd"] + "\n")
    return str(dst)


# ----------------------------------------------------------------------------- score
def score(inst: dict, repo_dir: str, timeout: int = 600) -> dict:
    """Run graded tests, parse, exact-match vs expected_output_json. reward in {0.0,1.0}."""
    r = run(f"bash {inst['test_cmd']!r}" if False else inst["test_cmd"],
            cwd=repo_dir, timeout=timeout)
    output = r.stdout + "\n" + r.stderr
    parse = parse_log_fn(inst["repo"])(output)
    parse = decolor_dict_keys(parse)
    expected = decolor_dict_keys(inst["expected_output_json"])
    parse = {k.split(" - ")[0]: parse[k] for k in sorted(parse)}
    expected = {k.split(" - ")[0]: expected[k] for k in sorted(expected)}

    if len(parse) != len(expected):
        reward, reason = 0.0, f"count mismatch parsed={len(parse)} expected={len(expected)}"
    else:
        mism = [(k, expected.get(k), parse[k]) for k in parse if k and parse[k] != expected.get(k)]
        reward = 1.0 if not mism else 0.0
        reason = "all match" if not mism else f"{len(mism)} mismatch(es): {mism[:5]}"
    return {"reward": reward, "reason": reason, "n_parsed": len(parse),
            "n_expected": len(expected), "rc": r.returncode}


# ----------------------------------------------------------------------------- logcheck
def logcheck(src: str) -> None:
    """Validate the scorer using the RECORDED old/new logs in the row (no build)."""
    row = load_row(src)
    inst = make_instance(row)
    er = json.loads(row["execution_result_content"])
    parse = parse_log_fn(inst["repo"])
    expected = inst["expected_output_json"]
    for label, key in [("FIXED(new)", "new_commit_res_stdout"), ("BUGGY(old)", "old_commit_res_stdout")]:
        p = decolor_dict_keys(parse(er[key]))
        match = sum(1 for k, v in expected.items() if p.get(k) == v)
        verdict = "reward=1.0" if match == len(expected) and len(p) == len(expected) else "reward=0.0"
        print(f"  {label}: parsed={len(p)} match_expected={match}/{len(expected)} -> {verdict}")


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("load"); p.add_argument("--src", required=True); p.add_argument("--out", required=True)
    p = sub.add_parser("build"); p.add_argument("--inst", required=True); p.add_argument("--workdir", required=True); p.add_argument("--variant", choices=["gold", "buggy"], required=True)
    p = sub.add_parser("score"); p.add_argument("--inst", required=True); p.add_argument("--repo", required=True)
    p = sub.add_parser("all"); p.add_argument("--src", required=True); p.add_argument("--workdir", required=True)
    p = sub.add_parser("logcheck"); p.add_argument("--src", required=True)
    a = ap.parse_args()

    if a.cmd == "load":
        inst = make_instance(load_row(a.src))
        json.dump(inst, open(a.out, "w"), indent=1)
        print(f"instance_id={inst['instance_id']} base={inst['base_commit']} "
              f"tests={list(inst['test_files'])} gold_files={list(inst['gold_files'])} "
              f"n_expected={len(inst['expected_output_json'])}\nwrote {a.out}")
    elif a.cmd == "build":
        inst = json.load(open(a.inst))
        print("built ->", build_repo(inst, a.workdir, a.variant))
    elif a.cmd == "score":
        inst = json.load(open(a.inst))
        print(json.dumps(score(inst, a.repo), indent=1))
    elif a.cmd == "logcheck":
        logcheck(a.src)
    elif a.cmd == "all":
        inst = make_instance(load_row(a.src))
        ipath = Path(a.workdir) / "instance.json"; ipath.parent.mkdir(parents=True, exist_ok=True)
        json.dump(inst, open(ipath, "w"), indent=1)
        results = {}
        for variant in ["buggy", "gold"]:
            print(f"\n========== BUILD {variant} ==========")
            d = build_repo(inst, a.workdir, variant)
            print(f"\n========== SCORE {variant} ==========")
            results[variant] = score(inst, d)
            print(json.dumps(results[variant], indent=1))
        print("\n========== SUMMARY ==========")
        for v, r in results.items():
            print(f"  {v:6s}: reward={r['reward']}  ({r['reason'][:80]})")
        gate = results["gold"]["reward"] == 1.0 and results["buggy"]["reward"] == 0.0
        print(f"  DISCRIMINATES (gold=1, buggy=0): {gate}")


if __name__ == "__main__":
    main()

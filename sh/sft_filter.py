#!/usr/bin/env python
"""Codified SFT trajectory quality filter — one deterministic pipeline replacing ad-hoc checks.

Each generation batch = a (traj_dir with *.traj.json, score preds.json) pair. A trajectory
is KEPT only if it passes ALL enabled checks; every drop is attributed to a reason so the
yield is auditable. Run it instead of hand-inspecting.

Checks (drop reasons):
  not_resolved   : score reward != 1
  not_faithful   : instance not in the buggy-confirmed faithful list (--faithful)
  empty_patch    : model_patch has no real change (empty / whitespace only)  [vacuous/degenerate]
  test_only      : patch touches ONLY test files, no source                  [anti test-gaming]
  limits_exceeded: agent exit_status == LimitsExceeded (didn't finish in step budget) [inefficient/incomplete]
  pip_leak       : trajectory ran `pip install/download <pkg>` to fetch upstream answer [leakage]
  githist_leak   : (only if --drop-githist) `git show <ref>:file` / cp-to-/tmp/orig reference-revert
  too_long       : est tokens > --max-tokens

Usage:
  python sh/sft_filter.py \
    --batch glm-904a2/out:glm-904a2/score --batch glm-904b2/out:glm-904b2/score \
    --batch glm-904/out:glm-904/score92 --batch glm-sample-50/glm50:glm50-rescore/score \
    --faithful vendor/faithful_verified.txt \
    --out vendor/sft-data/sft_clean.jsonl --max-tokens 28000 [--drop-githist]
"""
import argparse, glob, json, os, re, sys
from collections import Counter

CACHE = "vendor/swesmith-cache"
KEEP_ROLES = {"system", "user", "assistant"}
PIP_LEAK = re.compile(r'pip\s+(install|download)\s+(?!-e\b)[A-Za-z0-9_.\-]', re.I)
TEST_FILE = re.compile(r'(^|/)tests?/|test_|conftest|_test\.py')
# swesmith leak: history is [Initial(CLEAN) -> Bug Patch(buggy) -> Remove F2P(HEAD)], so
# `git show <old-commit/HEAD^/~N>:<source-file>` retrieves the un-perturbed answer (verified
# 2026-06-25). Reading a SOURCE file from a non-HEAD ref = peeking at clean code = leak.
# (Reading HEAD:file = current/buggy = ok; reading <ref>:test_file = understanding expected = ok.)
_GIT_SHOW = re.compile(r'git\s+show\s+(\S+?):(\S+)')
_CP_ORIG = re.compile(r'cp\s+-r?\s+\S*\s+/tmp/(orig|real|ref|correct|upstream|golden)', re.I)
# CAPABILITY-based detection (broadened 2026-06-26 after audit found the exact-command allowlist
# trivially bypassable). The leak = reading any blob/diff/log of a SOURCE file from a NON-HEAD ref
# (HEAD~N/HEAD^/<hex>/origin), regardless of which git verb does it. A ref-token is REQUIRED after
# the verb so benign `git diff` / `git checkout -b foo` / `git status` are NOT flagged.
_HIST_REF = r'(?:HEAD[~^]|[0-9a-f]{7,40}\b|origin/)'
_GIT_HIST = re.compile(r'git\s+(?:show|cat-file|checkout|restore|diff|revert|worktree\s+add)\b[^\n|;&`]*' + _HIST_REF, re.I)
_GIT_LOGP = re.compile(r'git\s+log\b[^\n|;&`]*-p\b', re.I)  # `git log -p` walks history -> clean source + bug diff

def githist_source_leak(cmds):
    if _CP_ORIG.search(cmds) or _GIT_HIST.search(cmds) or _GIT_LOGP.search(cmds):
        return True
    for ref, path in _GIT_SHOW.findall(cmds):
        if ref != "HEAD" and not TEST_FILE.search(path):  # source from a non-current commit
            return True
    return False


def patch_files(diff):
    return re.findall(r'diff --git a/\S+ b/(\S+)', diff) or re.findall(r'^\+\+\+ b/(.+)$', diff, re.M)


def patch_has_change(diff):
    return bool(re.search(r'^[+-](?![+-])', diff or "", re.M))  # any +/- content line


def assistant_cmds(messages):
    return "\n".join(m.get("content", "") or "" for m in messages if m.get("role") == "assistant")


def clean_messages(messages):
    out = []
    for m in messages:
        if m.get("role") not in KEEP_ROLES:
            continue
        out.append({"role": m["role"], "content": str(m.get("content", "") or "")})
    return out


_TOKENIZER = None
def _get_tokenizer():
    """Real tokenizer (Qwen3) for accurate length budgeting. char/4 undercounts ~7.6% and let
    over-cap trajectories through -> right-truncation severed the final SUBMIT turn. Override path
    via SFT_TOKENIZER env. Falls back to char/4 if transformers/tokenizer unavailable."""
    global _TOKENIZER
    if _TOKENIZER is None:
        from transformers import AutoTokenizer
        _TOKENIZER = AutoTokenizer.from_pretrained(
            os.environ.get("SFT_TOKENIZER", "/path/to/workspace/models/Qwen3-8B"))
    return _TOKENIZER

def est_tokens(messages):
    try:
        tok = _get_tokenizer()
        return len(tok.apply_chat_template(messages, tokenize=True, add_generation_prompt=False))
    except Exception as e:
        print(f"[warn] real tokenizer unavailable ({e}); falling back to char/4", file=sys.stderr)
        return sum(len(m["content"]) for m in messages) // 4


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", action="append", required=True, help="traj_dir:score_dir (relative to vendor/swesmith-cache)")
    ap.add_argument("--faithful", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-tokens", type=int, default=28000)
    ap.add_argument("--keep-githist", action="store_true", help="KEEP git-history source-leak trajectories (default: drop them — confirmed swesmith leak)")
    ap.add_argument("--min-assistant-turns", type=int, default=1)
    args = ap.parse_args()

    faithful = set(l.strip() for l in open(args.faithful) if l.strip())
    drops = Counter()
    kept = {}
    seen = set()

    for b in args.batch:
        td, sd = b.split(":")
        try:
            score = json.load(open(f"{CACHE}/{sd}/preds.json"))
            gen = json.load(open(f"{CACHE}/{td}/preds.json"))
        except Exception as e:
            print(f"[warn] skip batch {b}: {e}")
            continue
        for tf in glob.glob(f"{CACHE}/{td}/*.traj.json"):
            try:
                d = json.load(open(tf))
            except Exception:
                drops["bad_json"] += 1; continue
            iid = d.get("instance_id") or os.path.basename(tf).split(".traj.json")[0]
            if iid in seen:
                continue
            # ---- checks ----
            if score.get(iid, {}).get("reward") != 1:
                drops["not_resolved"] += 1; continue
            if iid not in faithful:
                drops["not_faithful"] += 1; continue
            patch = gen.get(iid, {}).get("model_patch", "") or ""
            if not patch_has_change(patch):
                drops["empty_patch"] += 1; continue
            fs = patch_files(patch)
            if fs and all(TEST_FILE.search(f) for f in fs):
                drops["test_only"] += 1; continue
            if d.get("info", {}).get("exit_status") == "LimitsExceeded":
                drops["limits_exceeded"] += 1; continue
            cmds = assistant_cmds(d.get("messages", []))
            if PIP_LEAK.search(cmds):
                drops["pip_leak"] += 1; continue
            if not args.keep_githist and githist_source_leak(cmds):
                drops["githist_leak"] += 1; continue
            msgs = clean_messages(d.get("messages", []))
            if sum(1 for m in msgs if m["role"] == "assistant") < args.min_assistant_turns:
                drops["too_short"] += 1; continue
            if est_tokens(msgs) > args.max_tokens:
                drops["too_long"] += 1; continue
            seen.add(iid)
            kept[iid] = {"instance_id": iid, "messages": msgs}

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        for v in kept.values():
            f.write(json.dumps(v, ensure_ascii=False) + "\n")
    # parquet alongside
    try:
        import pyarrow as pa, pyarrow.parquet as pq
        pq.write_table(pa.Table.from_pylist(list(kept.values())), args.out.replace(".jsonl", ".parquet"))
    except Exception as e:
        print(f"[warn] parquet skipped: {e}")

    print(f"=== SFT filter report ===")
    print(f"KEPT (clean SFT) = {len(kept)}")
    for r, n in drops.most_common():
        print(f"  dropped[{r}] = {n}")
    print(f"-> {args.out} (+ .parquet)")


if __name__ == "__main__":
    main()

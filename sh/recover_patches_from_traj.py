#!/usr/bin/env python
"""Re-derive model_patch from generation trajectories.

Background: the swebench_backticks agent submits via `git diff -- <files> > patch.txt` then
`cat patch.txt`. Many rollouts revert/re-apply the working tree before submitting, so the
post-hoc `git diff HEAD` the gen harness captured came back EMPTY even though the correct patch
lives in the agent's submission (patch.txt content, echoed in the final tool result). This
recovers those: for each traj it takes the saved patch (if non-empty) else scans the message
results for the best clean diff (the patch.txt content). Writes a corrected preds.json.

Usage: python sh/recover_patches_from_traj.py <traj_dir> [out_preds.json]
"""
import json, sys, glob
from pathlib import Path


def extract_diff(text):
    if not text or not text.strip():
        return ""
    i = text.find("diff --git ")
    if i == -1:
        return ""
    return text[i:].strip() + "\n"


def best_from_messages(msgs):
    """Return the best clean diff found in tool-result messages (the patch.txt content).
    Prefer the LAST diff that does NOT include forbidden scratch files (patch.txt/reproduce)."""
    cands = []
    for m in msgs:
        c = m.get("content", "")
        if not isinstance(c, str):
            continue
        d = extract_diff(c)
        if not d:
            continue
        # skip diffs that touch scratch/test infra files (the config forbids these in patch.txt)
        bad = any(f"diff --git a/{x}" in d or f"+++ b/{x}" in d
                  for x in ("patch.txt", "reproduce", "test_repro"))
        cands.append((d, bad, len(d)))
    if not cands:
        return ""
    clean = [c for c in cands if not c[1]]
    pool = clean or cands
    return pool[-1][0]  # the last (final) diff the agent produced


def main():
    traj_dir = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else traj_dir / "preds_recovered.json"
    preds = {}
    n_orig_nonempty = n_recovered = n_empty = 0
    for tf in sorted(glob.glob(str(traj_dir / "*.traj.json"))):
        d = json.load(open(tf))
        iid = d["instance_id"]
        info = d.get("info", {})
        patch = extract_diff(info.get("submission", "")) or extract_diff(info.get("agent_submission", ""))
        if patch:
            n_orig_nonempty += 1
        else:
            patch = best_from_messages(d.get("messages", []))
            if patch:
                n_recovered += 1
            else:
                n_empty += 1
        preds[iid] = {"model_name_or_path": "glm-5.2", "instance_id": iid, "model_patch": patch}
    out.write_text(json.dumps(preds, indent=2))
    tot = len(preds)
    print(f"trajs={tot}  orig_nonempty={n_orig_nonempty}  RECOVERED={n_recovered}  still_empty={n_empty}")
    print(f"  -> nonempty now {n_orig_nonempty + n_recovered}/{tot} "
          f"({100*(n_orig_nonempty+n_recovered)//max(tot,1)}%)  wrote {out}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""Convert mini-swe-agent trajectories -> SFT training data (clean masking).

mini-swe-agent trajectories are a flat chat list:
    system -> user(task) -> [assistant(THOUGHT+```bash```), user(<returncode><output> observation)]* -> exit
This maps directly to multi-turn SFT where ONLY assistant turns carry loss:
  - system / user(task) / user(observation)  -> masked (no loss)
  - assistant (the model's thought + command) -> loss-bearing
Environment observations come back as `user`-role messages, so a standard
"train on assistant turns only" SFT setup masks them automatically. We only emit
{role, content}, dropping litellm response artifacts (tool_calls, function_call,
extra, provider_specific_fields) and the terminal `exit` message.

Output: JSONL, one sample/line: {"instance_id": ..., "messages": [{role,content}...]}
Compatible with ms-swift (messages) and torchtune chat_dataset (conversation
column `messages`, openai style, train_on_input=false).

Usage:
  python sh/traj_to_sft.py --traj-dir <dir-with-*.traj.json> --out sft.jsonl \
     [--resolved-from results.json]   # only keep trajectories that RESOLVED (quality filter)
     [--min-assistant-turns 1]
"""
import argparse
import glob
import json
import os
import sys

_KEEP_ROLES = {"system", "user", "assistant"}
_STRIP_KEYS = {"tool_calls", "function_call", "provider_specific_fields", "extra", "name"}


def load_resolved_ids(path: str) -> set | None:
    if not path:
        return None
    d = json.load(open(path))
    # results.json from postprocess scoring has resolved_ids
    ids = d.get("resolved_ids")
    if ids is None and isinstance(d, dict):
        # accept swesmith score preds.json ({iid: {reward: 1}}) as well as {resolved: true}
        ids = [k for k, v in d.items() if isinstance(v, dict) and (v.get("resolved") or v.get("reward") == 1)]
    return set(ids or [])


def clean_messages(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        role = m.get("role")
        if role not in _KEEP_ROLES:  # drop 'exit' and any non-chat roles
            continue
        content = m.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        out.append({"role": role, "content": content})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--traj-dir", required=True, help="dir containing *.traj.json (recursive)")
    ap.add_argument("--out", required=True, help="output JSONL path")
    ap.add_argument("--resolved-from", default="", help="results.json: keep only resolved instances")
    ap.add_argument("--min-assistant-turns", type=int, default=1)
    args = ap.parse_args()

    resolved = load_resolved_ids(args.resolved_from)
    trajs = sorted(set(
        glob.glob(os.path.join(args.traj_dir, "**", "*.traj.json"), recursive=True)
        + glob.glob(os.path.join(args.traj_dir, "*.traj.json"))
    ))
    kept = skipped_unresolved = skipped_short = 0
    with open(args.out, "w") as fout:
        for t in trajs:
            try:
                d = json.load(open(t))
            except Exception as e:
                print(f"skip (bad json) {t}: {e}", file=sys.stderr)
                continue
            iid = d.get("instance_id") or os.path.basename(t).split(".traj.json")[0]
            if resolved is not None and iid not in resolved:
                skipped_unresolved += 1
                continue
            msgs = clean_messages(d.get("messages", []))
            n_assistant = sum(1 for m in msgs if m["role"] == "assistant")
            if n_assistant < args.min_assistant_turns:
                skipped_short += 1
                continue
            fout.write(json.dumps({"instance_id": iid, "messages": msgs}, ensure_ascii=False) + "\n")
            kept += 1

    print(f"trajectories found: {len(trajs)}")
    print(f"kept: {kept}  skipped_unresolved: {skipped_unresolved}  skipped_short: {skipped_short}")
    print(f"-> {args.out}")
    if resolved is not None:
        print(f"(quality filter: only {len(resolved)} resolved instance_ids)")


if __name__ == "__main__":
    main()

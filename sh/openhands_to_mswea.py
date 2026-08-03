#!/usr/bin/env python
"""OpenHands-trajectory -> mini-swe-agent messages converter + conversion-loss meter.

Reads nvidia/SWE-Zero-openhands-trajectories rows (trajectory = list of
{role, content, tool_calls}) and converts each to our messages format
(system/user/assistant, assistant = "THOUGHT:\\n```mswea_bash_command <cmd>```",
tool output folded into user turn).

OpenHands tool calls seen: execute_bash, think, finish, str_replace_editor
(view|create|str_replace|insert|undo_edit).

Conversion rules + loss accounting (the point of this script):
  execute_bash            -> bash fence            LOSSLESS  (unless is_input=true: LOSSY)
  finish                  -> submit marker fence   LOSSLESS
  think                   -> folded into next THOUGHT (no command)  STRUCTURAL (foldable)
  str_replace_editor view -> cat/sed/ls equivalent CONVERTIBLE
  str_replace_editor create-> cat heredoc          CONVERTIBLE
  str_replace_editor str_replace -> python b64 replace (unique, deterministic) CONVERTIBLE
  str_replace_editor insert -> python b64 insert    CONVERTIBLE
  str_replace_editor undo_edit -> NO bash equivalent (needs edit-history state)  LOSSY

A tool call is counted LOSSY if it cannot be reproduced as a single deterministic
bash command with the same effect. A trajectory is LOSSY if it has >=1 lossy call.
"""
import json, base64, argparse
from collections import Counter

SUBMIT_MARKER = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"


def b64(s: str) -> str:
    return base64.b64encode((s or "").encode()).decode()


def conv_execute_bash(args):
    if str(args.get("is_input", "false")).lower() == "true":
        return None, "execute_bash:is_input"  # mid-process interaction: no single-command analog
    return args.get("command", ""), None


def conv_finish(args):
    return f"echo {SUBMIT_MARKER} && cd /testbed && git diff HEAD", None


def conv_str_replace_editor(args):
    cmd = args.get("command")
    path = args.get("path", "")
    if cmd == "view":
        vr = args.get("view_range")
        if vr and isinstance(vr, list) and len(vr) == 2:
            return f"sed -n '{vr[0]},{vr[1]}p' {path}", None
        # view of dir or whole file -> cat (file) / find (dir). Use a probe that works for both.
        return f"if [ -d {path} ]; then find {path} -maxdepth 2; else cat -n {path}; fi", None
    if cmd == "create":
        ft = args.get("file_text", "")
        return f"echo {b64(ft)} | base64 -d > {path}", None
    if cmd == "str_replace":
        old = args.get("old_str", "")
        new = args.get("new_str", "")
        # deterministic, unique-replace with assertion (OpenHands guarantees old_str unique)
        py = (
            "import base64,sys;"
            f"p={path!r};"
            f"o=base64.b64decode('{b64(old)}').decode();"
            f"n=base64.b64decode('{b64(new)}').decode();"
            "s=open(p).read();"
            "assert s.count(o)==1, 'old_str not unique';"
            "open(p,'w').write(s.replace(o,n,1))"
        )
        return f"python3 -c \"{py}\"", None
    if cmd == "insert":
        line = args.get("insert_line", 0)
        new = args.get("new_str", "")
        py = (
            "import base64;"
            f"p={path!r};L={line};"
            f"n=base64.b64decode('{b64(new)}').decode();"
            "ls=open(p).read().splitlines(True);"
            "ls.insert(L, n if n.endswith(chr(10)) else n+chr(10));"
            "open(p,'w').writelines(ls)"
        )
        return f"python3 -c \"{py}\"", None
    if cmd == "undo_edit":
        return None, "str_replace_editor:undo_edit"  # no stateless bash equivalent
    return None, f"str_replace_editor:{cmd}:unknown"


def convert_trajectory(traj):
    """Return (messages, stats) where stats counts actions and losses for this trajectory."""
    msgs = []
    pending_think = []  # think contents to fold into next assistant THOUGHT
    stats = Counter()
    lossy = False
    for m in traj:
        role = m.get("role")
        content = m.get("content") or ""
        tcs = m.get("tool_calls") or []
        if role == "system":
            msgs.append({"role": "system", "content": content})
        elif role == "user":
            msgs.append({"role": "user", "content": content})
        elif role == "tool":
            # tool output -> fold into a user turn (mini-swe-agent style)
            # skip the "Your thought has been logged." filler that follows a think call
            if content.strip() == "Your thought has been logged.":
                continue
            msgs.append({"role": "user", "content": f"<output>\n{content}\n</output>"})
        elif role == "assistant":
            if not tcs:
                # plain assistant text with no action -> fold into pending think
                if content.strip():
                    pending_think.append(content.strip())
                continue
            # one assistant turn per tool call
            for tc in tcs:
                fn = tc.get("function", {})
                name = fn.get("name")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}
                    stats["bad_json_args"] += 1
                stats[f"action:{name}"] += 1
                stats["action:_total"] += 1
                if name == "think":
                    stats["think_total"] += 1
                    th = args.get("thought", "")
                    if th.strip():
                        pending_think.append(th.strip())
                    continue  # no command emitted; folded
                if name == "execute_bash":
                    cmd, loss = conv_execute_bash(args)
                elif name == "finish":
                    cmd, loss = conv_finish(args)
                elif name == "str_replace_editor":
                    cmd, loss = conv_str_replace_editor(args)
                    stats[f"sre:{args.get('command')}"] += 1
                else:
                    cmd, loss = None, f"unknown_tool:{name}"
                if loss:
                    stats[f"lossy:{loss}"] += 1
                    stats["lossy_total"] += 1
                    lossy = True
                    continue
                # build THOUGHT from any pending think + this turn's assistant content
                thought_parts = pending_think + ([content.strip()] if content.strip() else [])
                pending_think = []
                thought = "\n\n".join(thought_parts) or "(no thought)"
                msgs.append({
                    "role": "assistant",
                    "content": f"THOUGHT: {thought}\n\n```mswea_bash_command\n{cmd}\n```",
                })
    stats["lossy_trajectory"] = 1 if lossy else 0
    return msgs, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", default="/tmp/swez/train-00000.parquet")
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--out", default="/tmp/swez/converted.jsonl")
    args = ap.parse_args()

    import pyarrow.parquet as pq
    pf = pq.ParquetFile(args.parquet)
    rows = []
    for batch in pf.iter_batches(batch_size=500):
        rows.extend(batch.to_pylist())
        if len(rows) >= args.n:
            break
    rows = rows[: args.n]

    agg = Counter()
    n_lossy_traj = 0
    out = open(args.out, "w")
    for r in rows:
        msgs, st = convert_trajectory(r["trajectory"])
        agg.update(st)
        if st["lossy_trajectory"]:
            n_lossy_traj += 1
        out.write(json.dumps({"instance_id": r["instance_id"], "messages": msgs}) + "\n")
    out.close()

    n = len(rows)
    total_actions = agg["action:_total"]
    print(f"=== converted {n} trajectories -> {args.out} ===")
    print(f"total tool-calls: {total_actions}  ({total_actions/n:.1f}/traj)")
    print("\n-- ACTION-TYPE DISTRIBUTION --")
    for k in sorted([x for x in agg if x.startswith("action:") and x != "action:_total"]):
        v = agg[k]
        print(f"  {k[7:]:20s} {v:7d}  {100*v/total_actions:5.1f}%")
    print("\n-- str_replace_editor sub-commands --")
    for k in sorted([x for x in agg if x.startswith("sre:")]):
        print(f"  {k[4:]:14s} {agg[k]:7d}")
    print("\n-- LOSSY ACTIONS --")
    lt = agg["lossy_total"]
    for k in sorted([x for x in agg if x.startswith("lossy:")]):
        print(f"  {k[6:]:30s} {agg[k]:7d}")
    print(f"\n  lossy actions total: {lt} / {total_actions} = {100*lt/max(total_actions,1):.3f}% of actions")
    print(f"  lossy trajectories : {n_lossy_traj} / {n} = {100*n_lossy_traj/n:.2f}% of trajectories")
    print(f"  think actions (folded, not lossy): {agg['think_total']} ({100*agg['think_total']/total_actions:.1f}% of actions)")
    print(f"  bad_json_args: {agg['bad_json_args']}")


if __name__ == "__main__":
    main()

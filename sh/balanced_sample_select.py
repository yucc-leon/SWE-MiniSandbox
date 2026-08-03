#!/usr/bin/env python3
"""Dual-marginal balanced selection for GLM trajectory sampling.

Picks a subset of the FAITHFUL pool so that:
  - every bug TYPE is covered ~evenly, each occurrence in a DIFFERENT repo (type robustness)
  - every REPO is covered ~evenly, each with DIFFERENT types (repo coverage)
  - (repo,type) pairings rotated -> minimal redundancy (no full R*T grid, no concentration)

Greedy: repeatedly take the faithful instance whose (repo_count + type_count) is smallest,
preferring an unused (repo,type) cell. Balances both margins simultaneously.

Usage:
  python sh/balanced_sample_select.py --target 320 [--faithful f1.txt f2.txt ...] --out vendor/glm_balanced_filter.txt
"""
import argparse, re, collections, glob

def parse(iid):
    m = re.match(r"(.+?)\.[0-9a-f]+\.(func_pm_[a-z_]+|func_basic|combine_file|combine_module|lm_rewrite)__([0-9a-z]+)$", iid)
    return (m.group(1), m.group(2), m.group(3)) if m else None  # repo, type, hash

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--faithful", nargs="*", default=[
        "vendor/salvaged_instances.txt", "vendor/screen300_faithful.txt",
        "vendor/screen600_faithful.txt"])
    ap.add_argument("--extra-glob", default="vendor/*_faithful.txt")
    ap.add_argument("--target", type=int, default=320)
    ap.add_argument("--out", default="vendor/glm_balanced_filter.txt")
    args = ap.parse_args()

    files = set(args.faithful) | set(glob.glob(args.extra_glob))
    pool = {}
    for f in files:
        try:
            for iid in open(f).read().split():
                p = parse(iid)
                if p: pool[iid] = p
        except FileNotFoundError:
            pass
    items = [(iid, *p) for iid, p in pool.items()]
    # bucket by (repo,type)
    by_cell = collections.defaultdict(list)
    for iid, repo, typ, h in items:
        by_cell[(repo, typ)].append(iid)

    rc, tc = collections.Counter(), collections.Counter()
    used_cell = set()
    picked = []
    cells = list(by_cell)
    target = min(args.target, len(items))
    while len(picked) < target:
        # candidate cells with remaining instances
        best = None; best_key = None
        for cell in cells:
            if not by_cell[cell]: continue
            repo, typ = cell
            score = rc[repo] + tc[typ]
            # prefer unused cells (tie-break): penalize already-used cell
            score += (5 if cell in used_cell else 0)
            if best is None or score < best_key:
                best_key = score; best = cell
        if best is None: break
        iid = by_cell[best].pop()
        picked.append(iid); used_cell.add(best)
        rc[best[0]] += 1; tc[best[1]] += 1

    hashes = sorted({parse(i)[2] for i in picked})
    open(args.out, "w").write(".*__(" + "|".join(hashes) + ")$")
    print(f"faithful 池={len(items)}  选出={len(picked)} -> {args.out}")
    print(f"覆盖 repo={len(rc)}/{len(set(r for _,r,_,_ in items))}  类型={len(tc)}")
    print(f"每类型条数: {dict(tc.most_common())}")
    print(f"repo 条数分布: min={min(rc.values())} max={max(rc.values())} 均值={sum(rc.values())/len(rc):.1f}")
    dup = len(picked) - len(used_cell)
    print(f"重复(repo,type)对: {dup} (0=每对最多1条,完全差异化)")

if __name__ == "__main__":
    main()

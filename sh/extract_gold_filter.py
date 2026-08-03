#!/usr/bin/env python3
"""建完后从 build-output 提取 gold→1 实例,生成 Stage B / 一致性复验用的 filter。
gold→1 = pred 文件 reward==1(pre_check=true 下 gold patch 使目标测试通过 = 保真前提之一)。
"""
import json, glob, os, re, sys, collections

POOL = sys.argv[1] if len(sys.argv) > 1 else "vendor/swesmith-cache/chroot-diverse-v2"
OUT = sys.argv[2] if len(sys.argv) > 2 else "vendor/chroot_v2_gold_filter.txt"

preds = glob.glob(f"{POOL}/build-output/**/*.pred", recursive=True)
gold1, gold0 = [], []
for p in preds:
    iid = os.path.basename(p)[:-5]  # strip .pred
    try:
        d = json.load(open(p))
    except Exception as e:
        gold0.append((iid, f"parse_err:{e}")); continue
    r = d.get("reward", None)
    (gold1 if r == 1 else gold0).append((iid, r))

# family 分布
def fam(iid):
    m = re.search(r"\.(func_pm[a-z_]*|func_basic|combine_file|combine_module)", iid)
    return m.group(1).split("__")[0] if m else "?"
fams = collections.Counter(fam(i) for i, _ in gold1)
repos = set(i.split(".")[0] for i, _ in gold1)

# filter = 末尾 hash 的正则 alternation(和 diverse filter 同构)
hashes = []
for iid, _ in gold1:
    m = re.search(r"__([0-9a-z]+)$", iid)
    if m:
        hashes.append(m.group(1))
filt = ".*__(" + "|".join(sorted(set(hashes))) + ")$" if hashes else "MATCH_NOTHING_xxxxx"

print(f"总 pred={len(preds)}  gold→1={len(gold1)}  gold→0/err={len(gold0)}")
print(f"gold→1 覆盖仓库={len(repos)}  family={dict(fams)}")
print(f"gold→0 样例: {gold0[:5]}")
with open(OUT, "w") as f:
    f.write(filt)
print(f"已写 filter -> {OUT}")
print(f"filter 长度={len(filt)}  匹配 hash 数={len(set(hashes))}")

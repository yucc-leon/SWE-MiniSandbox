"""32 并发模拟真实 pytest basetemp 编号碰撞。"""

import os
import shutil
import concurrent.futures
from collections import Counter

basetemp = "/tmp/pytest-of-root"
os.makedirs(basetemp, exist_ok=True)
for d in os.listdir(basetemp):
    shutil.rmtree(os.path.join(basetemp, d), ignore_errors=True)


def simulate_real_pytest(sandbox_id):
    # Real pytest: find first non-existing index, create with exist_ok=True
    for i in range(1000):
        candidate = os.path.join(basetemp, f"pytest-{i}")
        if not os.path.exists(candidate):
            os.makedirs(candidate, exist_ok=True)
            marker = os.path.join(candidate, f"owner_{sandbox_id}.txt")
            with open(marker, "w") as f:
                f.write(f"sandbox_{sandbox_id}")
            return candidate, i
    return None, -1


num = 32
results = {}
with concurrent.futures.ThreadPoolExecutor(max_workers=num) as ex:
    futures = {ex.submit(simulate_real_pytest, i): i for i in range(num)}
    for f in concurrent.futures.as_completed(futures):
        sid = futures[f]
        path, idx = f.result()
        results[sid] = (path, idx)

idx_counts = Counter(idx for _, idx in results.values())
shared = {idx: count for idx, count in idx_counts.items() if count > 1}

if shared:
    print(f"!! {len(shared)} indices shared by multiple sandboxes:")
    for idx, count in sorted(shared.items()):
        d = os.path.join(basetemp, f"pytest-{idx}")
        files = os.listdir(d) if os.path.exists(d) else []
        print(f"  pytest-{idx}: {count} sandboxes, files in dir: {files}")
else:
    print(f"No collision among {num} concurrent sessions")

print(f"Index allocation: {sorted(idx for _, idx in results.values())}")
shutil.rmtree(basetemp, ignore_errors=True)

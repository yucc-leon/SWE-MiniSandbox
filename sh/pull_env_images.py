#!/usr/bin/env python3
"""One-shot resolver + puller for all Docker images needed by SWE-agent eval / env-factory.

Sources & how each image name is derived (all verified against the installed libs/datasets):
  - verified : SWE-bench Verified. make_test_spec(inst, namespace="swebench").instance_image_key
               (== exactly what the harness pulls; swebench 4.x, test_spec.py).
  - pro      : SWE-bench Pro. "jefzda/sweap-images:{row['dockerhub_tag']}" (scaleapi/SWE-bench_Pro-os).
  - swesmith : "image_name" column, e.g. jyangballin/swesmith.x86_64.oauthlib_1776_oauthlib.1fd52536.
  - r2e      : NOT wired — R2E-Gym uses per-repo install.sh, image scheme unconfirmed. See note below.

Run ON THE x86 machine that has Docker. Two phases:
  1) dry-run first to get the dedup'd image manifest (for whitelist / internal mirror):
        python sh/pull_env_images.py --sources verified,pro,swesmith --dry-run
  2) then actually pull:
        python sh/pull_env_images.py --sources verified,pro,swesmith --workers 8

IMPORTANT (ops): `docker pull` uses the DOCKER DAEMON proxy, NOT shell http_proxy.
In a restricted-egress env you MUST configure the daemon proxy first:
    /etc/systemd/system/docker.service.d/http-proxy.conf  ->  [Service]
      Environment="HTTP_PROXY=..."  "HTTPS_PROXY=..."  "NO_PROXY=localhost,127.0.0.1,<internal>"
    systemctl daemon-reload && systemctl restart docker
(or pull from an internal mirror registry instead).
"""
import argparse, subprocess, sys, time, os
from concurrent.futures import ThreadPoolExecutor, as_completed

def _log(m): print(m, flush=True)

def resolve_verified(limit):
    from datasets import load_dataset
    from swebench.harness.test_spec.test_spec import make_test_spec
    ds = load_dataset("princeton-nlp/SWE-bench_Verified", split="test")
    if limit: ds = ds.select(range(min(limit, len(ds))))
    return {make_test_spec(inst, namespace="swebench").instance_image_key for inst in ds}

def resolve_pro(limit):
    from datasets import load_dataset
    ds = load_dataset("ScaleAI/SWE-bench_Pro", split="test")
    if limit: ds = ds.select(range(min(limit, len(ds))))
    imgs = set()
    for row in ds:
        tag = row.get("dockerhub_tag")
        if tag: imgs.add(f"jefzda/sweap-images:{tag}")
    return imgs

def resolve_swesmith(limit):
    # prefer local parquet (avoids re-download / a known load_dataset hang on some boxes)
    local = os.environ.get("SWESMITH_LOCAL_PARQUET")
    imgs = set()
    if local and os.path.isdir(local):
        import glob, pyarrow.parquet as pq
        for fp in sorted(glob.glob(os.path.join(local, "*.parquet"))):
            for n in pq.read_table(fp, columns=["image_name"]).column("image_name").to_pylist():
                if n: imgs.add(n)
            if limit and len(imgs) >= limit: break
    else:
        from datasets import load_dataset
        ds = load_dataset("SWE-bench/SWE-smith", split="train")
        if limit: ds = ds.select(range(min(limit, len(ds))))
        for row in ds:
            if row.get("image_name"): imgs.add(row["image_name"])
    return imgs

RESOLVERS = {"verified": resolve_verified, "pro": resolve_pro, "swesmith": resolve_swesmith}

def pull(image, retries=3):
    err = ""
    for i in range(retries):
        r = subprocess.run(["docker", "pull", image], capture_output=True, text=True)
        if r.returncode == 0:
            return image, True, ""
        err = (r.stderr.strip().splitlines() or ["pull failed"])[-1]
        time.sleep(3 * (i + 1))
    return image, False, err

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default="verified,pro,swesmith")
    ap.add_argument("--limit", type=int, default=0, help="max instances per source (0=all)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--dry-run", action="store_true", help="resolve + write manifest, don't pull")
    ap.add_argument("--manifest", default="env_images_manifest.txt")
    args = ap.parse_args()

    per = {}
    for src in [s.strip() for s in args.sources.split(",") if s.strip()]:
        if src not in RESOLVERS:
            _log(f"[skip] unknown source: {src} (known: {','.join(RESOLVERS)})"); continue
        _log(f"[resolve] {src} ...")
        imgs = RESOLVERS[src](args.limit or None)
        per[src] = imgs
        _log(f"[resolve] {src}: {len(imgs)} unique images")

    flat = sorted({i for v in per.values() for i in v})
    with open(args.manifest, "w") as f:
        f.write("\n".join(flat) + ("\n" if flat else ""))
    _log(f"[manifest] {len(flat)} unique images -> {args.manifest}")
    if args.dry_run:
        _log("[dry-run] resolved only, not pulling."); return

    ok = fail = 0; fails = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(pull, i): i for i in flat}
        for n, fut in enumerate(as_completed(futs), 1):
            img, good, err = fut.result()
            if good: ok += 1
            else: fail += 1; fails.append((img, err))
            if n % 20 == 0 or not good:
                extra = f" | FAIL {img}: {err}" if not good else ""
                _log(f"[pull] {n}/{len(flat)} ok={ok} fail={fail}{extra}")
    _log(f"[done] ok={ok} fail={fail}")
    if fails:
        with open("env_images_failures.txt", "w") as f:
            f.write("\n".join(f"{i}\t{e}" for i, e in fails) + "\n")
        _log(f"[done] {fail} failures -> env_images_failures.txt (rerun to retry)")
    subprocess.run(["docker", "system", "df"])

if __name__ == "__main__":
    main()

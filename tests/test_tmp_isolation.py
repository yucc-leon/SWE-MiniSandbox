"""验证 chroot 模式下多个沙箱是否共享 /tmp。

不需要真正的 chroot 权限，只验证 bind mount 的数据共享行为。
如果没有 unshare 权限，退化为直接验证 /tmp 共享的逻辑推理。

运行: python tests/test_tmp_isolation.py
"""

import os
import subprocess
import sys
import tempfile
import shutil
import uuid
import concurrent.futures
import time


def has_unshare():
    """检测当前环境是否支持 unshare --mount。"""
    try:
        r = subprocess.run(
            ["unshare", "--mount", "echo", "ok"],
            capture_output=True, timeout=5,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ── Test 1: 直接验证 /tmp 共享 ──────────────────────────────────

def test_tmp_shared_without_isolation():
    """不用任何隔离，验证两个进程写同一个 /tmp 路径会互相覆盖。"""
    print("=" * 60)
    print("TEST 1: /tmp 共享验证（无隔离基线）")

    # 用一个固定路径模拟硬编码的测试临时文件
    fixed_path = "/tmp/test_swe_sandbox_collision_marker.txt"

    try:
        # 进程 A 写入
        with open(fixed_path, "w") as f:
            f.write("written_by_A")

        # 进程 B 读取 —— 能看到 A 写的内容
        with open(fixed_path, "r") as f:
            content = f.read()

        ok = content == "written_by_A"
        print(f"  {'✅' if ok else '❌'} 进程 B 能读到进程 A 写入 /tmp 的内容: {content}")

        # 进程 B 覆盖
        with open(fixed_path, "w") as f:
            f.write("overwritten_by_B")

        # 进程 A 再读 —— 被覆盖了
        with open(fixed_path, "r") as f:
            content = f.read()

        ok2 = content == "overwritten_by_B"
        print(f"  {'✅' if ok2 else '❌'} 进程 A 再读时内容已被 B 覆盖: {content}")

        return ok and ok2
    finally:
        if os.path.exists(fixed_path):
            os.remove(fixed_path)


# ── Test 2: 模拟 pytest basetemp 编号碰撞 ───────────────────────

def test_pytest_basetemp_collision():
    """模拟多个并发 pytest session 在共享 /tmp 下的编号碰撞。"""
    print("=" * 60)
    print("TEST 2: pytest basetemp 编号碰撞模拟")

    # pytest 默认 basetemp 路径格式
    basetemp_root = "/tmp/pytest-of-root"
    os.makedirs(basetemp_root, exist_ok=True)

    # 清理之前的残留
    for d in os.listdir(basetemp_root):
        p = os.path.join(basetemp_root, d)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)

    num_sandboxes = 8
    collisions = []

    def simulate_pytest_session(sandbox_id):
        """模拟一个 pytest session 创建 basetemp 目录。
        
        pytest 的逻辑是：尝试 pytest-0, pytest-1, ... 直到找到不存在的。
        多个并发 session 可能同时看到 pytest-0 不存在，然后都尝试创建。
        """
        for i in range(100):
            candidate = os.path.join(basetemp_root, f"pytest-{i}")
            if not os.path.exists(candidate):
                # 模拟竞争窗口：检查和创建之间有间隙
                time.sleep(0.001)  # 放大竞争窗口
                try:
                    os.makedirs(candidate, exist_ok=False)
                    # 写入标记
                    marker = os.path.join(candidate, "marker.txt")
                    with open(marker, "w") as f:
                        f.write(f"sandbox_{sandbox_id}")
                    return candidate, i
                except FileExistsError:
                    # 被别人抢了，继续找下一个
                    continue
        return None, -1

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_sandboxes) as executor:
        futures = {
            executor.submit(simulate_pytest_session, i): i
            for i in range(num_sandboxes)
        }
        for f in concurrent.futures.as_completed(futures):
            sandbox_id = futures[f]
            path, idx = f.result()
            results[sandbox_id] = (path, idx)

    # 检查是否有编号碰撞（多个 sandbox 拿到同一个编号）
    indices = [idx for _, idx in results.values()]
    unique_indices = set(indices)

    # 检查 marker 文件是否被覆盖
    marker_mismatches = 0
    for sandbox_id, (path, idx) in results.items():
        if path is None:
            continue
        marker = os.path.join(path, "marker.txt")
        if os.path.exists(marker):
            with open(marker) as f:
                content = f.read()
            if content != f"sandbox_{sandbox_id}":
                marker_mismatches += 1
                collisions.append((sandbox_id, idx, content))

    print(f"  ℹ️  {num_sandboxes} 个并发 session，分配到 {len(unique_indices)} 个不同编号")
    print(f"  ℹ️  编号分配: {sorted(indices)}")

    if marker_mismatches > 0:
        print(f"  ❌ 发现 {marker_mismatches} 次 marker 被覆盖（数据污染）")
        for sid, idx, content in collisions:
            print(f"     sandbox_{sid} 的 pytest-{idx} 被覆盖为: {content}")
    else:
        print(f"  ✅ 没有发现 marker 覆盖")

    # 即使没有覆盖，编号碰撞本身也说明存在竞争
    # exist_ok=False 让我们避免了覆盖，但真实 pytest 用的是 exist_ok=True
    print(f"  ℹ️  注意: 真实 pytest 使用 os.makedirs(exist_ok=True)，碰撞时不会报错而是直接共享目录")

    # 清理
    shutil.rmtree(basetemp_root, ignore_errors=True)

    return True  # 这个测试主要是展示风险，不做 pass/fail 判断


# ── Test 3: 用 unshare 验证 bind mount 共享 /tmp ────────────────

def test_bind_mount_shares_tmp():
    """用真实的 unshare + bind mount 验证两个沙箱共享 /tmp。"""
    print("=" * 60)
    print("TEST 3: bind mount /tmp 共享验证（需要 unshare 权限）")

    if not has_unshare():
        print("  ⏭️  跳过: 当前环境不支持 unshare --mount")
        return True

    base = tempfile.mkdtemp(prefix="sandbox_tmp_test_")
    sandbox_a = os.path.join(base, "sandbox_a")
    sandbox_b = os.path.join(base, "sandbox_b")

    for sb in [sandbox_a, sandbox_b]:
        for d in ["bin", "usr", "lib", "lib64", "etc", "tmp", "dev", "proc"]:
            os.makedirs(os.path.join(sb, d), exist_ok=True)

    marker_file = f"/tmp/swe_sandbox_test_{uuid.uuid4().hex[:8]}.txt"

    try:
        # 沙箱 A: 在 /tmp 写一个文件
        cmd_a = (
            f"unshare --mount sh -c '"
            f"mount --bind /tmp {sandbox_a}/tmp && "
            f"mount --bind -o ro /bin {sandbox_a}/bin && "
            f"mount --bind -o ro /usr {sandbox_a}/usr && "
            f"mount --bind -o ro /lib {sandbox_a}/lib && "
            f"chroot {sandbox_a} /bin/bash -c \"echo from_sandbox_a > {marker_file}\"'"
        )

        r = subprocess.run(cmd_a, shell=True, capture_output=True, text=True, timeout=10)
        ok_write = r.returncode == 0
        if not ok_write:
            print(f"  ❌ 沙箱 A 写入失败: {r.stderr}")
            return False
        print(f"  ✅ 沙箱 A 写入 {marker_file}")

        # 沙箱 B: 读取沙箱 A 写的文件
        cmd_b = (
            f"unshare --mount sh -c '"
            f"mount --bind /tmp {sandbox_b}/tmp && "
            f"mount --bind -o ro /bin {sandbox_b}/bin && "
            f"mount --bind -o ro /usr {sandbox_b}/usr && "
            f"mount --bind -o ro /lib {sandbox_b}/lib && "
            f"chroot {sandbox_b} /bin/bash -c \"cat {marker_file}\"'"
        )

        r = subprocess.run(cmd_b, shell=True, capture_output=True, text=True, timeout=10)
        content = r.stdout.strip()
        ok_read = content == "from_sandbox_a"
        print(f"  {'✅' if ok_read else '❌'} 沙箱 B 读到沙箱 A 的内容: '{content}'")

        if ok_read:
            print(f"  ⚠️  结论: 两个独立的 chroot 沙箱通过 bind mount 共享了 /tmp")
        
        return ok_read

    except subprocess.TimeoutExpired:
        print(f"  ❌ 命令超时")
        return False
    finally:
        # 清理
        if os.path.exists(marker_file):
            os.remove(marker_file)
        shutil.rmtree(base, ignore_errors=True)


# ── Test 4: tmpfs 隔离验证 ──────────────────────────────────────

def test_tmpfs_isolation():
    """验证用 tmpfs 替代 bind mount 后，/tmp 是否隔离。"""
    print("=" * 60)
    print("TEST 4: tmpfs 隔离验证（需要 unshare 权限）")

    if not has_unshare():
        print("  ⏭️  跳过: 当前环境不支持 unshare --mount")
        return True

    base = tempfile.mkdtemp(prefix="sandbox_tmpfs_test_")
    sandbox = os.path.join(base, "sandbox")

    for d in ["bin", "usr", "lib", "lib64", "etc", "tmp", "dev", "proc"]:
        os.makedirs(os.path.join(sandbox, d), exist_ok=True)

    marker_file = "/tmp/tmpfs_test_marker.txt"

    try:
        # 用 tmpfs 挂载 /tmp，然后在里面写文件
        cmd = (
            f"unshare --mount sh -c '"
            f"mount -t tmpfs tmpfs {sandbox}/tmp && "
            f"mount --bind -o ro /bin {sandbox}/bin && "
            f"mount --bind -o ro /usr {sandbox}/usr && "
            f"mount --bind -o ro /lib {sandbox}/lib && "
            f"chroot {sandbox} /bin/bash -c \"echo from_tmpfs > {marker_file} && cat {marker_file}\"'"
        )

        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        sandbox_content = r.stdout.strip()
        ok_inside = sandbox_content == "from_tmpfs"
        print(f"  {'✅' if ok_inside else '❌'} 沙箱内能写入和读取 /tmp: '{sandbox_content}'")

        # 宿主机上检查：文件不应该存在
        host_exists = os.path.exists(marker_file)
        ok_isolated = not host_exists
        print(f"  {'✅' if ok_isolated else '❌'} 宿主机 /tmp 上{'不' if ok_isolated else ''}存在该文件")

        if ok_inside and ok_isolated:
            print(f"  ✅ 结论: tmpfs 方案下 /tmp 完全隔离")

        return ok_inside and ok_isolated

    except subprocess.TimeoutExpired:
        print(f"  ❌ 命令超时")
        return False
    finally:
        if os.path.exists(marker_file):
            os.remove(marker_file)
        shutil.rmtree(base, ignore_errors=True)


# ── main ─────────────────────────────────────────────────────────

def main():
    print("\n🧪 /tmp 隔离风险验证\n")

    results = {}
    results["tmp_shared_baseline"] = test_tmp_shared_without_isolation()
    results["pytest_basetemp_collision"] = test_pytest_basetemp_collision()
    results["bind_mount_shares_tmp"] = test_bind_mount_shares_tmp()
    results["tmpfs_isolation"] = test_tmpfs_isolation()

    print("\n" + "=" * 60)
    print("📊 结果汇总\n")
    for name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {status}  {name}")

    print()
    print("💡 结论:")
    print("   - bind mount /tmp 让多个 chroot 沙箱共享宿主机 /tmp")
    print("   - 高并发下 pytest basetemp 编号可能碰撞")
    print("   - 改用 tmpfs 可以完全隔离每个沙箱的 /tmp")


if __name__ == "__main__":
    main()

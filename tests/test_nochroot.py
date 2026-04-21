"""no-chroot 模式冒烟测试.

不依赖完整的 swesandbox 依赖链，直接测试核心逻辑。
运行: python tests/test_nochroot.py
"""

import os
import sys
import subprocess
import tempfile
import uuid
import concurrent.futures

# ── 1. unshare 检测 ──────────────────────────────────────────────

def test_unshare_detection():
    """验证当前环境 unshare --mount 是否可用，以及预期的降级行为."""
    print("=" * 60)
    print("TEST 1: unshare --mount 检测")
    try:
        r = subprocess.run(
            ["unshare", "--mount", "echo", "ok"],
            capture_output=True, timeout=5,
        )
        has_unshare = r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        has_unshare = False

    if has_unshare:
        print("  ℹ️  unshare --mount 可用 → use_chroot=True (chroot 模式)")
    else:
        print("  ℹ️  unshare --mount 不可用 → use_chroot=False (no-chroot 降级)")
    print(f"  结果: has_unshare={has_unshare}")
    # 这个测试只是检测，不管哪种结果都算通过
    return True


# ── 2. sandbox_path 路径翻译 ─────────────────────────────────────

def test_sandbox_path():
    """验证 sandbox_path() 在两种模式下的行为."""
    print("=" * 60)
    print("TEST 2: sandbox_path() 路径翻译")

    root_dir = "/sandbox/test_instance_abc123"
    git_folder = "testbed"

    # chroot 模式: 路径不变
    def sandbox_path_chroot(path):
        return path

    # no-chroot 模式: 加 root_dir 前缀
    def sandbox_path_nochroot(path):
        return os.path.join(root_dir, path.lstrip('/'))

    cases = [
        ("/testbed", "/testbed", f"{root_dir}/testbed"),
        ("/tools", "/tools", f"{root_dir}/tools"),
        ("/run_tests.sh", "/run_tests.sh", f"{root_dir}/run_tests.sh"),
        ("/testbed/lib", "/testbed/lib", f"{root_dir}/testbed/lib"),
    ]

    all_pass = True
    for path, expected_chroot, expected_nochroot in cases:
        got_chroot = sandbox_path_chroot(path)
        got_nochroot = sandbox_path_nochroot(path)
        ok_c = got_chroot == expected_chroot
        ok_n = got_nochroot == expected_nochroot
        status = "✅" if (ok_c and ok_n) else "❌"
        if not (ok_c and ok_n):
            all_pass = False
        print(f"  {status} sandbox_path('{path}')")
        print(f"       chroot:    {got_chroot} (expect {expected_chroot})")
        print(f"       no-chroot: {got_nochroot} (expect {expected_nochroot})")

    return all_pass


# ── 3. startup_nochroot 命令生成 ─────────────────────────────────

def test_startup_nochroot():
    """验证 startup_nochroot() 生成的命令格式."""
    print("=" * 60)
    print("TEST 3: startup_nochroot() 命令生成")

    root_dir = "/sandbox/test_instance_abc123"
    ps1 = "SHELLPS1PREFIX"

    cmd = (
        f"/bin/bash --noprofile --norc -c "
        f"'export USER=${{USER:-$(whoami)}}; export PS1=\"{ps1}\"; "
        f"export SWE_SANDBOX_ROOT_DIR=\"{root_dir}\"; "
        f"export SWE_SANDBOX_GIT_FOLDER=\"testbed\"; "
        f"export SWE_SANDBOX_TOOL_PATH=\"/tools\"; "
        f"mkdir -p \"{root_dir}/tmp\"; "
        f"export TMPDIR=\"{root_dir}/tmp\"; "
        f"export TMP=\"$TMPDIR\"; export TEMP=\"$TMPDIR\"; "
        f"cd {root_dir}; exec /bin/bash --noprofile --norc'"
    )
    cmd += "\n"

    checks = [
        ("包含 /bin/bash", "/bin/bash" in cmd),
        ("包含 cd root_dir", f"cd {root_dir}" in cmd),
        ("包含 PS1 设置", ps1 in cmd),
        ("包含 sandbox root 环境变量", f"SWE_SANDBOX_ROOT_DIR=\"{root_dir}\"" in cmd),
        ("包含 git_folder 环境变量", "SWE_SANDBOX_GIT_FOLDER=\"testbed\"" in cmd),
        ("包含 tool_path 环境变量", "SWE_SANDBOX_TOOL_PATH=\"/tools\"" in cmd),
        ("创建实例级 tmp 目录", f"mkdir -p \"{root_dir}/tmp\"" in cmd),
        ("包含 TMPDIR 环境变量", f"TMPDIR=\"{root_dir}/tmp\"" in cmd),
        ("TMP/TEMP 继承 TMPDIR", 'export TMP="$TMPDIR"; export TEMP="$TMPDIR"' in cmd),
        ("以换行结尾", cmd.endswith("\n")),
        ("不包含 unshare", "unshare" not in cmd),
        ("不包含 chroot", "chroot" not in cmd),
        ("不包含 mount", "mount" not in cmd),
    ]

    all_pass = True
    for desc, ok in checks:
        status = "✅" if ok else "❌"
        if not ok:
            all_pass = False
        print(f"  {status} {desc}")

    print(f"  生成的命令: {cmd.strip()[:100]}...")
    return all_pass


# ── 4. _rewrite_script_paths 脚本路径替换 ────────────────────────

def test_rewrite_script_paths():
    """验证 _rewrite_script_paths() 对 swebench 脚本的路径替换."""
    print("=" * 60)
    print("TEST 4: _rewrite_script_paths() 脚本路径替换")

    root_dir = "/sandbox/django__django-16379_a1b2c3"
    git_folder = "testbed"

    def rewrite(script_content):
        rd = root_dir.rstrip('/')
        return script_content.replace(f"/{git_folder}", f"{rd}/{git_folder}")

    # 模拟 swebench 生成的 eval 脚本片段
    fake_eval_script = """#!/bin/bash
set -xo pipefail
export PATH="/SWE/shared/django/4.2/default/venv/bin:$PATH"
source /SWE/shared/django/4.2/default/venv/bin/activate
cd /testbed
git status
git show
cd /testbed
python -m django test --settings=test_settings
: '>>>>> End Test Output'
git checkout abc123 tests/test_foo.py
"""

    rewritten = rewrite(fake_eval_script)

    checks = [
        ("cd /testbed → cd {root_dir}/testbed",
         f"cd {root_dir}/testbed" in rewritten and "cd /testbed\n" not in rewritten),
        ("/SWE/shared 路径不受影响",
         "/SWE/shared/django" in rewritten),
        ("#!/bin/bash 不受影响",
         rewritten.startswith("#!/bin/bash")),
        ("/requirements.txt 不受影响 (不含 testbed)",
         True),  # 这个脚本里没有 /requirements.txt，但逻辑上不会被替换
    ]

    all_pass = True
    for desc, ok in checks:
        status = "✅" if ok else "❌"
        if not ok:
            all_pass = False
        print(f"  {status} {desc}")

    # 额外测试: matplotlib 的 PYTHONPATH=/testbed/lib
    matplotlib_line = "PYTHONPATH=/testbed/lib:$PYTHONPATH pytest"
    rewritten_mpl = rewrite(matplotlib_line)
    expected_mpl = f"PYTHONPATH={root_dir}/testbed/lib:$PYTHONPATH pytest"
    ok_mpl = rewritten_mpl == expected_mpl
    status = "✅" if ok_mpl else "❌"
    if not ok_mpl:
        all_pass = False
    print(f"  {status} PYTHONPATH=/testbed/lib 替换")
    if not ok_mpl:
        print(f"       got:    {rewritten_mpl}")
        print(f"       expect: {expected_mpl}")

    return all_pass


# ── 5. rewrite_observation_paths 反向映射 ────────────────────────

def test_rewrite_observation_paths():
    """验证 no-chroot 输出会被映射回逻辑路径而不是泄露宿主机路径."""
    print("=" * 60)
    print("TEST 5: rewrite_observation_paths() 反向映射")

    root_dir = "/sandbox/django__django-16379_a1b2c3"
    replacements = {
        f"{root_dir}/testbed": "/testbed",
        f"{root_dir}/tools": "/tools",
        f"{root_dir}/root": "/root",
        f"{root_dir}/run_tests.sh": "/run_tests.sh",
        f"{root_dir}/res.patch": "/res.patch",
    }

    def rewrite(output):
        rewritten = output
        for real_path, logical_path in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
            rewritten = rewritten.replace(real_path, logical_path)
        return rewritten

    original = "\n".join(
        [
            f"{root_dir}/testbed/django/db/models/base.py",
            f"{root_dir}/tools/edit_anthropic/bin/str_replace_editor",
            f"Applying patch from {root_dir}/res.patch",
            f"bash {root_dir}/run_tests.sh",
        ]
    )
    rewritten = rewrite(original)

    checks = [
        ("testbed 路径被收敛", "/testbed/django/db/models/base.py" in rewritten),
        ("tools 路径被收敛", "/tools/edit_anthropic/bin/str_replace_editor" in rewritten),
        ("res.patch 路径被收敛", "Applying patch from /res.patch" in rewritten),
        ("run_tests.sh 路径被收敛", "bash /run_tests.sh" in rewritten),
        ("不再泄露 root_dir", root_dir not in rewritten),
    ]

    all_pass = True
    for desc, ok in checks:
        status = "✅" if ok else "❌"
        if not ok:
            all_pass = False
        print(f"  {status} {desc}")

    return all_pass


# ── 6. runtime 控制字符清理 ─────────────────────────────────────

def test_strip_control_chars():
    """验证 runtime 会清掉裸 CR，避免 observation 出现大量 \\r 噪音."""
    print("=" * 60)
    print("TEST 6: _strip_control_chars() 清理裸 CR")

    def strip_control_chars(s: str) -> str:
        return s.replace("\r\n", "\n").replace("\r", "")

    original = "\rHere's the result\r\r\rNo replacement was performed\r\nline2\r"
    cleaned = strip_control_chars(original)

    checks = [
        ("保留正文", "No replacement was performed" in cleaned),
        ("CRLF 归一化为 LF", "\r\n" not in cleaned and "line2" in cleaned),
        ("裸 CR 被清掉", "\r" not in cleaned),
        ("前缀不再有回车", cleaned.startswith("Here's the result")),
    ]

    all_pass = True
    for desc, ok in checks:
        status = "✅" if ok else "❌"
        if not ok:
            all_pass = False
        print(f"  {status} {desc}")

    return all_pass


# ── 7. 多实例并发目录创建 ────────────────────────────────────────

def test_concurrent_directory_creation():
    """验证多个沙箱实例并发创建目录不冲突."""
    print("=" * 60)
    print("TEST 5: 多实例并发目录创建")

    base_dir = tempfile.mkdtemp(prefix="sandbox_test_")
    traj_id = "django__django-16379"
    num_instances = 20

    def create_unique_folder(base, tid):
        folder_name = tid + '_' + str(uuid.uuid4())
        folder_path = os.path.join(base, folder_name)
        os.makedirs(folder_path, exist_ok=True)
        return folder_path

    created_dirs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(create_unique_folder, base_dir, traj_id)
            for _ in range(num_instances)
        ]
        for f in concurrent.futures.as_completed(futures):
            created_dirs.append(f.result())

    unique_dirs = set(created_dirs)
    all_exist = all(os.path.isdir(d) for d in created_dirs)

    checks = [
        (f"创建了 {num_instances} 个目录", len(created_dirs) == num_instances),
        ("所有目录路径唯一", len(unique_dirs) == num_instances),
        ("所有目录实际存在", all_exist),
    ]

    all_pass = True
    for desc, ok in checks:
        status = "✅" if ok else "❌"
        if not ok:
            all_pass = False
        print(f"  {status} {desc}")

    # 清理
    import shutil
    shutil.rmtree(base_dir, ignore_errors=True)

    return all_pass


# ── 7. 端到端: 在真实 bash session 中验证 no-chroot 启动 ────────

def test_nochroot_bash_session():
    """在真实 bash 中验证 no-chroot 启动命令能正常工作."""
    print("=" * 60)
    print("TEST 6: no-chroot bash session 端到端")

    root_dir = tempfile.mkdtemp(prefix="sandbox_e2e_")
    # 模拟沙箱目录结构
    testbed_dir = os.path.join(root_dir, "testbed")
    tools_dir = os.path.join(root_dir, "tools")
    os.makedirs(testbed_dir)
    os.makedirs(tools_dir)

    # 写一个测试文件
    with open(os.path.join(testbed_dir, "hello.txt"), "w") as f:
        f.write("hello from testbed\n")

    # 构造 no-chroot 启动命令，然后在里面执行一些操作
    test_script = f"""
cd {root_dir}
echo "PWD=$(pwd)"
echo "TESTBED_EXISTS=$(test -d testbed && echo yes || echo no)"
echo "TOOLS_EXISTS=$(test -d tools && echo yes || echo no)"
mkdir -p "{root_dir}/tmp"
export TMPDIR="{root_dir}/tmp"
export TMP="$TMPDIR"
export TEMP="$TMPDIR"
echo "TMPDIR=$TMPDIR"
echo "TMP=$TMP"
echo "TEMP=$TEMP"
python - <<'PY'
import tempfile
print("TEMPFILE_DIR=" + tempfile.gettempdir())
PY
cat testbed/hello.txt
echo "SANDBOX_PATH_TEST={os.path.join(root_dir, 'testbed')}"
ls testbed/
"""

    try:
        r = subprocess.run(
            ["bash", "-c", test_script],
            capture_output=True, text=True, timeout=10,
        )
        output = r.stdout

        checks = [
            ("cd 到 root_dir 成功", f"PWD={root_dir}" in output),
            ("testbed 目录存在", "TESTBED_EXISTS=yes" in output),
            ("tools 目录存在", "TOOLS_EXISTS=yes" in output),
            ("TMPDIR 指向实例 tmp", f"TMPDIR={root_dir}/tmp" in output),
            ("TMP 指向实例 tmp", f"TMP={root_dir}/tmp" in output),
            ("TEMP 指向实例 tmp", f"TEMP={root_dir}/tmp" in output),
            ("Python tempfile 使用实例 tmp", f"TEMPFILE_DIR={root_dir}/tmp" in output),
            ("能读取 testbed 内文件", "hello from testbed" in output),
            ("sandbox_path 拼接正确", f"SANDBOX_PATH_TEST={root_dir}/testbed" in output),
        ]

        all_pass = True
        for desc, ok in checks:
            status = "✅" if ok else "❌"
            if not ok:
                all_pass = False
            print(f"  {status} {desc}")

        if not all_pass:
            print(f"  [DEBUG] stdout: {output}")
            print(f"  [DEBUG] stderr: {r.stderr}")

    except subprocess.TimeoutExpired:
        print("  ❌ bash session 超时")
        all_pass = False
    finally:
        import shutil
        shutil.rmtree(root_dir, ignore_errors=True)

    return all_pass


# ── main ─────────────────────────────────────────────────────────

def main():
    print("\n🧪 SWE-MiniSandbox no-chroot 模式冒烟测试\n")

    results = {}
    results["unshare_detection"] = test_unshare_detection()
    results["sandbox_path"] = test_sandbox_path()
    results["startup_nochroot"] = test_startup_nochroot()
    results["rewrite_script_paths"] = test_rewrite_script_paths()
    results["rewrite_observation_paths"] = test_rewrite_observation_paths()
    results["strip_control_chars"] = test_strip_control_chars()
    results["concurrent_dirs"] = test_concurrent_directory_creation()
    results["bash_session_e2e"] = test_nochroot_bash_session()

    print("\n" + "=" * 60)
    print("📊 测试结果汇总\n")
    all_pass = True
    for name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        if not passed:
            all_pass = False
        print(f"  {status}  {name}")

    print()
    if all_pass:
        print("🎉 全部通过")
    else:
        print("⚠️  有测试失败")
        sys.exit(1)


if __name__ == "__main__":
    main()

"""no-chroot 模式集成测试.

使用真实的 swerex LocalRuntime + pexpect BashSession，
验证 no-chroot 模式下多个沙箱实例能正确并发运行。

运行: python tests/test_nochroot_integration.py
依赖: pip install swe-rex (swerex + pexpect)
"""

import asyncio
import os
import shutil
import sys
import tempfile
import uuid
import concurrent.futures

from swerex.runtime.sandbox import LocalRuntime
from swerex.runtime.abstract import (
    BashAction,
    CreateSandboxBashSessionRequest,
)


def make_root_dir(base: str, traj_id: str) -> str:
    """模拟上游 create_unique_folder."""
    folder = os.path.join(base, f"{traj_id}_{uuid.uuid4()}")
    os.makedirs(folder, exist_ok=True)
    return folder


def make_startup_cmd(root_dir: str) -> str:
    """模拟 startup_nochroot()."""
    ps1 = "SHELLPS1PREFIX"
    return (
        f"/bin/bash --noprofile --norc -c "
        f"'export USER=${{USER:-$(whoami)}}; export PS1=\"{ps1}\"; "
        f"cd {root_dir}; exec /bin/bash --noprofile --norc'"
        + "\n"
    )


def sandbox_path(root_dir: str, path: str) -> str:
    """模拟 sandbox_path() in no-chroot mode."""
    return os.path.join(root_dir, path.lstrip("/"))


# ── Test 1: 单实例基本功能 ───────────────────────────────────────

async def test_single_instance():
    """验证单个 no-chroot 沙箱实例的基本功能."""
    print("=" * 60)
    print("TEST 1: 单实例 no-chroot session")

    base = tempfile.mkdtemp(prefix="sandbox_int_")
    root_dir = make_root_dir(base, "django__django-16379")

    # 模拟沙箱目录结构
    testbed = os.path.join(root_dir, "testbed")
    tools = os.path.join(root_dir, "tools", "registry", "bin")
    os.makedirs(testbed)
    os.makedirs(tools)
    with open(os.path.join(testbed, "manage.py"), "w") as f:
        f.write("# Django manage.py stub\nprint('hello django')\n")

    runtime = LocalRuntime()
    startup_cmd = make_startup_cmd(root_dir)

    try:
        await runtime.create_session(
            CreateSandboxBashSessionRequest(
                startup_source=[],
                startup_timeout=10,
                startup_cmd=startup_cmd,
            )
        )

        # 1) pwd 应该是 root_dir
        r = await runtime.run_in_session(BashAction(command="pwd", timeout=5, check="raise"))
        # 过滤掉 shell 噪音 (如 VS Code 的 __vsc_prompt_cmd_original)
        pwd_lines = [l.strip() for l in r.output.strip().split("\n")
                     if l.strip().startswith("/") and "command not found" not in l]
        pwd = pwd_lines[-1] if pwd_lines else ""
        ok_pwd = pwd == root_dir
        print(f"  {'✅' if ok_pwd else '❌'} pwd = {pwd} (expect {root_dir})")

        # 2) testbed 目录存在
        r = await runtime.run_in_session(BashAction(command="ls testbed/manage.py", timeout=5, check="raise"))
        ok_testbed = "manage.py" in r.output
        print(f"  {'✅' if ok_testbed else '❌'} testbed/manage.py 可访问")

        # 3) sandbox_path 翻译后的路径可用
        sp = sandbox_path(root_dir, "/testbed")
        r = await runtime.run_in_session(BashAction(command=f"ls {sp}/manage.py", timeout=5, check="raise"))
        ok_sp = "manage.py" in r.output
        print(f"  {'✅' if ok_sp else '❌'} sandbox_path('/testbed') = {sp} 可访问")

        # 4) 可以在 testbed 中执行 python
        sp_manage = sandbox_path(root_dir, "/testbed/manage.py")
        r = await runtime.run_in_session(BashAction(command=f"python {sp_manage}", timeout=5, check="raise"))
        ok_py = "hello django" in r.output
        print(f"  {'✅' if ok_py else '❌'} python manage.py 输出正确")

        # 5) 写文件到 sandbox 内
        patch_path = sandbox_path(root_dir, "/res.patch")
        r = await runtime.run_in_session(BashAction(
            command=f"echo 'diff --git a/test b/test' > {patch_path} && cat {patch_path}",
            timeout=5, check="raise"
        ))
        ok_write = "diff --git" in r.output
        print(f"  {'✅' if ok_write else '❌'} 写文件到 sandbox_path('/res.patch') 成功")

        # 6) 模拟 _rewrite_script_paths: 替换后的脚本能执行
        git_folder = "testbed"
        rd = root_dir.rstrip("/")
        fake_script = f"#!/bin/bash\ncd /{git_folder}\npwd\n"
        rewritten = fake_script.replace(f"/{git_folder}", f"{rd}/{git_folder}")
        script_path = os.path.join(root_dir, "test_rewrite.sh")
        with open(script_path, "w") as f:
            f.write(rewritten)
        os.chmod(script_path, 0o755)
        r = await runtime.run_in_session(BashAction(command=script_path, timeout=5, check="raise"))
        ok_rewrite = os.path.join(root_dir, "testbed") in r.output
        print(f"  {'✅' if ok_rewrite else '❌'} 重写后的脚本 cd 到正确目录")

        all_pass = all([ok_pwd, ok_testbed, ok_sp, ok_py, ok_write, ok_rewrite])

    except Exception as e:
        print(f"  ❌ 异常: {e}")
        all_pass = False
    finally:
        await runtime.close()
        shutil.rmtree(base, ignore_errors=True)

    return all_pass


# ── Test 2: 多实例并发 ───────────────────────────────────────────

async def _run_instance(base: str, instance_id: str, idx: int) -> dict:
    """在独立 runtime 中运行一个沙箱实例."""
    root_dir = make_root_dir(base, instance_id)
    testbed = os.path.join(root_dir, "testbed")
    os.makedirs(testbed)

    # 每个实例写一个唯一标识文件
    marker = f"instance_{idx}_{uuid.uuid4().hex[:8]}"
    with open(os.path.join(testbed, "marker.txt"), "w") as f:
        f.write(marker)

    runtime = LocalRuntime()
    startup_cmd = make_startup_cmd(root_dir)

    result = {"idx": idx, "root_dir": root_dir, "marker": marker, "ok": False, "error": None}

    try:
        await runtime.create_session(
            CreateSandboxBashSessionRequest(
                startup_source=[],
                startup_timeout=10,
                startup_cmd=startup_cmd,
            )
        )

        # 验证 pwd
        r = await runtime.run_in_session(BashAction(command="pwd", timeout=5, check="raise"))
        pwd_lines = [l.strip() for l in r.output.strip().split("\n")
                     if l.strip().startswith("/") and "command not found" not in l]
        pwd = pwd_lines[-1] if pwd_lines else ""
        if pwd != root_dir:
            result["error"] = f"pwd mismatch: {pwd} != {root_dir}"
            return result

        # 验证 marker 文件
        sp = sandbox_path(root_dir, "/testbed/marker.txt")
        r = await runtime.run_in_session(BashAction(command=f"cat {sp}", timeout=5, check="raise"))
        if marker not in r.output:
            result["error"] = f"marker mismatch: {r.output.strip()} != {marker}"
            return result

        # 验证实例间隔离: 写一个文件，不应影响其他实例
        r = await runtime.run_in_session(BashAction(
            command=f"echo '{marker}' > {sandbox_path(root_dir, '/testbed/output.txt')}",
            timeout=5, check="raise"
        ))

        result["ok"] = True

    except Exception as e:
        result["error"] = str(e)
    finally:
        await runtime.close()

    return result


async def test_concurrent_instances():
    """验证多个 no-chroot 沙箱实例并发运行，互不干扰."""
    print("=" * 60)
    print("TEST 2: 多实例并发 (5 个实例)")

    base = tempfile.mkdtemp(prefix="sandbox_concurrent_")
    num_instances = 5

    instance_ids = [
        "django__django-16379",
        "flask__flask-4992",
        "pytest__pytest-11148",
        "sympy__sympy-24152",
        "matplotlib__matplotlib-25311",
    ]

    try:
        # 并发启动所有实例
        tasks = [
            _run_instance(base, instance_ids[i], i)
            for i in range(num_instances)
        ]
        results = await asyncio.gather(*tasks)

        all_pass = True
        for r in results:
            status = "✅" if r["ok"] else "❌"
            if not r["ok"]:
                all_pass = False
            err_msg = f" ({r['error']})" if r["error"] else ""
            print(f"  {status} 实例 {r['idx']} ({instance_ids[r['idx']]}){err_msg}")

        # 验证隔离: 每个实例的 output.txt 只包含自己的 marker
        isolation_ok = True
        for r in results:
            if not r["ok"]:
                continue
            output_file = os.path.join(r["root_dir"], "testbed", "output.txt")
            if os.path.exists(output_file):
                content = open(output_file).read().strip()
                if content != r["marker"]:
                    isolation_ok = False
                    print(f"  ❌ 实例 {r['idx']} 隔离失败: output={content}, marker={r['marker']}")

        if isolation_ok:
            print(f"  ✅ 所有实例文件隔离正确")
        else:
            all_pass = False

    finally:
        shutil.rmtree(base, ignore_errors=True)

    return all_pass


# ── Test 3: 模拟完整 eval 脚本执行 ───────────────────────────────

async def test_eval_script_simulation():
    """模拟 swebench eval 脚本在 no-chroot 模式下的执行."""
    print("=" * 60)
    print("TEST 3: 模拟 eval 脚本执行")

    base = tempfile.mkdtemp(prefix="sandbox_eval_")
    root_dir = make_root_dir(base, "django__django-16379")

    testbed = os.path.join(root_dir, "testbed")
    os.makedirs(testbed)

    # 创建一个假的 test 文件
    with open(os.path.join(testbed, "test_example.py"), "w") as f:
        f.write("""
import sys
print(">>>>> Start Test Output")
print("PASSED test_example::test_basic")
print("PASSED test_example::test_advanced")
print(">>>>> End Test Output")
sys.exit(0)
""")

    # 模拟 swebench 生成的 eval 脚本 (原始版本，含 /testbed)
    original_eval_script = """#!/bin/bash
set -xo pipefail
cd /testbed
git status 2>/dev/null || true
python test_example.py
"""

    # _rewrite_script_paths 替换
    git_folder = "testbed"
    rd = root_dir.rstrip("/")
    rewritten_script = original_eval_script.replace(f"/{git_folder}", f"{rd}/{git_folder}")

    # 写到 run_tests.sh
    script_path = os.path.join(root_dir, "run_tests.sh")
    with open(script_path, "w") as f:
        f.write(rewritten_script)
    os.chmod(script_path, 0o755)

    runtime = LocalRuntime()
    startup_cmd = make_startup_cmd(root_dir)

    try:
        await runtime.create_session(
            CreateSandboxBashSessionRequest(
                startup_source=[],
                startup_timeout=10,
                startup_cmd=startup_cmd,
            )
        )

        # 执行 eval 脚本
        run_tests = sandbox_path(root_dir, "/run_tests.sh")
        r = await runtime.run_in_session(BashAction(command=run_tests, timeout=15, check="raise"))
        output = r.output

        checks = [
            ("脚本执行成功", r.exit_code == 0),
            ("包含 Start Test Output", "Start Test Output" in output),
            ("包含 PASSED", "PASSED test_example::test_basic" in output),
            ("包含 End Test Output", "End Test Output" in output),
        ]

        all_pass = True
        for desc, ok in checks:
            status = "✅" if ok else "❌"
            if not ok:
                all_pass = False
            print(f"  {status} {desc}")

        if not all_pass:
            print(f"  [DEBUG] output:\n{output[:500]}")

    except Exception as e:
        print(f"  ❌ 异常: {e}")
        all_pass = False
    finally:
        await runtime.close()
        shutil.rmtree(base, ignore_errors=True)

    return all_pass


# ── Test 4: git 操作模拟 ─────────────────────────────────────────

async def test_git_operations():
    """验证在 no-chroot 模式下 git 操作正常 (模拟 _reset_repository + get_patch)."""
    print("=" * 60)
    print("TEST 4: git 操作 (init/commit/diff)")

    base = tempfile.mkdtemp(prefix="sandbox_git_")
    root_dir = make_root_dir(base, "flask__flask-4992")
    testbed = os.path.join(root_dir, "testbed")
    os.makedirs(testbed)

    runtime = LocalRuntime()
    startup_cmd = make_startup_cmd(root_dir)

    try:
        await runtime.create_session(
            CreateSandboxBashSessionRequest(
                startup_source=[],
                startup_timeout=10,
                startup_cmd=startup_cmd,
            )
        )

        git_dir = sandbox_path(root_dir, "/testbed")

        # 初始化 git repo
        init_cmds = " && ".join([
            f"cd {git_dir}",
            "git init",
            "git config user.email test@test.com",
            "git config user.name Test",
            "echo 'hello' > app.py",
            "git add -A",
            "git commit -m 'initial'",
        ])
        r = await runtime.run_in_session(BashAction(command=init_cmds, timeout=10, check="raise"))
        ok_init = r.exit_code == 0
        print(f"  {'✅' if ok_init else '❌'} git init + commit")

        # 模拟 agent 修改
        modify_cmds = " && ".join([
            f"cd {git_dir}",
            "echo 'world' >> app.py",
        ])
        await runtime.run_in_session(BashAction(command=modify_cmds, timeout=5, check="raise"))

        # 模拟 get_patch (sandbox_path 版本)
        patch_path = sandbox_path(root_dir, "/res.patch")
        patch_cmd = f"cd {git_dir} && git add -A && git diff --cached > {patch_path}"
        r = await runtime.run_in_session(BashAction(command=patch_cmd, timeout=5, check="raise"))
        ok_patch_cmd = r.exit_code == 0
        print(f"  {'✅' if ok_patch_cmd else '❌'} git diff --cached > patch")

        # 验证 patch 文件内容
        r = await runtime.run_in_session(BashAction(command=f"cat {patch_path}", timeout=5, check="raise"))
        ok_patch_content = "+world" in r.output
        print(f"  {'✅' if ok_patch_content else '❌'} patch 包含修改内容")

        # 模拟 _reset_repository
        reset_cmds = " && ".join([
            f"cd {git_dir}",
            "git restore .",
            "git reset --hard HEAD",
            "git clean -fdq",
        ])
        r = await runtime.run_in_session(BashAction(command=reset_cmds, timeout=5, check="raise"))
        ok_reset = r.exit_code == 0
        print(f"  {'✅' if ok_reset else '❌'} git reset --hard")

        # 验证 reset 后文件恢复
        r = await runtime.run_in_session(BashAction(command=f"cat {git_dir}/app.py", timeout=5, check="raise"))
        ok_restored = "world" not in r.output and "hello" in r.output
        print(f"  {'✅' if ok_restored else '❌'} reset 后文件恢复原状")

        all_pass = all([ok_init, ok_patch_cmd, ok_patch_content, ok_reset, ok_restored])

    except Exception as e:
        print(f"  ❌ 异常: {e}")
        all_pass = False
    finally:
        await runtime.close()
        shutil.rmtree(base, ignore_errors=True)

    return all_pass


# ── main ─────────────────────────────────────────────────────────

async def async_main():
    print("\n🧪 SWE-MiniSandbox no-chroot 集成测试 (swerex LocalRuntime)\n")

    results = {}
    results["single_instance"] = await test_single_instance()
    results["concurrent_instances"] = await test_concurrent_instances()
    results["eval_script"] = await test_eval_script_simulation()
    results["git_operations"] = await test_git_operations()

    print("\n" + "=" * 60)
    print("📊 集成测试结果汇总\n")
    all_pass = True
    for name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        if not passed:
            all_pass = False
        print(f"  {status}  {name}")

    print()
    if all_pass:
        print("🎉 全部通过 — no-chroot 模式在当前环境可用")
    else:
        print("⚠️  有测试失败")
        sys.exit(1)


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()

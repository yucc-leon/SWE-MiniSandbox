# SWE-bench Verified envdump 接入方案(路径已摸清)

> 目标:用 x86 导出的 500 个 Verified 冻结环境(`envdump_verified.tar.gz`),在 aarch64 无容器下忠实重建评测环境,让 eval 这一环落地。本文记录现有路径、已做的代码改动、以及激活它还差的一步。

## 1. 现有重建路径(data_type=swebench)——已读通
- 实例加载后构造 `test_spec`(`sandbox_deployment.py:722` → `get_test_specs_from_ds`)。
- `install_env()` 的 swebench 分支(`:936`):
  `install_script_content = test_spec.setup_env_script + test_spec.install_repo_script`
  - `setup_env_script`:SWE-bench 自带的 **per-repo/per-version 版本 pin 配方**(conda create + pip install,来自 `MAP_REPO_VERSION_TO_SPECS`)——这也是 Verified 本来 docker-free 就能跑的原因。
  - `install_repo_script`:clone + checkout + `pip install -e .`。
- no-chroot 下会 strip 系统包命令(`:957`);pre_check 走 `setup_env_swebench`(`:1157`) 跑 eval_script。
- **结论**:Verified 是 `conda_testbed`,完全走这条现成路径,不用新写重建逻辑。成本低。

## 2. 已做的代码改动(可逆、默认关)
在 swebench 分支追加了 `SWEBENCH_ENVDUMP` opt-in(`sandbox_deployment.py`,与 swesmith 的 `SWESMITH_ENVDUMP` 对称):
- 开启后,在 harness 自带 pin 的 setup+install 之后,再 `pip install <Verified dump 的冻结 pin>`,消除 pip 重解析漂移,做到与 x86 镜像**逐包一致**。
- 键 = `test_spec.instance_id`;映射经 `SWEBENCH_ENVDUMP_MAP`(JSON `{instance_id: pip_freeze_path}`)。
- 追加在脚本末尾(testbed env 已激活);`|| true` 容错;默认关,不影响其他消费者。
- 语法已校验。

## 3. 激活还差的一步(等 tarball 落地)
1. 拉 `envdump_verified.tar.gz` 到 `vendor/envdump_verified/`(源在 docker 机器)。
2. 确认 dump 目录命名 → `instance_id` 的对应关系(Verified 镜像名形如 `swebench/sweb.eval.x86_64.<instance_id_normalized>`;需核对归一化规则,一般是 `__`→`_1776_` 之类或直接 instance_id)。
3. 建映射:
   ```python
   # 遍历 vendor/envdump_verified/<dir>/pip_freeze.txt, 反解 instance_id 做键
   json.dump({iid: freeze_path, ...}, open("vendor/envdump_verified_map.json","w"))
   ```
4. 跑一小批(如 20 个)验证:`SWEBENCH_ENVDUMP=1 SWEBENCH_ENVDUMP_MAP=...` + `data_type=swebench` + pre_check,看 gold-patch→resolved 是否忠实(评测口径,不是 swesmith 的 flip)。

## 4. 验证口径(与 swesmith 实验的区别)
- swesmith 侧问的是"环境构建正确率的翻转"(gold→0→gold→1)。
- Verified 侧问的是"**500 个评测环境能否在 aarch64 忠实重建**":gold patch 应用后判定 resolved 的比例,应逼近 x86 官方的 ~100%。差多少 = aarch64 无容器 eval 的忠实度缺口。
- 这直接补上我们之前 eval 环节的短板(曾遇 vLLM 加载问题;环境侧忠实重建是另一半)。

## 5. 优先级(承接主线)
- **前置闸门**:先等 swesmith A/B 抽样出数,确认 envdump 方法在真实 pipeline 有效。
- 方法证实后,本接入即可激活(第 3 步,低成本)。
- 价值:让 aarch64 **无容器忠实评测** SWE-bench Verified 成为现实——评测是 pipeline 的真实一环,也是"容器无关重建"通用配方跨到"人工精选型"数据集家族的一个实证点。

## 相关
- swesmith 侧对称实现:`SWESMITH_ENVDUMP` + `vendor/envdump_freeze_map.json` + `sh/tp_envdump_ab.yaml`
- 系统包审计(补 pip 之外的卡点):`docs/x86-manual-apt-audit-task.md`

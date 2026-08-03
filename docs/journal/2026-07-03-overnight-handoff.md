# Overnight handoff — 2026-07-03 夜

## 目标(一句话)
验证 envdump + tzdata/apt 修复能把 aarch64 无容器重建的忠实率从裸装 ~27% 拉到 ~80%,并推进 blog 草稿。

## Live-state 快照(resume 从这里)
- 分支:`feat/chroot-delivery`(不 push)。
- 已完成:A/B tp **8119 成功**(裸装 27.5% → envdump 67.5%,+16 翻转,0 回退)。13 失败已归因(见下)。
- 代码改动(工作树,已语法+单测校验):`sandboxdev/swesandbox/sandbox_deployment.py` 新增 4 个 opt-in——`SWESMITH_ENVDUMP`/`SWEBENCH_ENVDUMP`(pip 冻结 pin)、`SWESMITH_SYSDEPS`(apt-replay)、`SWESMITH_TZFIX`(tzdata)。
- 数据/映射(vendor/):`envdump_freeze_map.json`(222)、`envdump_verified_map.json`(500)、`envdump_system_deps_map.json`(5 repo apt)、`envdump_exp_sample_100.txt`(重跑样本,含原40)。
- Verified 接入件齐(500/500),等 A/B 方法确认后激活。

## 今晚决定(用户已确认)
1. 范围 = 修复 + 重跑验证 ~80% + 推进 blog 草稿(**不发布**)。OUT:发布 blog、拉 SWE-bench Pro、全量重筛、Verified aarch64 eval(留白天)。
2. 重跑样本 = **100**(`envdump_exp_sample_100.txt`)。
3. 提交 = **只提交我的 envdump 相关文件,不 push**;不碰用户的 mini-swe-agent/rl 等 WIP。
4. apt-replay = **全试(含 hydra 的 openjdk),失败 skip-and-continue**。

## 默认(未问,自取)
- tzfix 用 pip `tzdata` + `TZ=UTC`(可能救 nikola/django-money 的 TZif;schedule 的 `time.tzset` 缺失是 python-build 级、tzdata 未必能救,如仍挂=如实记录)。
- 重跑 ARM A=纯裸装(对照),ARM B=envdump+sysdeps+tzfix(完整方案)。delta=完整方案净升。
- dask 根因(site-packages 另有 released dask 与 /testbed editable 冲突)=纯读定位,不占机器。

## Work-list(done-condition)
| 任务 | 占机器 | done-condition | fallback |
|---|---|---|---|
| T1 代码修复(sysdeps+tzfix) | 否 | 语法+单测过 ✅ 已完成 | — |
| T2 重跑 A/B2(tp) | 是(1 job) | `vendor/swesmith-cache/envdump_ab2_summary.txt` 出数,envdump+fixes ≥~75% | job 挂/机器不来→留 yaml 就绪,晨报注明"submit sh/tp_envdump_ab2.yaml" |
| T3 blog 草稿推进(§1-3,§5) | 否 | 草稿各节写完整、数字核对 | — |
| T4 dask 根因定位 | 否 | 记下是哪个 pin 拉入 dask / 或 base 自带 | 查不动→记为待跟进 |

## 全局 stop-conditions / 上限
- tp 重跑**最多 2 次**(一次主、一次失败重试);到 ~80% 或 2 次用完即停,不追尾巴(剩下是任务质量 F2P,gate 该排除)。
- 不 push、不发布、不删除、不碰 RL/训练脚本与无关文件。
- 单个任务失败不阻塞其他(skip-and-continue)。

## 最可能的夜间 stall + 预案
1. **tp 机器不来/排队卡** → T2 跳过,代码+yaml 就绪,晨报给一键提交命令。
2. **chroot 内 apt 装 openjdk 走代理超时/失败** → `|| true` 容错,hydra 仍挂就记为已知系统包卡点(不阻塞其余 4 个轻的)。
3. **tzfix 没救到 schedule(tzset)** → 预期内(python-build 级),记录、不纠缠。
4. **重跑出现 regression** → 如实记进晨报,不隐藏。

## Logistics
- 提交:`git add` 仅 envdump 相关(sandbox_deployment.py 的注入、vendor 下 map/sample、docs/新建、sh/tp_envdump_ab*.yaml、docs/blog),clear message,不 push。
- 结果/日志:`vendor/swesmith-cache/envdump-{baseline2,envdump2}/`;汇总 `vendor/swesmith-cache/envdump_ab2_summary.txt`。
- **晨报**(先读这个):本文件的 `## Progress log`。
- 保持:tp 会话/机器由平台管;无需用户手动保活。

## 13 个失败归因(参考,来自 8119)
5 F2P 逻辑失败(非环境,gate 该排除) / 3 时区tzdata(T2 修) / 1 java-hydra(T2 apt 修) / 1 dask 安装路径冲突(T4 查) / 1 tmp flaky / 1 超时MONAI / 1 patch-apply-pyquery。

## Progress log
- 2026-07-03 夜 起点:T1 完成(代码修复+校验);T2 待提交;T3/T4 待办。
- 00:17 T2 已提交 tp **8123**(envdump-ab2,100 样本,ARM B=envdump+sysdeps+tzfix),监控挂上(bu95watce)。
- 00:18 **提交受阻**:`git commit` 报 `.git/objects` 权限不足(.git 属主非当前用户)。我的 8 个新文件已 `git add`(staged),但写不进 commit。晨报待办:用户回来 `git commit`(staged 内容已就绪);sandbox_deployment.py 注入仍只在工作树(见 T1 note)。**不影响 tp 运行**(读工作树)。
- 转 T3(blog)+T4(dask),等 8123。
- 00:30 **T4 完成**:dask 根因定位——freeze 含 `distributed==2025.2.0`(依赖 dask)→ envdump 装 distributed 时把 released dask 拉进 site-packages,与 /testbed editable 冲突。修法=envdump 后补 `pip install -e . --no-deps`(未验证,留白天应用)。已记入 blog §4。
- 00:35 **T3 完成**:blog 草稿 §0-3.5、§5 铺成成文(要点→散文),更新状态头(不再"待填")与发布前 todo。所有数字未改。
- 00:35 8123 运行中;监控 bu95watce 挂着。夜间任务全部推进完,余下=等 8123。

## ★ 晨报(先读这个)——2026-07-04 凌晨,夜间任务全部完成

**一句话:envdump 方法硬证实(核心贡献),但我夜里加的 tzdata+apt 修复基本没落地——收回 ~80% 预测,真实 ~63%。**

**T2 重跑结果(tp 8123,100 实例):**
- baseline(裸装)**30/100=30%** → envdump+fixes **63/100=63%**,**+33 翻转、0 回退**。
- 核心 envdump 结论在 100 样本上**稳健复现**(与 40 样本的 27.5%→67.5% 一致,~2x)。**这个是硬的、可写进 blog 的。**

**诚实校正——tzdata+apt 修复只是边际(原 40 子集 27→28,+1):**
- tzdata:django-money 翻转 ✅;nikola 的 TZif 环境错**修好了**(采集错→338 passed),但它另有 4 个 dev-server 网络测试失败(非环境)→仍 gold→0。
- schedule:`time.tzset` 缺失=python-build 级,tzdata 无解(预期内)。
- hydra:apt 装 java 跑了,但需要 **ANTLR 代码生成 build 步骤**,不是装包能解;失败签名已变。
- **按 stop-condition 未追第二次重跑**(修复没干净落地、剩余是深 build/网络/任务质量尾巴,边际递减)。

**真正结论:envdump 把 aarch64 无容器忠实率地板从 ~35% 抬到 ~63%(稳健、零回退)= 核心价值;再往上是异质长尾、不再是 pip 环境问题。实践回到"接受 ~60% + 多筛实例"。**

**待你回来做(都不占机器,除非想再验):**
1. `git commit`(8 个新文件已 staged;`.git/objects` 权限当时挡了我)。
2. 若想应用 dask 修法(envdump 后补 `pip install -e . --no-deps`)+ 重跑验证——这是唯一"干净可修"的残余,值得一试;hydra/nikola/schedule 不建议追。
3. blog 草稿 §4 已填真实 63% + 诚实归因;可定稿。
4. sandbox_deployment.py 注入仍在工作树(未提交,见 T1 note)。

**没做/未验证的**:sandbox_deployment.py 的 SYSDEPS/TZFIX 注入本身工作正常(日志确认 apt/tzdata 都跑了),只是效果边际;dask 修法未验证。

## 2026-07-04 blog 重构(用户改了发心)
旧稿"无容器复现 SWE-smith 三个坑"发心有问题(站成批判姿态)。改为**系列:走通 SWE-agent 训练+评测全流程,按层分三篇**:
- `docs/blog/blog1-systems.md`(系统层:docker/docker-free/沙箱编排/envdump,已成文,docker 部分 §6 留白待另一台机器填)
- `docs/blog/blog2-data.md`(数据层:数据集光谱/结构性泄露/诚实漏斗,骨架+种子)
- `docs/blog/blog3-harness.md`(harness 层:scaffold/推理/训练/vacuous reward,骨架)
旧 `draft-container-free-swe-smith.md` 已删(内容分入三篇;详细 13-失败归因留存于本 journal 晨报段+memory)。泄露归数据层、reward 归 harness 层(它们容器无关,不算系统层的坑);docker-vs-docker-free 回归系统层核心张力。

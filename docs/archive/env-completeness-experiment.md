# 环境完整度实验:Tier-1 vs Tier-2(并行,2026-06-22 20:33 触发)

> 目标:把保真率从 ~35-50% 往 ~100% 推。损失根因 = 重建 venv 只跑薄 `pip install -e .`,没复现 swesmith 镜像 build 的完整测试环境(`swesmith_environment.yml` 拿不到)。见 [data-generation-batches-and-method.md] §损失定位。
> 两条路并行试,比较保真率提升 + 量化"方法损失 vs 架构地板"。

## Tier-1(本地、快、partial):profile install_cmds + full-deps
- **数据已备**:`vendor/profile_install_cmds.json`(15 个带 extras 的 repo,如 autograd `.[scipy,test]`、apispec `.[dev]`)。
- **做法**:在 `sandboxdev/swesandbox/sandbox_deployment.py` 的 swesmith install_env 分支,若 repo 命中 profile_install_cmds.json 则用其 install_cmds 替换默认 `pip install -e .`;否则保留默认 + 已默认开的 full-deps。建议 gate `SWESMITH_PROFILE_INSTALL=1`。
- **测**:在 `vendor/fulldeps_test_filter.txt`(30 个缺依赖 gold→0 实例)上重建 + gold,**翻转率 vs full-deps-only 的 28%**。
- 预期:命中那 15 个 extras-repo 的实例会翻;非 extras repo 无变化(它们的依赖在拿不到的 env.yml 里)。

## Tier-2(faithful、重、正解):aarch64 上跑 swesmith try_install_py
- **工具**:`SWE-smith/swesmith/build_repo/try_install_py.py` + `configs/install_repo.sh`。它用 SWE-agent+LM 在本机装 repo+跑通测试,产出 ARM 原生 `sweenv_<repo>.yml`。
- **前置**:swesmith profiles import 缺 `tree_sitter_c` 等 —— 装好 build_repo 依赖,或只调 try_install_py 需要的部分;LM 用 GLM(`vendor/.glm_key`,内网直连不走代理)。
- **做法**:挑 ~10 个 repo(覆盖之前 gold→0 的、不同家族),逐个跑 try_install_py on aarch64 → 看能否产出 tests-passing 的 env;用产出的 env 重跑这些 repo 的实例 gold。
- **量**:① 多少 repo 的 ARM 原生 env 构建成功(tests 过);② 这些 repo 实例保真率;③ **构建失败的 repo = 真·架构地板**(连 LM 驱动 ARM 安装都跑不通)。

## 比较与产出
- 两边都在 chroot + 新代码(`-B` 分支 ref)下跑;
- 比 Tier-1 翻转率 vs Tier-2 保真率 vs 成本;
- 结论写回本 doc:哪条更值得全量铺(快但 partial vs 慢但 faithful + 能隔离架构地板)。

## 资源注意
- 都是 CPU pod(gpus:0)+ 可能用 GLM(Tier-2 的 LM 安装);
- 任务名**全小写**(否则卡资源准备中);
- 跑前确认 divgrid(dgrid-a1/b1)是否已腾出 CPU pod 资源。

---
## 执行记录 2026-06-23(定时触发)
- **NPU 已空出**(昨天满额,今晨 4 张可调度)→ 起 SFT smoke(task 7318,sh/sft/,首次训练闭环验证)。
- **Tier-1 启动**(task 7319):40 个 extras-repo 的 gold→0,开 `SWESMITH_PROFILE_INSTALL=1`(用 vendor/profile_install_cmds.json 的 per-repo extras)。量翻转率。
- **Tier-2 受阻(诊断结论)**:`try_install_py` 依赖 `docker`(建镜像用)+ `tree_sitter_c`,我们 docker-free 装不了。**直接跑不行**。要在 aarch64 docker-free 做 Tier-2,得自建"LM 驱动的原生 env 构建环"(用 GLM + 我们的 chroot/venv 框架复刻其精神),是独立工程,本轮不硬上,留作后续。

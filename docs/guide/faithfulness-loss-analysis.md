# 保真率损失:深度分析(证据固定 + 尝试记录)

> ⚠️ **2026-06-29 校正**:本文**根因分析(aarch64 重建环境 → 弥散 pytest collection 错 → ~35-50% 保真率,架构无关)仍有效、值得读**。但文中 faithful 计数(如"496 faithful 已够")用的是**打分器修复前**的 buggy→0 gate,已被取代——当前可信 RL 池 = **1864**(用修好的 grader 双向验证)。计数当历史快照看,结论(保真率非瓶颈、gate 正确排除不可修实例)仍成立。

> 问题:在 aarch64 上重建 swesmith 环境后,随机实例的 gold→1∧buggy→0 保真率仅 ~35%(bare)/~50%(full-deps),远低于 swesmith x86 验证时的 ~100%。本文固定证据、记录已做尝试、定位根因、列方案空间。
> 证据数据:`vendor/analysis/faithfulness_loss_evidence.json`(全量 1305 个 gold→0 的分类 + 样本)。最后更新 2026-06-23。

## 1. 基线与缺口(固定)

| 口径 | faithful 率 | 来源 |
|---|---|---|
| swesmith x86 + 镜像 build | ~100%(定义,数据集即验证集) | swesmith 构建时验证 |
| 我们 aarch64 + bare `pip install -e .` | ~35% | screen300/600 实测 |
| aarch64 + full-deps | ~50% | py500/divgrid 实测 |
| **预筛实例(salvage)** | **94%** | salvage 157/167 |

> 注:MiniSandbox 的 "docker≈精度" 是 **RL 训练表现(1600 curated 样本)**,非 per-instance 保真率;且它也是 venv+conda(非镜像)。所以我们随机 ~50% 与之不矛盾(arxiv:2602.11210 + README)。

## 2. 失败归因(固定证据,全量 1305 个 gold→0)

| 维度 | 占比 |
|---|---|
| **f2p 目标测试失败**(gold 应用后仍不过) | **~41%** |
| 有清晰错误签名(missing_module) | ~12% |
| pytest 参数/conftest 采集错 | ~2% |
| tzset/时区 | ~2% |
| **无清晰签名("other")** | **~64%** |

**"other" 主力的真实形态(抽样固定):**
- `F2P_FAIL|other`(478):几乎全是 **"X passed, N errors"** —— pytest **采集/setup error**(非断言失败)打掉若干测试文件,被grade 的测试恰在其中。例:radon `315 passed, 22 errors`、funcy `79 passed, 5 errors`、exceptiongroup `4 errors`。
- `F2P_OKNA|other`(529):大量 **"无 pytest 汇总"**(测试运行崩溃/空输出)+ 少量 `X failed, Y errors`。

**结论:主因 = 弥散的 pytest 采集 error** —— 不是单一缺包,而是**每个测试文件各缺各的**(某文件需某可选依赖、某 fixture 需某插件、某 conftest 导入失败)。叠加少量真断言失败 + 运行崩溃。**没有单一可修因。**

## 3. 已做尝试(记录 + 结果)

| 尝试 | 做法 | 结果 | 结论 |
|---|---|---|---|
| **full-deps**(`SWESMITH_FULL_DEPS=1`) | 装常见 extras + requirements-*test*/dev | 缺依赖 gold→0 翻转 **8/28=28%**(偏样本) | 只救常见 extras,主体没动 |
| **profile install_cmds**(Tier-1) | 用 swesmith 自己的 per-repo install_cmds(正确 extras 如 `.[scipy,test]`) | extras-repo gold→0 翻转 **3/34=8%** | **实证否定"缺 extras"假设**——装对 extras 也基本没用 |
| **Tier-2: try_install_py** | 复用 swesmith LM 驱动 env 构建 | **受阻**:依赖 `docker`(建镜像用)+ `tree_sitter_c`,docker-free 装不了 | 需 docker-free 改造(独立工程) |
| **HF Conda Backend Files** | 下 `lblankl/MiniSandbox` 找精确 env | 只有**基础多版本 python 脚本**(`3.10.sh`…21个),**无 per-repo env dump** | 精确 env(`sweenv_<repo>.yml`)未发布,在 swesmith build 日志里,拿不到 |

## 4. 根因(定位)

1. swesmith 镜像 build 时,每个 repo 的**完整测试环境**被 `conda env create --file sweenv_<repo>.yml` 装好(含每个测试文件需要的所有可选依赖/插件/精确版本);
2. 运行时 install 规格只是增量 `pip install -e .`,**假设镜像已有那层**;
3. 我们 docker-free 重建只跑了增量,**丢了那层弥散的完整依赖** → 各测试文件采集 error → grade 测试不 PASSED → gold→0;
4. 这层**弥散**(非单一包)→ full-deps/profile-extras 只能覆盖常见的一小部分 → 翻转率低;
5. **架构(aarch64)纯贡献小**:错误多是缺依赖/采集,与 ARM 无关(x86 同法重建也会有);少量版本敏感(f2p_fail 里的 attr/type error)才可能涉架构。

## 5. 方案空间(可行性 + 成本)

| 方案 | 能修多少 | 成本 | 评 |
|---|---|---|---|
| **接受 ~50% + 多筛**(gate 正确排除不可修的) | — | 极低 | ✅ 推荐:496 faithful 已够,筛选便宜,保真率非瓶颈 |
| TZ/locale + tzdata 对齐 | tzset/时区类 ~2% | 低 | 边际,证据显示占比小 |
| 补全常见测试插件(pytest-asyncio/mock/...) | 部分采集 error | 中 | 打地鼠,覆盖不全 |
| **LM 驱动 env 构建**(try_install_py 的 docker-free 复刻,GLM 跑通测试再 dump env) | 大部分(逼近 swesmith 原法) | 高(独立工程) | 真·彻底修,但是另一个项目 |
| 拿到 `sweenv_<repo>.yml`(求作者/build 日志) | 几乎全部 | 取决可得性 | 最快若能拿到,但未公开 |

## 6. 旁路发现(待跟进)
- **eval(7323)失败**:vLLM-ascend 加载我们合并的 SFT 模型时 `Engine core initialization failed`(ERR99999)。闭环 infer 这步的独立问题,需查(可能合并产物/vllm-ascend 配置/单卡)。

## 7. 结论
- **没有银弹**:保真损失是弥散的环境不完整(每测试文件各缺各的),三次尝试(full-deps 28%、profile-extras 8%、Tier-2 受阻)证明无单一补丁;
- **架构非主因**;
- **保真率不是流水线瓶颈**:gate 正确排除不可修实例,筛选便宜,496 faithful 已够起步;
- 真彻底修 = LM 驱动 env 构建(独立工程)或拿到未公开的 per-repo env dump。**建议优先级低**,把精力放回闭环 + 采样。

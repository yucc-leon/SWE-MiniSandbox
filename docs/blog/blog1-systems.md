# [草稿·未发布] 走通 SWE-agent 流程(一):系统层——docker、docker-free 与沙箱编排

> 系列发心:记录我们端到端走通 SWE-agent 训练+评测流程的实战。因算力约束,系统层分叉成两条路——docker-free(aarch64/无 docker)与 docker(x86)——两条都不像论文/文档说的那样坦途。本篇讲**系统层**:沙箱怎么搭、环境怎么忠实重建。数据层与 harness 层见系列(二)(三)。
> 状态:docker-free 部分已成文+实测;**docker 部分留白待填(另一台机器)**。发布前:统一语言、补图、复核数字。

## 0. 目标与分叉
目标不是批判任何数据集,而是**把 SWE-agent 的训练+评测流程端到端跑起来**。系统层的第一个岔路是沙箱怎么建:
- **docker 路线**(x86,可自由用 docker):直接拉官方镜像。本以为坦途——但也不是(见 §6,留白)。
- **docker-free 路线**(aarch64 NPU,无 docker):venv+conda 从零重建,叠 MiniSandbox 方案。这条路暴露了一堆原论文没讲清的坑,是本篇主体。

## 1. 沙箱编排(docker-free)
- MiniSandbox(venv+conda,非镜像)+ chroot 隔离;venv 共享缓存 + wheelhouse(离线 pip)+ tar 分发。
- clone 稳定性根修:proxy basic-auth + `checkout -B {base_commit} FETCH_HEAD`。
- 预筛池:gold→1 ∧ buggy→0 的 pre-check gate(零 API 成本先验环境)。

## 2. 核心坑:隐式、不可移植的环境规格
- 现象:无容器重建后忠实率仅 ~35%(裸装 `pip install -e .`)/ ~50%(补常见 extras),vs 官方镜像 ~100%。
- 根因:每个 repo 的完整测试环境在镜像 build 时由未发布的 `sweenv_<repo>.yml`(`conda env create`)装好;对外只暴露增量 `pip install -e .`,默认"镜像那层已存在"。无容器重建丢了那层。
- 失败是**弥散的**:每个测试文件各缺各的(可选依赖/pytest 插件/conftest 导入)。证伪"缺 extras"假设(装对 extras 仅翻转 8%,full-deps 28%)。**架构(aarch64)纯贡献小。**
- 一般化教训:**合成 SWE 环境应发布显式、可移植、完整的 env 规格,而非增量安装命令。**

## 3. 补救:把隐式环境变显式(envdump)
- 想法:完整环境就在公开镜像里,在有 docker 的机器上批量 `docker run <img> pip freeze / conda env export`,导出每个 repo 精确 pin,再拿显式清单在无容器机重建 venv。
- 落地:swesmith 222/222、SWE-bench Verified 500/500 全导出成功;注入沙箱经 `SWESMITH_ENVDUMP`/`SWEBENCH_ENVDUMP` opt-in。
- **实测(tp A/B,真实 aarch64 chroot pipeline)**:
  - 8119(40 实例):裸装 27.5% → envdump **67.5%**,+16 翻转、0 回退。
  - 8123(100 实例,+tzdata+apt 修复):裸装 30% → **63%**,+33 翻转、0 回退。核心结论稳健复现(~2x)。
- **诚实边界**:tzdata/apt 后续修复只是边际(原 40 子集 27→28)。tzdata 修好 TZif 环境层(django-money 翻/nikola 采集错→338pass)但 repo 上叠非环境失败;schedule `tzset`=python-build 级无解;hydra 需 ANTLR 代码生成 build 步骤、非 apt 能解。
- **结论**:envdump 把弥散 pip 依赖坎解决,忠实率**地板从 ~35% 抬到 ~63%(稳健、零回退)**;再往上是异质长尾(任务质量/深 build/平台/网络),不再是 pip 环境问题。实践回到"接受 ~60% + 多筛实例"。

## 4. 反证:根因在打包,不在语言/容器/架构
222 repo 中 91 个非 Python(Go/Rust/Node/PHP/Java),其环境规格是**完整、提交在仓库里的** lock 文件(go.mod/go.sum、package.json、Cargo.lock)——`go build`/`cargo`/`npm ci` 直接按 lock 拉精确依赖。它们**天然没有"隐式环境"坑**,真正需要的只是工具链。这反证坑二根因 = SWE-smith 对 Python 特有的"per-repo conda + 只发布增量 pip"打包方式,不是无容器/aarch64/语言本身。

## 5. 系统包层(pip 之外)
- 审计(`apt-mark showmanual` 镜像实际口径,非 profile 代码):仅 5 个 Python repo 刻意 apt 装系统包——pdfplumber(ghostscript)/pipdeptree(graphviz)/scrapy(lxml -dev 头)/hydra(openjdk)/pydantic(pipx)。其余系统层=固定基础镜像 + pip wheel 自带。
- 教训:以镜像实际为准,别照抄 profile 代码(conan 代码说要 cmake/meson,镜像实际没装)。

## 6. docker 路线(★留白——另一台机器填)
> 本以为官方镜像一路坦途,实则不然。待填:镜像拉取/构建摩擦、评测环境问题、与 docker-free 的对照。

## 7. 结论(系统层)
- docker-free 可行但有代价:envdump 把忠实率地板抬到 ~63%,是这条路的核心工程贡献;剩余是不可约长尾,接受 ~60% + 多筛。
- docker 路线解决忠实率,但(待 §6 填)有它自己的摩擦。
- **系统层的真相:沙箱怎么搭、环境规格是否显式可移植,直接决定可复现性——这与容器无关,是打包与编排的选择。**

## 相关
- `docs/guide/faithfulness-loss-analysis.md`、`docs/guide/docker-free-retrospective.md`
- 数据:`vendor/envdump_freeze_map.json`、`envdump_system_deps_map.json`、A/B `sh/tp_envdump_ab*.yaml`
- 系列(二)数据层、(三)harness 层

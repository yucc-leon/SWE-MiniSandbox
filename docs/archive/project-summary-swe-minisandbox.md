# SWE-MiniSandbox 项目阶段总结

## 项目定位

SWE-MiniSandbox 是面向 SWE-bench 类软件工程任务的本地化评测与运行环境项目。项目基于上游
[`lblankl/SWE-MiniSandbox`](https://github.com/lblankl/SWE-MiniSandbox) 进行适配，目标是在本地/受限服务器环境中复现 SWE-Agent 风格的 generation、sandbox execution 和 official-style scoring 流程，用于评估代码修复模型在真实开源仓库 issue 上的解题能力。

本项目把上游评测流程迁移到当前计算环境中，并尽量保持评测口径与官方实现一致。这样可以在本地资源、权限、网络和文件系统限制下，稳定运行 32B 等模型的批量评测，并获得可复现、可对比的 resolved 结果。

## 基于原项目的开发与优化

### 1. no-chroot 运行环境适配

原始项目默认假设具备较完整的 sandbox/chroot 能力，但当前运行环境对系统权限、隔离方式和文件系统访问存在限制。为此，本项目重点开发了 no-chroot 模式，使 sandbox execution 能在不依赖完整 chroot 的情况下运行。

主要工作包括：

- 适配 no-chroot 下的路径重写和工作目录映射，保证 agent、测试命令和 scoring 脚本看到的路径语义一致。
- 修复 shell/session 生命周期问题，避免 timeout 或异常退出后残留的 shell 状态污染后续实例。
- 在 session reset 后恢复必要的工具 PATH 和环境变量，保证后续命令仍能找到本地工具链。
- 对 patch 内容做 sanitization，降低二进制 patch、异常路径和无效 diff 对 scoring 流程的干扰。
- 增加 no-chroot 相关 smoke/integration 测试，覆盖路径重写、shell 恢复和运行时行为。

### 2. scoring 稳定性修复

在 32B baseline 评测过程中，早期结果受 runtime error 和 scoring 链路噪声影响较大。项目后续重点区分“模型没有解出题”和“评测基础设施失败”两类问题，优先修复后者。

主要修复包括：

- 修复 shell 脚本在 `set -e` 下提前退出，导致 official-style 汇总文件缺失的问题。
- 明确区分 generation 侧信号和 scoring 侧真值，避免 `submitted`、`empty_patch`、`completed`、`resolved` 等字段口径混用。
- 修复 timeout 后 shell 未正确回收导致的后续 reset error。
- 对 shared venv、wheelhouse、git cache 等依赖准备流程做稳定性处理，降低冷启动和共享文件系统压力。
- 修复评分过程中因系统包安装、宿主环境 PATH、代理配置等导致的非模型错误。

这些修复的目标是让 unresolved 尽可能代表模型或 agent 本身没有完成任务，而不是评测环境异常。

### 3. 环境准备与复现流程优化

为了让评测能在新环境中复现，本项目对环境准备流程做了收敛，避免只依赖调试期间临时手动操作。

主要工作包括：

- 固化 install script 和 bootstrap 逻辑，降低换环境后缺依赖、缺工具、缺 wheelhouse 的概率。
- 增加 shared venv cache/wheelhouse 的预热与复用策略，降低大批量 scoring 的重复安装成本。
- 对 generation 和 scoring 的运行状态、预测文件、轨迹文件、结果汇总进行口径梳理，便于中断后恢复和排查。
- 补充 handoff 文档和 resume checklist，方便新 session 或接手者快速理解当前评测状态。

### 4. 与上游行为差异的控制

由于本项目的目标是尽量贴近上游公开评测精度，开发过程中刻意控制了 agent 行为层面的改动，避免把基础设施修复和策略干预混在一起。

具体原则包括：

- 尽量不改 prompt、工具交互和 agent 决策逻辑。
- 优先修复 no-chroot、scoring、cache、runtime recovery 等基础设施问题。
- 将 baseline 与 intervention 分开看，和上游结果对比时只使用基础设施修复后的 baseline。
- 将 official scoring 输出作为 resolved 真值，不用 generation 侧字段覆盖评分结果。

## 当前阶段

当前项目已经完成一轮 no-chroot/scoring 稳定性收敛。

已完成的验证包括：

- no-chroot smoke 测试。
- shell recovery 测试。
- wheelhouse/bootstrap 测试。
- install script 相关测试。

从阶段目标看，本项目已经具备在当前受限环境下运行 SWE-MiniSandbox 评测流程的基础能力，并且解决了多类会显著污染分数的 runtime/scoring 问题。

## 后续原计划

如果项目继续推进，后续主要有两条主线。

### 1. 继续对齐上游评测精度

上游原始代码在 32B baseline 上的解决率约为 40% 出头，而当前本地 no-chroot 适配后的评测结果仍存在差距。后续需要继续排查本仓库相对上游的行为差异，重点包括：

- 对比 agent 轨迹、上下文管理、工具调用和 observation 累积方式。
- 检查 no-chroot 适配是否仍引入隐性行为差异。
- 分析 unresolved/error case，区分模型能力问题、agent 行为问题和基础设施问题。
- 在不改 prompt 和工具交互的前提下，尽量把基础设施层行为贴近上游。

### 2. 提升 generation 与 scoring 效率

全量 500 instance 评测流程耗时较长，后续原本计划继续优化吞吐效率：

- 复用和预热 shared venv cache，减少 scoring 阶段重复安装成本。
- 优化 scoring 并发策略，避免冷 cache 下盲目提高并发导致共享文件系统压力过大。
- 改善 generation/scoring 的断点恢复能力，减少中断后的重复计算。
- 对慢实例、timeout 实例和异常 shard 做更细粒度统计，定位主要耗时来源。

### 3. 完善可复现交接

项目后续建议补齐：

- 一份从空环境启动 generation/scoring 的最小复现文档。
- 当前关键环境变量、模型 endpoint、cache 路径和结果目录说明。
- 典型故障排查表，例如 proxy、git cache、shared venv、timeout、shell reset 等问题。
- 上游对齐 checklist，确保后续修改不会无意中改变 baseline 行为。

## 阶段结论

本阶段主要完成了 SWE-MiniSandbox 在受限环境下的 no-chroot 运行与 scoring 稳定性改造，使项目从“能跑部分流程”推进到“能较稳定地执行批量评测并产出可解释结果”的阶段。

当前最重要的技术判断是：在追求分数提升前，必须先保证评测链路本身稳定、可复现、与上游口径一致。否则分数变化无法判断是模型能力、agent 行为还是基础设施噪声导致。基于这一原则，本阶段优先收敛了 runtime/scoring 层问题，并暂时避免对 prompt 和工具交互做策略性改动。

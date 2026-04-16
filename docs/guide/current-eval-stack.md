# Current Evaluation Stack

本文档整理当前仓库中“已经能跑通的 MiniSandbox evaluation 主链”，重点回答两个问题：

- 当前可运行方案和项目原始代码相比，到底改了什么
- 修改后的 generation / scoring / sandbox / server 是怎么连起来执行的

本文只覆盖 evaluation 主链，不展开 RL training。

## 1. 当前方案的定位

当前可运行方案不是原始 SWE-Agent docker benchmark 直跑，也不是单纯的本机 vLLM 脚本，而是一个混合体系：

- agent 与 batch orchestration 仍沿用 SWE-Agent 的 `run-batch`
- sandbox 使用本仓库的 MiniSandbox，本地部署、无 Docker 评测
- runtime 依赖 SWE-ReX 的本地 shell/session 抽象
- generation 阶段调用 OpenAI 兼容的外部推理服务
- scoring 阶段不再依赖模型推理，而是用 `empty` agent replay `preds.json`
- 最终 official-style resolved 通过 SWE-bench harness 判定

如果你关心“和官方到底差多少”，先看 `docs/guide/eval-modes.md`。
当前文档描述的是工程主链，不等于严格官方对齐口径。

这套结构的关键目标是：

- 保留本地 container-free sandbox
- 把“生成 patch”和“官方风格评分”拆开
- 让最终分数尽量贴近 benchmark official resolved 口径

## 2. 与原项目相比的主要差别

下面按机制而不是按文件清单说明。

### 2.1 部署模型从 Docker 优先转为本地 MiniSandbox 优先

原始基线更偏向：

- Docker image 驱动实例环境
- 标准 SWE-Agent / SWE-ReX 路径

当前方案改成：

- `sandboxdev/swesandbox/sandbox_deployment.py`
  作为主要 deployment
- `use_chroot: false` 成为 Ascend 路径下的默认工作模式
- 不依赖容器 namespace/chroot 才能运行 evaluation

这意味着 evaluation 的主要隔离边界从“容器”变成了：

- 独立 sandbox 根目录
- 独立 repo copy
- 独立 shared venv / git cache / tool root

### 2.2 新增 no-chroot 运行路径

这是当前方案最关键的基础改造之一。

在 `sandboxdev/swesandbox/sandbox_deployment.py:164` 中：

- `SandboxDeploymentConfig.use_chroot` 被显式暴露
- 当 `unshare --mount` 不可用时，会自动退回 `use_chroot=false`

含义是：

- 原始设计期望用 `unshare + mount + chroot` 做更强隔离
- 现在为了适应无特权环境、K8s/平台容器、Ascend 机器限制，必须支持 plain bash session + 路径映射

围绕这个 no-chroot 路径，还配套改了：

- `SWE-agent/sweagent/environment/repo.py`
  repo copy/reset 方式
- `SWE-agent/sweagent/tools/tools.py`
  tool bundle 安装根路径从硬编码 `/root/tools` 变成可适配 `/tools`
- `SWE-ReX/src/swerex/runtime/local.py`
  和 `SWE-ReX/src/swerex/runtime/sandbox.py`
  去掉裸 `\r` 干扰，降低 no-chroot shell 输出污染

### 2.3 generation 和 scoring 被拆成两个独立阶段

这是当前方案和“传统一把跑完”的另一个本质差异。

当前链路是：

1. generation:
   `sh/run_sweagent_eval_ascend.sh`
2. scoring:
   `sh/run_swebench_scoring_ascend.sh`
3. formal wrapper:
   `sh/run_sweagent_formal_ascend.sh`

拆开的价值是：

- generation 阶段只负责产出 `preds.json`
- scoring 阶段只负责 replay patch 并做官方风格评判
- 可以对已有 `preds.json` 重打分，而不用重新调用模型

### 2.4 scoring 使用 patch replay，而不是再让 agent 推理

在 `config/sweagent_score_ascend.yaml` 中：

- agent 类型是 `empty`
- `pre_check: true`

同时在 `SWE-agent/sweagent/run/batch_instances.py` 中：

- 支持 `instances.model_patch_file`
- 会从 `preds.json` 里把每个实例的 `model_patch` 注入为 `ds["patch"]`

然后在 `SWE-agent/sweagent/agent/empty_agent.py` 中：

- `pre_check()` 直接在 sandbox 内应用 patch 并执行评分

所以 scoring 阶段本质上不是“模型再答一遍”，而是：

- 读取已有 patch
- 在干净 sandbox 中应用 patch
- 运行官方评分逻辑
- 写出 `.pred` 和状态文件

### 2.5 official-style resolved 被单独后处理汇总

当前仓库里又新增了两个汇总脚本：

- `sh/postprocess_eval.py`
  负责 generation 阶段本地汇总，例如 `preds.json`、时延统计、本地 reward summary
- `sh/postprocess_official_scoring.py`
  负责 scoring 阶段生成 `results.json` 和 `instance_results.jsonl`

这意味着“本地 quick summary”和“官方风格 resolved summary”已经被概念上区分开了。

目前 `postprocess_official_scoring.py` 的语义也已经收敛为：

- `submitted` / `empty_patch` 反映 generation 阶段是否真的给了 prediction
- `completed` / `resolved` / `error` 反映 scoring 阶段的 official-style 输出

### 2.6 新增 prewarm/prepare-first 机制

当前正式方案并不是直接硬跑 500 题，而是允许先做环境预热：

- `sh/run_sweagent_prepare_ascend.sh`
- `config/sweagent_prepare_ascend.yaml`
- `sandboxdev/swesandbox/prep_plan.py`
- `sandboxdev/swesandbox/prep_run.py`
- `sandboxdev/swesandbox/prep_report.py`

这个 prepare 阶段会：

- 按环境桶统计实例
- 生成 prewarm dataset
- 用 `empty` agent 做环境构建与缓存预热
- 输出失败桶报告

这是为了降低：

- 首次建环境抖动
- 某些 Python backend 首次安装失败
- 全量正式跑时的冷启动成本

### 2.7 模型访问改成 OpenAI-compatible local/remote server

在 `SWE-agent/sweagent/agent/models.py` 中，有一组明显围绕本地 OpenAI 兼容 server 的增强：

- 自动识别本地模型路径
- 本地 tokenizer/chat template 计 token
- 记录 LM request metrics
- 处理 context window bad request

这说明当前 agent 层不再只是假设标准 OpenAI/Anthropic provider，而是明确支持：

- `api_base` 指向本地或局域网 vLLM
- 模型名可能是本地路径或 alias
- token 统计需要对齐本地 tokenizer

### 2.8 新增多 server、代理、watchdog 和恢复逻辑

当前 Ascend 路径为了解决 NPU 推理可用性，额外引入了运维层脚本：

- `sh/serve_qwen_ascend.sh`
  单机 vLLM 启动器
- `sh/run_sweagent_eval_ascend_dual.sh`
  多后端 orchestrator
- `sh/openai_lb_proxy.py`
  本地代理与 backend fallback
- `sh/eval_watchdog.py`
  单实例卡死检测和 run 级熔断

这些都不是原项目的主线功能，而是当前“真实机器跑全量 evaluation”所必需的工程化补强。

## 3. 当前 evaluation 的执行流程

下面按正式路径说明。

### 3.1 formal 入口

入口是：

- `sh/run_sweagent_formal_ascend.sh`

它负责：

1. 设定 generation config、score config、runtime root
2. 调用 generation
3. 检查 `preds.json` 是否存在
4. 调用 scoring

它本身不做推理、不做评分，只是总控。

### 3.2 generation 阶段

generation 入口：

- `sh/run_sweagent_eval_ascend.sh`

它依次做：

1. 激活 `swe-sandbox` 环境
2. 设置本地 `PYTHONPATH`
3. 校验 `sweagent / swerex / swesandbox` 导入
4. 如果 `PREPARE_FIRST=1`，先跑 prepare 阶段
5. 检查 `API_BASE`
6. 如 `AUTO_START_SERVER=1` 且 endpoint 不可用，则本地启动 `sh/serve_qwen_ascend.sh`
7. 通过 `python -m sweagent run-batch` 启动批量生成
8. 可选启动 watchdog
9. 结束后跑 `sh/postprocess_eval.py`

generation 产物主要包括：

- `output/<instance_id>/<instance_id>.traj`
- `output/<instance_id>/<instance_id>.pred`
- `output/<instance_id>/<instance_id>.patch`
- `output/preds.json`
- `output/run_batch_exit_statuses.yaml`
- `local_eval_report.json`
- `timing_summary.json`

### 3.3 scoring 阶段

scoring 入口：

- `sh/run_swebench_scoring_ascend.sh`

它依次做：

1. 激活 `swe-sandbox`
2. 读取 generation 阶段的 `preds.json`
3. 用 `empty` agent 执行 `run-batch`
4. 每个实例把 `model_patch` 注入到 sandbox repo
5. 执行 official-style grading
6. 结束后调用 `sh/postprocess_official_scoring.py`

scoring 的核心不是大模型调用，而是 patch replay。

产物主要包括：

- `output/<instance_id>/<instance_id>.pred`
- `output/run_batch_exit_statuses.yaml`
- `results.json`
- `instance_results.jsonl`

## 4. sandbox 的主要机制

### 4.1 每个实例有独立 sandbox 根目录

在 `sandboxdev/swesandbox/sandbox_deployment.py` 中：

- 每个实例会创建唯一 `root_dir`
- repo、tools、venv、脚本都在这个根目录下组织

这使得同一批实例可以并行运行，且互不污染。

### 4.2 git cache 与 shared venv

当前方案把环境构建拆成两个缓存层：

- `gitcache`
  缓存 repo 复制/归档结果
- `shared_venv`
  按 repo/version/image 维度复用构建好的 Python 环境

这是全量 evaluation 能跑起来的关键，否则 500 题的冷启动成本会过高。

### 4.3 no-chroot 下的逻辑路径保持不变

即使实际运行是 host 路径，agent 仍尽量看到逻辑路径：

- `/testbed`
- `/tools`
- `/root`

这件事依赖：

- deployment 侧的 `sandbox_path`
- tool root 适配
- repo copy/reset 适配

其目标是避免 agent prompt 和工具逻辑暴露大量 host 路径细节。

### 4.4 tool bundle 安装方式被改造

在 `SWE-agent/sweagent/tools/tools.py` 中：

- tool bundle 安装根从硬编码 `/root/tools` 改成读取 deployment 的 `abs_tool_path`
- bundle 已存在时会跳过重复上传

这是 no-chroot sandbox 下工具能稳定工作的前提。

### 4.5 scoring 时 patch 应用与 official grading

在 `SWE-agent/sweagent/environment/swe_sbenv.py` 中：

- 读取 `ds["patch"]`
- 应用 patch 和 `test_patch`
- 执行 `_calculate_reward()`

而在 `sandboxdev/swesandbox/sandbox_deployment.py:1225` 附近：

- 对 SWE-bench，resolved 判定直接来自 `get_resolution_status(report) == ResolvedStatus.FULL.value`
- 然后返回 `int(success)`，写入 `.pred["reward"]`

所以对正式 scoring `.pred` 而言，`reward == 1` 本质上就是 official resolved。

## 5. server 与模型调用机制

### 5.1 单机 server

由 `sh/serve_qwen_ascend.sh` 负责：

- 激活 vLLM Ascend env
- source Ascend 环境
- 做 Python/vLLM import preflight
- 启动 `vllm serve`

### 5.2 generation 如何接入模型

generation 配置在 `config/sweagent_infer_ascend_officiallike.yaml`：

- `agent.model.api_base`
- `agent.model.name`
- `agent.model.max_input_tokens`

最终由 `LiteLLMModel` 通过 OpenAI-compatible 接口发请求。

在“两机模式”下，推荐不要直接手工调用 `run_sweagent_eval_ascend.sh`，而是走：

- `sh/run_sweagent_formal_remote_infer.sh`

它会：

- 使用远端任务机提供的 `API_BASE`
- 先 probe 远端 OpenAI-compatible server
- 明确禁用本地 auto-start server
- 然后复用同一套 generation + scoring 主链

### 5.3 多 server 与代理

在多 NPU/多实例模式下：

- `sh/run_sweagent_eval_ascend_dual.sh`
  负责按 NPU 切 shard 与 backend server
- `sh/openai_lb_proxy.py`
  提供统一入口和 fallback

逻辑上相当于：

- 后端若干 `vllm serve`
- 前面一个本地 OpenAI 兼容代理
- generation 全部打到代理入口

### 5.4 watchdog 机制

`sh/eval_watchdog.py` 会：

- 轮询 output 目录变化和已完成实例集合
- 检测卡住的实例树
- 先尝试杀实例相关进程
- 仍无恢复时再终止整个 run

这不是评测语义的一部分，而是长跑稳定性的补丁。

## 6. 当前方案的配置分层

建议把当前配置理解成三层：

### 6.1 generation config

- `config/sweagent_infer_ascend*.yaml`

负责：

- agent prompt
- tool bundles
- model 配置
- sandbox deployment 默认值

### 6.2 scoring config

- `config/sweagent_score_ascend.yaml`

负责：

- `empty` agent replay
- `pre_check=true`
- 同一套 sandbox deployment

### 6.3 orchestration shell

- `run_sweagent_eval_ascend.sh`
- `run_swebench_scoring_ascend.sh`
- `run_sweagent_formal_ascend.sh`
- `run_sweagent_prepare_ascend.sh`

负责：

- 环境变量装配
- runtime root 组织
- 本地 endpoint 检测
- server 启停
- 产物后处理

## 7. 当前方案中的已知粗糙点

这些点不影响“能跑”，但值得记录：

- `results.json` 的生成此前依赖 scoring 脚本后处理；如果 `run-batch` 非零退出且 shell 开着 `set -e`，后处理会被跳过。这个问题已经在 `sh/run_swebench_scoring_ascend.sh` 修正。
- `postprocess_official_scoring.py` 目前对“空 patch 但 reward=1”的边界处理仍可能与 official `passed` 口径不一致，后续应再统一。
- 多 server / proxy / watchdog 目前仍是 shell + Python helper 组合，工程上可继续收敛。

## 8. 推荐如何理解这套系统

如果只抓主线，可以把当前方案理解成：

1. 先准备本地可复用 sandbox 环境缓存
2. generation 阶段让 agent 通过 OpenAI 兼容 server 产生 patch
3. 把 patch 汇总成 `preds.json`
4. scoring 阶段不用模型，直接 replay patch
5. 由 SWE-bench harness 给出 official-style resolved 结果

它的核心价值不是“把所有东西都本地化”，而是：

- 把 sandbox 从 Docker 解耦
- 把评分从生成解耦
- 把模型服务从实例评测解耦

这三层解耦，是当前方案能够继续演进到“任务平台起 server，本地机做编排”的根本原因。

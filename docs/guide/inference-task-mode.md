# Task-Submitted Inference Mode

本文档整理从“当前开发机直接拉起 vLLM server”迁移到“在内部任务平台提交推理任务，由任务启动 OpenAI 兼容 server，本地开发机只负责非 NPU 依赖编排”的准备项。

目标模式不是重写整个 evaluation 框架，而是把“推理服务生命周期”从本机 shell 搬到任务平台，保留当前已经验证过的 docker-free MiniSandbox evaluation/scoring 主链路。

## 1. 目标工作模式

推荐把系统拆成两个面：

- 本地开发机：
  负责准备配置、提交推理任务、轮询任务状态、拿到 `API_BASE`、执行 `run_sweagent_eval_ascend.sh` / `run_swebench_scoring_ascend.sh` 这类非 NPU 依赖流程、做日志汇总和失败恢复。
- 任务平台：
  负责用指定 docker image 和硬件资源启动推理引擎，暴露 OpenAI 兼容接口，例如 `/health`、`/v1/models`、`/v1/chat/completions`。

换句话说，未来应该把 “server management” 从 evaluation helper 中抽出去，变成一个单独的“推理任务提交层”。

## 2. 当前代码中已经可复用的部分

现有代码并不要求推理服务必须由本机启动；它只要求存在一个可访问的 OpenAI 兼容 endpoint。

直接可复用的入口有：

- `sh/run_sweagent_eval_ascend.sh`
  只要提供 `API_BASE`，就可以驱动生成阶段；本机直启 server 只是 `AUTO_START_SERVER=1` 下的一个 fallback。
- `sh/run_sweagent_formal_ascend.sh`
  已经把 generation 和 scoring 拆成两个阶段，未来可以只替换 generation 的 server 来源。
- `config/sweagent_infer_ascend_officiallike.yaml`
  这是当前工程增强配置，不是最干净的对齐 baseline。对齐实验建议优先改用
  `config/sweagent_infer_ascend_minisandbox_baseline.yaml`
  agent 侧已经通过 `agent.model.api_base` 访问 OpenAI 兼容接口。
- `sh/openai_lb_proxy.py`
  如果平台侧会起多个后端 server，这个代理仍然可以作为本地入口层；也可以把负载均衡逻辑迁到平台侧。

所以迁移重点不是改 agent/sandbox 主体，而是补齐“任务提交、服务发现、生命周期管理”。

## 3. 建议补齐的代码能力

### 3.1 任务提交层

需要新增一个独立脚本或 Python 模块，例如：

- `sh/submit_inference_task.sh`
- `sh/poll_inference_task.sh`
- `sh/fetch_inference_endpoint.sh`
- 或者一个统一的 `sh/run_remote_inference_task.py`

这个层至少要做四件事：

- 提交任务：
  向内部网页平台提供 docker image、硬件资源、环境变量、启动脚本。
- 轮询状态：
  区分 `queued/running/succeeded/failed/cancelled`。
- 获取服务地址：
  返回 `API_BASE`，以及必要时的 `MODEL_NAME` 映射。
- 拉日志：
  至少能把平台任务 ID 和远端日志链接写回本地 runtime 目录。

建议输出一个本地状态文件，例如：

```json
{
  "task_id": "infer-20260412-001",
  "status": "running",
  "api_base": "http://10.0.0.12:8000/v1",
  "model_name": "/path/to/workspace/models/sweagent-7b",
  "submitted_at": "2026-04-12T20:00:00Z",
  "image": "registry/internal/vllm-ascend:0.11.0-cann8.3",
  "resource_profile": "Ascend-1xNPU-96G"
}
```

建议路径：

- `${RUNTIME_ROOT}/inference_task.json`

这样 generation/scoring 日志可以和推理任务绑定起来。

当前仓库已经提供一个最小通用骨架：

- `sh/run_remote_inference_task.py`
  用于生成提交包、记录任务元数据、探活远端 endpoint
- `sh/check_openai_compatible_server.py`
  用于独立检查 `/health`、`/v1/models` 和可选的 `chat/completions`

推荐的最小流程是：

```bash
python sh/run_remote_inference_task.py render \
  --runtime-root .runtime/remote-infer-sweagent7b \
  --image registry/internal/vllm-ascend:0.11.0-cann8.3 \
  --resource-profile Ascend-1xNPU-96G \
  --model-path /models/sweagent-7b \
  --model-name /path/to/workspace/models/sweagent-7b \
  --public-base-url http://TASK_HOST:8000/v1 \
  --conda-init-script /path/to/workspace/miniforge3/bin/activate \
  --conda-env-name vllm-ascend-cann8.3-v011-clean \
  --ascend-nnal-env /usr/local/Ascend/nnal/atb/set_env.sh \
  --ascend-toolkit-env /usr/local/Ascend/ascend-toolkit/set_env.sh \
  --enable-prefix-caching
```

这会生成：

- `${RUNTIME_ROOT}/remote-inference/launch_vllm_task_ascend.generated.sh`
- `${RUNTIME_ROOT}/inference_task.request.json`

手工在平台上提交后，再把任务信息写回本地：

```bash
python sh/run_remote_inference_task.py record \
  --runtime-root .runtime/remote-infer-sweagent7b \
  --task-id infer-20260412-001 \
  --status running \
  --api-base http://TASK_HOST:8000/v1
```

等任务 ready 后，做一次探活：

```bash
python sh/run_remote_inference_task.py probe \
  --runtime-root .runtime/remote-infer-sweagent7b \
  --check-chat \
  --chat-model /path/to/workspace/models/sweagent-7b
```

### 3.2 平台启动脚本模板

平台任务最终还是要落到“运行一段 shell 脚本”。建议把启动脚本模板纳入仓库，而不是每次手填网页表单。

建议新增：

- `sh/templates/launch_vllm_task_ascend.sh`
- 如果未来支持 GPU，再补 `sh/templates/launch_vllm_task_cuda.sh`

这个模板至少要包含：

- 进入正确 env
- source Ascend/CUDA 环境
- 校验模型目录
- 校验 `import vllm`
- 启动 `vllm serve`
- 写 readiness 日志
- 如果平台允许，打印最终服务 URL

这样网页平台里只需要传少量参数：

- docker image
- 资源规格
- 启动命令
- 环境变量

而不需要把具体业务逻辑散落在网页表单里。

当前模板文件已经落在：

- `sh/templates/launch_vllm_task_ascend.sh`
- `sh/templates/launch_sglang_task_ascend.sh`

### 3.3 endpoint 发现与 readiness 协议

当前本地 helper 依赖以下事实：

- `GET /health` 可用
- `GET /v1/models` 可用
- `POST /v1/chat/completions` 遵循 OpenAI 兼容协议

未来任务平台模式下，也应保持这套最小契约不变。推荐把 readiness contract 固化成文档和探测代码：

- server 就绪判定：`/v1/models` 返回 200
- 别名模型判定：如果对外 model alias 不等于真实模型路径，应记录 rewrite 规则
- 异常判定：区分“端口已开但 engine 未 ready”和“进程已崩溃”

建议新增一个本地探针脚本：

- `sh/check_openai_compatible_server.py`

它至少检查：

- `/health`
- `/v1/models`
- 一个最小 `chat/completions`

### 3.4 将本地 auto-start 与远端 server 模式解耦

当前 `sh/run_sweagent_eval_ascend.sh` 仍内建“本地探活失败就 `nohup` 拉起 server”的能力。未来建议保留，但下沉成可选模式：

- `SERVER_MODE=local_autostart`
- `SERVER_MODE=external_api`
- `SERVER_MODE=task_submitted`

最少需要做到：

- `external_api`：
  仅使用传入的 `API_BASE`，不尝试启动本地 server
- `task_submitted`：
  先调用“任务提交层”，拿到 endpoint 后再进入 evaluation

推荐不要继续把“提交任务”直接塞进 `run_sweagent_eval_ascend.sh`，而是单独做前置阶段。原因是：

- 失败域不同
- 重试逻辑不同
- 生命周期不同
- 日志关注点不同

### 3.5 多后端与代理职责重新划分

当前多机/多卡链路是：

- `sh/run_sweagent_eval_ascend_dual.sh`
- `sh/openai_lb_proxy.py`

未来有两种可选方案：

1. 平台负责负载均衡
   本地只拿一个 `API_BASE`。
2. 平台只起多个裸 server
   本地继续起一个 `openai_lb_proxy.py` 聚合这些后端。

从维护成本看，建议优先选方案 1。因为任务平台通常更适合管理：

- 多副本生命周期
- 资源隔离
- 服务发现
- 健康检查

本地保留 proxy 的理由主要是：

- 平台暂时不支持多后端统一暴露
- 你希望保留当前的 fallback/轮转策略

### 3.6 日志与工件规范

迁移后最容易丢的是“这次 evaluation 用的到底是哪一个远端 server”。建议固定产物：

- `${RUNTIME_ROOT}/inference_task.json`
- `${RUNTIME_ROOT}/inference_probe.json`
- `${RUNTIME_ROOT}/inference_submit.log`
- `${RUNTIME_ROOT}/inference_remote.log.link`

至少记录：

- 平台任务 ID
- docker image
- 资源规格
- 启动脚本版本
- 模型路径
- endpoint
- readiness 时间
- 本地 evaluation runtime root

## 4. 建议补齐的文档

至少建议补四类文档：

### 4.1 平台任务模板文档

说明平台表单怎么填：

- docker image
- 硬件规格
- 环境变量
- 启动脚本
- 端口暴露
- 健康检查

### 4.2 本地编排 runbook

说明本地机器如何执行：

1. 提交推理任务
2. 轮询 endpoint
3. 启动 generation
4. 启动 scoring
5. 回收任务

当前仓库已经补了一个面向“两机模式”的正式入口：

- `sh/run_sweagent_formal_remote_infer.sh`

它的职责是：

- 从 `API_BASE` 或 `inference_task.json` 读取远端 endpoint
- 在进入 generation 之前探活远端 server
- 强制 `AUTO_START_SERVER=0`
- 本地执行 generation
- 本地执行 scoring

典型用法：

```bash
INFERENCE_TASK_RUNTIME_ROOT=.runtime/remote-infer-sweagent7b \
EVAL_RUNTIME_ROOT=.runtime/ascend-eval-sweagent7b-remote-formal \
SCORE_RUNTIME_ROOT=.runtime/ascend-score-sweagent7b-remote-formal \
MODEL_NAME=/path/to/workspace/models/sweagent-7b \
NUM_WORKERS=16 \
INSTANCE_SLICE=:500 \
bash sh/run_sweagent_formal_remote_infer.sh
```

### 4.3 故障处理文档

至少覆盖：

- 任务成功启动但 `/v1/models` 不 ready
- 平台任务已 ready 但本地访问失败
- endpoint 变化/过期
- 评分完成但 server 未及时回收

### 4.4 安全与资源边界说明

要说明清楚：

- 哪些路径必须放在镜像里
- 哪些模型路径必须挂载
- 哪些 token/密钥需要平台注入
- 本地和远端谁负责回收 GPU/NPU 资源

## 5. 推荐代码改造顺序

建议按这个顺序做，而不是一步到位：

1. 先把“任务提交脚本”独立出来，但仍手工把返回的 `API_BASE` 传给 `run_sweagent_eval_ascend.sh`
2. 再把任务状态文件落盘到 `RUNTIME_ROOT`
3. 再加 `SERVER_MODE=task_submitted`
4. 最后再考虑把多后端代理、自动回收、平台日志同步整合进去

这样可以避免同时改动：

- evaluation 主流程
- 远端 server 生命周期
- 任务平台 API 适配

## 6. 推荐最小交付物

如果只做最小可用版本，建议至少准备这些文件：

- `docs/guide/task-inference-runbook.md`
- `sh/templates/launch_vllm_task_ascend.sh`
- `sh/run_remote_inference_task.py`
- `sh/check_openai_compatible_server.py`

以及一个最小状态约定：

- 本地运行 generation 前，必须已经有一个可读的 `inference_task.json`
- 其中必须包含 `api_base`

## 7. 当前阶段的结论

从当前代码结构看，迁移到“任务平台起 server，本地机做编排和 sandbox evaluation”是自然演进，不需要推翻现有 MiniSandbox evaluation 主链。

真正需要补的是三层：

- 推理任务提交与服务发现
- 平台启动脚本模板
- 日志/状态文件规范

而不是重写 agent、sandbox 或 scoring。

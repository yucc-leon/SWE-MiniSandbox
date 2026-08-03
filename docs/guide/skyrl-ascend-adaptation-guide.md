# SkyRL 昇腾 NPU 适配指南

> 基于 codescout 项目在 Ascend 910 + CANN 8.3 上的实战经验
> 用于指导 SWE-MiniSandbox 的昇腾适配

## 1. 概述

我们在 codescout 项目中完成了 SkyRL（旧版，commit 81e5a97）在昇腾 NPU 上的完整适配，
包括 FSDP2 训练、vLLM 推理、Ray 分布式调度、HCCL 通信、weight sync 等全链路。
本文档记录适配过程中的关键经验，供 SWE-MiniSandbox 复用。

SWE-MiniSandbox 内嵌的 SkyRL 版本与 codescout 使用的版本结构高度一致，
需要的 NPU patch 基本相同。

## 2. 适配分层

### 通用层（任何 SkyRL + Ascend 组合都需要）

| 层 | 内容 | 复杂度 |
|----|------|--------|
| Monkey-patch | `torch.cuda.*` → `torch.npu.*` 透明代理 | 低，已有现成代码 |
| Device patch | 源码中 `cuda`→`npu`, `nccl`→`hccl`, `GPU`→`NPU` | 中，有自动化脚本 |
| Attention | `eager`→`sdpa`（避免 O(n²) 显存） | 低，改一行 |
| flash_attn | import 改为 try/except | 低 |
| vllm-ascend | 从源码构建（修改 build deps） | 中 |

### 任务层（SWE 任务特定）

| 层 | codescout | SWE-MiniSandbox |
|----|-----------|-----------------|
| Agent SDK | openhands-sdk 1.7.1 | SWE-agent + SWE-ReX |
| 环境隔离 | git clone + /tmp/testbed | sandbox (chroot/namespace) |
| Reward | multilevel_localization_f1 | swebench test execution |
| Tool calling | hermes parser via vLLM | SWE-agent 自带 |

## 3. 已验证的软件栈

| 组件 | 版本 | 说明 |
|------|------|------|
| CANN | 8.3.RC1 | 8.5 下 vllm-ascend 0.11 不稳定 |
| torch | 2.8.0+cpu | `--no-deps` 安装，避免拉 CUDA torch |
| torch_npu | 2.8.0.post2 | `--no-deps` 安装 |
| vllm | 0.11.0 | SkyRL 旧版 pin 的版本 |
| vllm-ascend | 0.11.0rc2.dev | 源码构建，修改了 torch 2.8 兼容性 |
| ray | 2.51.1 | |
| transformers | 4.57.6 | 需要 patch continue_final_message |

## 4. SkyRL 源码 patch 详解

### 4.1 NPU Device Patch（9 个文件）

自动化脚本：`codescout/scripts/setup_ascend_env.sh` 或 `SkyRL/npu_support/apply_npu_patches.py`

核心替换规则：
- `torch.cuda.*` → `torch.npu.*`（current_device, empty_cache, synchronize, set_device 等）
- `torch.device("cuda:N")` → `torch.device("npu:N")`
- `init_device_mesh("cuda", ...)` → `init_device_mesh("npu", ...)`
- `backend="nccl"` → `backend="hccl"`
- `{"GPU": N}` → `{"NPU": N}`（Ray 资源）

受影响的文件（SWE-MiniSandbox 的 SkyRL 中已确认存在）：

```
skyrl-train/skyrl_train/distributed/fsdp_strategy.py    (6 处)
skyrl-train/skyrl_train/distributed/fsdp_utils.py       (9 处)
skyrl-train/skyrl_train/distributed/strategy.py          (7 处)
skyrl-train/skyrl_train/workers/fsdp/fsdp_worker.py     (3 处)
skyrl-train/skyrl_train/workers/worker.py                (2 处)
skyrl-train/skyrl_train/inference_engines/vllm/vllm_engine.py (4 处)
skyrl-train/skyrl_train/model_wrapper.py                 (flash_attn + sdpa)
```

### 4.2 SDPA Attention Patch（关键）

**这是最重要的 patch**。没有它，`flash_attn=false` 会让 HuggingFace 模型用 eager attention，
attention score 矩阵是 O(n²) 显存，长序列必定 OOM。

文件：`skyrl-train/skyrl_train/model_wrapper.py`

```python
# 原始代码:
from flash_attn.bert_padding import pad_input, unpad_input
# ...
self.attn_implementation = "flash_attention_2" if use_flash_attention_2 else "eager"

# 修改为:
try:
    from flash_attn.bert_padding import pad_input, unpad_input
except ImportError:
    pad_input = None
    unpad_input = None
# ...
self.attn_implementation = "flash_attention_2" if use_flash_attention_2 else "sdpa"
```

PyTorch SDPA 在 NPU 上自动走 memory-efficient 实现，benchmark 结果：

| Seq Len | SDPA | npu_fusion (BNSD) | eager (估算) |
|---------|------|-------------------|-------------|
| 2k | 0.37ms | 0.45ms | ~0.4ms |
| 16k | 13.57ms | 27.64ms | OOM |
| 32k | 56.25ms | 111.71ms | OOM |

### 4.3 Monkey-Patch（npu_support/）

通过 `.pth` 文件在 Python 启动时自动加载，做以下透明代理：

- `torch.cuda` → `torch.npu`（模块级 proxy）
- `Tensor.cuda()` → `Tensor.npu()`
- `ray.remote(num_gpus=N)` → `resources={"NPU": N}`
- `init_process_group(backend="nccl")` → `"hccl"`
- `flash_attn` stub（避免 import 报错）

安装方式：
```bash
SITE=$(python -c "import site; print(site.getsitepackages()[0])")
cp -r SkyRL/npu_support $SITE/npu_support
cp SkyRL/npu_support/npu_autoload.pth $SITE/
```

## 5. 踩坑记录

### 5.1 显存管理

| 问题 | 原因 | 解决 |
|------|------|------|
| backward OOM | eager attention O(n²) | 改用 SDPA |
| forward OOM（碎片化） | PyTorch reserved >> allocated | `PYTORCH_NPU_ALLOC_CONF=expandable_segments:True` |
| 64GB vs 80GB | Ascend 910 比 H100 少 16GB/卡 | 降低 gpu_memory_utilization 或 context length |

Ascend 910 (64GB) 上 Qwen3-4B 的实测显存上限：
- 4 train + 4 infer：max_seq_len ~40k 可跑（SDPA），~18k 就 OOM（eager）
- 2 train + 6 infer：训练卡显存不够（FSDP 分片太少）

### 5.2 vllm-ascend 构建

PyPI 上的 vllm-ascend 0.11.0rc1 是 source distribution，build-requires 写死了
torch==2.7.1。需要从源码构建并修改：
- `pyproject.toml`: torch/torch_npu 版本
- `CMakeLists.txt`: torch 版本检查 + Python 3.12 支持
- `setup.py`: python3 路径 + ASCEND_PYTHON_EXECUTABLE

详细步骤见 `codescout/scripts/setup_ascend_env.sh` 的 Step 4。

### 5.3 CANN 版本兼容性

| CANN | vllm | vllm-ascend | 状态 |
|------|------|-------------|------|
| 8.3.RC1 | 0.11.0 | 0.11.0rc1 | ✅ 全链路验证通过 |
| 8.5 | 0.11.0 | 0.11.0rc1 | ❌ HTTP serving 503 |
| 8.5 | 0.14.1 | 0.14.0rc1 | ❌ 编译失败 |
| 8.5 | 0.16.0 | 0.14.0rc2 | ⚠️ 能 serve 但 Python 3.11 only |

**结论：CANN 8.3 + vllm 0.11 是当前唯一稳定的组合。**

### 5.4 transformers 兼容性

transformers 4.57 新增了 `continue_final_message` 和 `add_generation_prompt` 互斥检查。
vllm 0.11 在多轮对话时会同时传这两个参数。需要 patch：

```python
# transformers/tokenization_utils_base.py
if continue_final_message:
    add_generation_prompt = False  # patched
    if False:  # 原来是 if add_generation_prompt:
        raise ValueError(...)
```

### 5.5 代理与网络

- 代理会阻断 git clone（agent 需要 clone GitHub 仓库搭建 testbed）
- 代理也会阻断 vLLM HTTP endpoint 的本地访问
- 解决：训练时不设代理，wandb 用 offline 模式
- git 重试：`git config --global http.retry 3`

### 5.6 hermes tool parser

vllm 0.11 的 hermes tool parser 在以下情况会失败：
- 模型生成的 JSON 有语法错误（缺逗号、未终止字符串）→ 模型能力问题，RL 会改善
- 双重编码的 JSON arguments → 需要在 agent SDK 加防御性 `isinstance(str)` 检查

## 6. SWE-MiniSandbox 适配要点

### 与 codescout 的关键差异

| 维度 | codescout | SWE-MiniSandbox |
|------|-----------|-----------------|
| 训练模式 | 异步（fully_async_trainer） | 同步（trainer.py） |
| Agent | openhands-sdk | SWE-agent |
| 环境 | git clone + /tmp | sandbox (chroot/namespace) |
| Reward | localization F1 | swebench test execution |
| SkyRL 版本 | commit 81e5a97 | 类似时期，略有差异 |

### 适配步骤（预估）

1. **通用 NPU patch**：直接复用 `apply_npu_patches.py`，应用到 SWE-MiniSandbox/SkyRL/
2. **SDPA patch**：同样修改 `model_wrapper.py`
3. **Monkey-patch**：安装 `npu_support/` 到 site-packages
4. **vllm-ascend**：同样的源码构建流程
5. **训练脚本**：参考 `run_async_training_npu.sh` 创建 NPU 版本，
   主要改 HCCL 环境变量 + weight_sync_backend + flash_attn=false
6. **sandbox 环境**：确认 chroot/namespace 在 NPU 机器上正常工作
7. **SWE-agent**：确认 SWE-agent 能通过 vLLM HTTP endpoint 正常交互

### 预期不需要额外适配的部分

- sandbox 隔离（纯 Linux 层面，不涉及 NPU）
- swebench reward 计算（纯 CPU，不涉及 NPU）
- 数据加载（parquet，不涉及 NPU）
- Ray 调度（monkey-patch 已处理 GPU→NPU 资源映射）

### 可能需要额外关注的部分

- SWE-MiniSandbox 的 SkyRL 用同步训练，weight sync 逻辑可能不同
- SWE-agent 的 tool calling 格式可能跟 hermes parser 不完全兼容
- sandbox 的 conda 环境管理可能跟 NPU 环境有冲突

## 7. 参考文件

| 文件 | 位置 | 说明 |
|------|------|------|
| 一键搭建脚本 | `codescout/scripts/setup_ascend_env.sh` | 完整环境搭建 |
| NPU patch 脚本 | `SkyRL/npu_support/apply_npu_patches.py` | SkyRL 源码 patch |
| Monkey-patch | `SkyRL/npu_support/patch_cuda.py` | 运行时透明代理 |
| NPU 训练脚本 | `codescout/scripts/run_async_training_npu.sh` | 训练启动参考 |
| 恢复文档 | `codescout/docs/ascend-adaptation-recovery.md` | 完整恢复指南 |
| 适配状态 | `codescout/docs/npu-adaptation-status.md` | 详细适配记录 |
| Attention benchmark | `SkyRL/npu_support/bench_attn_v2.py` | 性能测试 |


## 8. 容易遗漏的项（Review 补充）

### 8.1 utils/utils.py 代理转发

SkyRL 的 `prepare_runtime_environment()` 负责构建 Ray worker 的环境变量。
需要添加 proxy 变量转发，否则 Ray worker 里的 wandb 等工具无法联网：

```python
# skyrl-train/skyrl_train/utils/utils.py, prepare_runtime_environment() 末尾添加:
for proxy_var in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
                  "ALL_PROXY", "no_proxy", "NO_PROXY"):
    if os.environ.get(proxy_var):
        env_vars[proxy_var] = os.environ[proxy_var]
```

### 8.2 关键环境变量（训练脚本必须设置）

```bash
# CANN 运行时
source /usr/local/Ascend/ascend-toolkit/latest/bin/setenv.bash

# NPU 显存碎片化优化（没有这个长序列会 OOM）
export PYTORCH_NPU_ALLOC_CONF=expandable_segments:True

# HCCL 通信超时（默认太短，weight sync 会超时）
export HCCL_CONNECT_TIMEOUT=360
export HCCL_EXEC_TIMEOUT=360

# 让 SkyRL 把 LD_LIBRARY_PATH 和 PYTHONPATH 转发到 Ray worker
# 没有这两个，Ray worker 找不到 CANN 的 .so 文件和 Python 包
export SKYRL_LD_LIBRARY_PATH_EXPORT=1
export SKYRL_PYTHONPATH_EXPORT=1

# vllm-ascend 特定
export VLLM_ASCEND_ENABLE_NZ=0

# 清除代理（代理会阻断 git clone 和 vLLM HTTP 本地访问）
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY
```

### 8.3 fully_async_trainer.py 的 weight sync 调试开关

仅用于 async 训练模式（codescout 用，SWE-MiniSandbox 的同步模式不需要）。
添加了 `SKYRL_SKIP_WEIGHT_SYNC=1` 环境变量，设置后跳过 weight sync，
用于排查 weight sync 导致的 vLLM 引擎崩溃问题。

```python
# skyrl-train/skyrl_train/fully_async_trainer.py
# 在 initial weight sync 和 per-step weight sync 处添加:
import os
if os.environ.get("SKYRL_SKIP_WEIGHT_SYNC", "0") == "1":
    logger.info("Skipping weight sync (SKYRL_SKIP_WEIGHT_SYNC=1)")
else:
    with Timer("sync_weights_to_inference_engines"):
        await self.async_sync_policy_weights_to_inference_engines()
```

### 8.4 apply_npu_patches.py 的覆盖范围

当前 `apply_npu_patches.py` 只处理 device patch（cuda→npu 等），
以下改动需要手动应用或扩展脚本：

| 文件 | 改动 | 是否在 apply_npu_patches.py 中 |
|------|------|-------------------------------|
| distributed/fsdp_strategy.py | cuda→npu | ✅ |
| distributed/fsdp_utils.py | cuda→npu | ✅ |
| distributed/strategy.py | cuda→npu | ✅ |
| workers/fsdp/fsdp_worker.py | cuda→npu | ✅ |
| workers/worker.py | nccl→hccl, GPU→NPU | ✅ |
| inference_engines/vllm/vllm_engine.py | cuda→npu, nccl→hccl | ✅ |
| model_wrapper.py | flash_attn import | ✅ |
| **model_wrapper.py** | **eager→sdpa** | **❌ 需要手动** |
| **utils/utils.py** | **proxy 转发** | **❌ 需要手动** |
| **fully_async_trainer.py** | **weight sync 开关** | **❌ 需要手动** |

`setup_ascend_env.sh` 中已包含 SDPA patch 的自动化，但 utils.py 和
fully_async_trainer.py 的 patch 未包含。SWE-MiniSandbox 用同步训练，
fully_async_trainer.py 不需要，但 utils.py 的 proxy 转发仍然需要。


### 8.5 训练前清理（每次启动前必做）

```bash
# 1. 停止残留进程
ray stop --force 2>/dev/null
pkill -9 -f "vllm\|VLLMEngine\|EngineCore\|rayFSDP\|ray::" 2>/dev/null
sleep 5
npu-smi info  # 确认所有 NPU 无进程

# 2. 清理 NPU kernel 编译缓存（偶尔导致启动异常）
rm -rf /path/to/workspace/workspace/kernel_meta* 2>/dev/null

# 3. 清理 pyc 缓存（patch 源码后必须，否则旧 pyc 覆盖新代码）
find SkyRL/skyrl-train -name "__pycache__" -exec rm -rf {} + 2>/dev/null
```

### 8.6 conda 环境快照

没有 `environment.yml`。如需导出当前环境：
```bash
pip list --format=freeze > codescout/requirements-ascend.txt
```
恢复时用 `setup_ascend_env.sh` 而非 `pip install -r`，因为安装顺序和 `--no-deps` 很重要。

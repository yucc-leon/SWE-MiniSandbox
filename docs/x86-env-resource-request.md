# SWE-agent 训练/评测 — x86 环境机器资源需求

SWE 评测/训练的瓶颈是**每个任务的可执行环境复现**，公开生态（SWE-bench / Pro / swesmith / R2E）的镜像与脚手架都是 **x86 原生**，ARM 上重建成本极重。故采用 **NPU 训练/推理 + x86 环境构建/评测** 分离架构，x86 侧直接复用预构建镜像。

---

## 给 PZ：风险与时序

- **近期出结果的路径（SFT warmup + 评测）是扎实的**：跨架构只传文件 + 一次无状态推理调用，无紧耦合循环。先给 2 台即可跑通评测、拿基线分。
- **唯一的真不确定性在 RL**：需要 NPU policy ↔ x86 sandbox 的实时闭环。这要新建「远程 sandbox 传输层」（现无），且 vLLM-ascend 作外部并发推理端点未验证——**属 phase-2，预留开发时间，不阻塞近期结果**。
- **GPU 能消除这条风险**：若给 GPU，policy + sandbox 同在 x86/GPU 机房走成熟 CUDA 栈，RL 跨架构接缝直接消失。GPU 非阻塞起步，但强烈建议。
- **节奏**：先批 2 台 + NFS 出基线分 → RL 上量再扩到 4 台。

---

## 给运维：要提供什么

### 1. 机器

| 阶段 | 机器 | 配置 |
|---|---|---|
| **起步（先批）** | eval ×1 | 32–64 vCPU / 256 GB / 2 TB NVMe |
| | env-factory ×1 | 32–64 vCPU / 256 GB / 2 TB NVMe |
| | 共享 NFS | 4–8 TB |
| 后续 RL 上量 | 补到 4 台 | 64 vCPU / 256 GB / 2 TB NVMe + NFS 扩容 |
| 可选（强烈建议） | GPU | 4B SFT 2–4 张 / RL 4–8 张，有多少用多少 |

### 2. 每台环境与权限

- Ubuntu 22.04 x86_64；Docker 24+；Python 3.11/3.12；conda 或 uv；git
- env-factory 另装 Node.js + TS（tsx / vitest）
- **root 或 docker 组**权限
- **Docker 存储（`/var/lib/docker`）放本地 NVMe，不要放 NFS**（overlay2 在 NFS 上不支持）；NFS 仅作镜像/数据中央缓存

### 3. 存储（约 3 TB+ 常驻）

Verified 镜像 ~1 TB+、Pro 镜像 ~1 TB+、swesmith 数百 GB、R2E 数百 GB、轨迹数据 几十 GB。→ 每台 2 TB 本地盘 + 4–8 TB 共享 NFS。

> 🔴 共享 NFS 须**新开专用卷**，不要挂现有 `/sharedata`（630 T 已用 99%、仅余 ~7.7 T 且高度争用，放不下 3 TB+ 镜像）。

### 4. 网络 / proxy

- proxy 放行：`huggingface.co`、`hub.docker.com` + `*.docker.io`、`github.com`、`pypi.org` + `files.pythonhosted.org`
- **Docker registry 大流量可达**（首次拉 ~2 TB+ 镜像是主要启动延迟；量大时建议内网 mirror）
- （RL 阶段）NPU↔x86 同内网可达 + 端口开通

### 5. 要拉的镜像/数据集（已核实）

| 用途 | HuggingFace 数据集 | Docker 镜像 | 代码 |
|---|---|---|---|
| SWE-bench Verified（评测主对标）| `princeton-nlp/SWE-bench_Verified` | DockerHub `swebench/sweb.eval.x86_64.*`（harness 自动拉）| `github.com/SWE-bench/SWE-bench` |
| SWE-bench Pro（评测 TS/JS 业务，必需）| `ScaleAI/SWE-bench_Pro` (split=test) | DockerHub `jefzda/sweap-images`（按样本 `dockerhub_tag`）| `github.com/scaleapi/SWE-bench_Pro-os` |
| swesmith（任务构建）| `SWE-bench/SWE-smith` | DockerHub `jyangballin/swesmith.x86_64.*` | `github.com/SWE-bench/SWE-smith` |
| R2E-Gym（任务构建）| `R2E-Gym/R2E-Gym-*` | repo 内 | `github.com/R2E-Gym/R2E-Gym` |
| SFT warmup 数据 | `nvidia/SWE-Zero-openhands-trajectories` | — | — |

注：Pro 公开集 731 实例 / Python+Go+TS+JS；其 repo 为 **GPL，仅用于评测，不入训练集**。

**一键拉取**：`bash sh/pull_env_images.sh --dry-run`（先出去重镜像清单 `env_images_manifest.txt` 供白名单/内网 mirror）→ `bash sh/pull_env_images.sh`（真拉 Verified+Pro+swesmith；swesmith 去重后 222 个镜像）。⚠️ `docker pull` 走 **daemon proxy**，非 shell http_proxy——受限出网须先配 daemon proxy 或内网 mirror。

### 6. 验收（机器到手自测，过即达标）

1. `docker pull swebench/sweb.eval.x86_64.<任一实例>` 成功 → registry/proxy/盘通。
2. **核心**：gold patch 在 Verified 上跑 5–10 个实例 **100% resolve**：
   `python -m swebench.harness.run_evaluation --dataset_name princeton-nlp/SWE-bench_Verified --predictions_path gold --instance_ids <…> --run_id smoke --max_workers 4`
3. `--max_workers 24` 跑一批不 OOM → 验证 24+ 并行容器。
4. Pro 自检：`scaleapi/SWE-bench_Pro-os` 的 `swe_bench_pro_eval.py` + `--dockerhub_username=jefzda`，gold 跑几个 TS/JS 实例链路通。

> 达标线：gold 在 Verified + Pro 上 100% resolve，且稳定跑 24+ 并行容器。

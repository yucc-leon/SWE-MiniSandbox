# RL 闭环状态(2026-06-29)

## 🎉 闭环已闭合(12:26,task 7739 任务成功)

完整 SFT→RL 链路端到端跑通:rollout(SWEsbEnv 部署 → vllm-ascend 生成 → bash 动作 → patch)→ reward(修好的 scorer)→ GRPO 更新(global_step 1,2)→ checkpoint 保存 + 模型导出(`/path/to/root/exports/global_step_3/policy`)。**容器无关 SWE-RL 在 Ascend 上端到端 work。**

**诚实保留:这一轮所有 reward=0**(4B 在 8 个 tiny 实例上解出 0)→ GRPO 优势=0 → 无真正学习信号。机械闭环成立,但要看到学习需 reward 方差(更易的实例 / 更大 n_samples / 用 SFT 冷启动模型而非 base)。

### 从 round-10 转向后的最终修复链(有了 SkyRL 写权限)
A. venv pip 坏 → 清 rl-run 池 + NROLL=1 干净重解压。
B. modal → 带 deps 装。
C. **'choices' KeyError → 加诊断日志(inference_engine_client.py:692)捕获到 vllm-ascend 真实错误**:`"auto" tool choice 需要 --enable-auto-tool-choice + --tool-call-parser` → 加 `engine_init_kwargs.enable_auto_tool_choice=true` + `tool_call_parser=hermes`(codescout 脚本本有)。
D. cost-tracking RuntimeError → `MSWEA_COST_TRACKING=ignore_errors`。
E. 空 rollout IndexError → generator 加健壮性护栏(无 assistant 轮返回 None,不崩整批)。
F. **根因:n_assistant=0(全 user 消息)→ 默认 LitellmModel 是 tool-calling 但模板是 text-based,不匹配 → 无可解析动作。修复 = yaml model 段加 `model_class: litellm_textbased`**(与 gen 一致)。+ 'exit' role 过滤。

---

## 〔历史〕过夜推进记录(已被上面闭合,留档)

> 目标:把 SFT→RL→eval 闭环在 NPU 上跑通(tiny run,8 实例,Qwen3-4B,GRPO)。
> 结论:**框架层全部打通,我们的 container-free 沙箱已在训练进程内成功部署;剩下是 agent↔vllm 接线 + 并发/格式的多面集成调试,留待有连续注意力时攻(建议你在场,因含设计决策)。**

## 已跨过的里程碑(9 轮调试,全部验证)

| 层 | 状态 | 关键修复 |
|---|---|---|
| 脚本/import | ✅ | no_proxy(set -u)、save_traj stub、generator 绝对导入 |
| **vllm-ascend 推理** | ✅ 引擎就绪 | 去 expandable_segments;`colocate_all=false`(绕开缺失的 sleep-mode 内核);**GPU KV cache ~291k tokens** |
| **FSDP 训练侧** | ✅ init OK | `use_sample_packing=false`(NPU 无 flash_attn) |
| Ray / HCCL | ✅ | RAY_OVERRIDE_RESOURCES 解 tar_io 挂死 |
| **我们的 container-free 沙箱** | ✅ **部署成功** | 部署栈依赖一次装齐;**"Environment Initialized" ×6,0 个 no-valid-responses** |

**重大意义**:整条 RL 基础设施(NPU 上 vllm-ascend + FSDP GRPO + Ray)+ 我们独有的容器无关沙箱在训练进程内部署,都验证可跑。这是"跑通"的主体。

## ✅ 更新(第 10 轮):agent↔策略端点接线已解决

`#1` 修好了(AuthError 归 0)。根因:我精简 tiny 脚本时漏了 `generator.enable_http_endpoint=True`(上游 8B 脚本有)。修复 = 加回 `enable_http_endpoint=True/http_endpoint_host=127.0.0.1/http_endpoint_port=8080` + run 脚本 export `OPENAI_BASE_URL=http://127.0.0.1:8080/v1`、`OPENAI_API_KEY=dummy`。**agent 现在能够到被训练的策略模型。** 这是闭环里最关键的设计点,已通。

## ✅ 第 11 轮更新:A+B 解决,闭环跑到"生成请求"

- **A(venv pip 损坏)已解决**:清掉被早期并发竞争弄坏的 rl-run 池 + NROLL=1 干净重解压 → `build_env` 错误归 0。
- **B(modal)已解决**:带 deps 装 modal → 错误归 0。
- **agent↔策略端点 + 沙箱部署全通**:Environment Initialized,agent 真的发出了 `/chat/completions` 请求到 SkyRL 端点。
- **exit role 已修**:mini-swe-agent 结束追加的 'exit' role 消息,在 generator 的 init_and_run 返回前过滤(只留 system/user/assistant)。

## 🔴 最后的阻塞:vllm-ascend chat_completion 返回无 'choices'(需引擎级调查)

- 现象:agent 每个 `/chat/completions` 请求 → `litellm.InternalServerError: ... in SkyRL: 'choices'`。
- 根因定位(已读代码):`inference_engine_client.py` 的 `_parse_partial_response_and_inplace_update_accum` 访问 `partial_response["choices"]` KeyError ——即 **vllm-ascend 引擎的 chat_completion 返回了一个错误响应(没有 choices 字段),SkyRL 解析器没优雅处理**。引擎本身健康(init/KV cache/weight-sync 都正常),是它**拒绝了这个具体请求**。
- **下一步(需引擎级 debug)**:① 开 `RAY_DEDUP_LOGS=0` + SkyRL inference client 的 DEBUG 日志(line 317 有 `logger.debug` 打印请求体),或在 `_parse_partial_response` 前 log `partial_response`,捕获 vllm-ascend 实际返回的错误体;② 对比上游 mini_swe(CUDA)能跑的请求差异——疑点:mini-swe-agent litellm 发的 model 名/某 sampling 参数/chat template 被 vllm-ascend 拒。这是 vllm-ascend↔SkyRL 的响应格式/请求兼容问题,**建议带引擎日志专门调一轮**,而非盲改。

## 旧的剩余阻塞记录(A/B 已解决,留档)

- **A. venv pip 损坏(核心)**:env setup 的 relink 步 `pip install -e .` 在新建 rl-run venv 池里报 `ModuleNotFoundError: pip._internal.build_env`——该 venv 自带的 pip 版本坏/不兼容。修向:重建该实例的 shared_venv,或 relink 前 `python -m ensurepip --upgrade`/换用 conda 后端的 pip,或复用已验证好的 gen 池 venv(glm-904a2 等)而非新建 rl-run 池。
- **B. `No module named 'modal'`**:tp 任务里 `--no-deps` 装 modal 导致它缺自身依赖、import 失败。修向:`pip install modal`(带 deps)或在 swerex 部署里把 modal 设为可选 import(它只是可选部署后端)。

## 旧记录(部分已被上面更新)

1. **【最关键·含设计决策】agent↔vllm 端点接线**:rollout 里 mini-swe-agent 的 DefaultAgent 用 litellm 调"被训练的策略模型",报 `AuthenticationError: api_key must be set`。精确诊断(已查代码):
   - generator 在 `__init__` 算了 `self.base_url=http://{http_server_inference_engine_client_host}:{port}`(默认 `127.0.0.1:8000`),但 `init_and_run` 里 `get_model(litellm_model_name, model_config)` **没把 base_url/api_key 注入** model_config,litellm 默认去打真 OpenAI → AuthError。
   - **端口不匹配**:我 run 脚本设 `generator.http_endpoint_port=8080`,但 generator 读 `generator.http_server_inference_engine_client_port`(默认 8000)。要统一。
   - 上游靠 `--env-file .env.miniswe` 设 `OPENAI_BASE_URL`/`OPENAI_API_KEY`,但我们复制来的 `.env.miniswe` 是**空的**。
   - **修法(早上一击)**:在 `minisandbox_swesmith.yaml` 的 `model.model_kwargs` 里塞 `api_base=http://127.0.0.1:<port>/v1` + `api_key=dummy`(或在 run 脚本 export `OPENAI_BASE_URL`/`OPENAI_API_KEY`),并让 `http_endpoint_port` 与 `http_server_inference_engine_client_port` 一致。**设计点:策略模型怎么 serve 给 rollout 里的 agent(SkyRL 的 http_endpoint vs client port)。**
2. **shared_venv 并发竞争**:`n_samples_per_prompt=4` 让同一实例 4 个 rollout 并发解压**同一个** venv tar → `Directory not empty`/`No such file`。我们单机 gen 用独立 root_dir 避开了,但 RL 里共享 shared_venv 根。修法:每 rollout 独立 venv 目录,或加解压锁,或预解压。
3. **message role 格式**:`Expected message role 'user'/'assistant', got X`——轨迹里有 system/tool role 没被规整。
4. **venv 内 pip 损坏**:relink 时 `pip._internal.build_env` 缺失(SWESMITH_FULL_DEPS 重装路径)。

## 怎么接着干(给你早上)

- **快验**:先只修 #1(agent 端点接线)+ 把 `n_samples_per_prompt=1`(暂避 #2 并发),看能否单实例 rollout 走通 → reward → 一步 GRPO。这是最小闭环。
- 再逐个解 #2/#3/#4 放大。
- 全部文件就位:`sh/rl/mini_swe_rl/`(generator/utils/main/yaml/run 脚本)、`sh/rl/tp_rl_run.yaml`(装依赖+启动)、`vendor/rl-data/{train,val}.parquet`(8+8)。
- 调试日志:`vendor/rl_run_top.log`(bash -x 全程)。
- 记忆 [[rl-skyrl-wiring-spec]] 有完整 9 轮调试链。

## 一句话
> RL 不是"没跑起来",是"跑到了我们自己 env 的最后一段集成";框架全通、沙箱能部署,差 agent↔策略端点接线 + 并发/格式收尾。建议你在场攻 #1(它是"策略怎么 serve 给 rollout"的设计决策)。

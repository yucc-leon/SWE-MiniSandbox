# 过夜进展报告 — 2026-06-17 → 06-18 早

目标(你定的):用强模型跑 SWE-bench Verified,**对齐评测、检验脚手架+运行环境是否还有偏差**;chroot 忠实;单 8 卡 pod 串行;失败自动回退。

---

## ★ 头条结果(早上先看这个）

**训练前忠实基线(SWE-bench Verified :100 切片,chroot `/testbed`,mini-swe-agent bash 脚手架):**
| 模型 | resolved | 备注 |
|---|---|---|
| **Qwen3-8B** | **7%** (7/100) | 完整 100,清洗后 error=9 |
| Qwen2.5-14B-Instruct | 5% (5/100) | 生成 96,非空仅 33(偏保守),error=19 |

洞察:**Qwen3-8B(更新更小)反超 Qwen2.5-14B(更大更老)**——未经 SWE 训练时模型代际 > 参数量;两者都低(5-7%)正说明 **SFT 才是真正的杠杆**。两者都跑在已验证的单卡 chroot 路径(绕开 30B 多卡 bug)。

**过程中抓到并修了一个真·管线 bug(正是你要的"环境/管线偏差"排查成果):**
- gen 用 `git add -A && git diff` 采 patch → 把 agent 写进仓库的草稿文件(patch.txt 等)污染进 patch → 判分时大面积应用失败。
- 表现:污染版判分 resolved=2/100、error=66/100;**修复(改 `git diff HEAD` 只采已跟踪改动)+ 清洗后:resolved=7/100、error=9/100**。一个 bug 把分数压低了 3.5 倍。
- 也说明"非空 patch 率"会被草稿文件虚高(64 非空里 21 个是纯草稿,真实 43 个有源码改动)。

## TL;DR

1. ✅ **生成端验证通过**:mini-swe-agent(bash 脚手架)在 **chroot `/testbed`** 里稳定产**非空 patch**(脚手架对、环境无偏差阻塞)。⚠️ **纠错**:`run_minisweagent_gen.py` 只**生成 patch、不判分**(我先前哨兵里 `reward` 字段不存在 → "resolved=0" 是假象,不是真实评分)。真实 Verified 分数要跑**单独判分步骤**(`run_swebench_scoring_ascend.sh` + chroot 判分配置,gold 5/5 已验证)——见下进行中项。
2. ⚠️ **30B 多卡受阻**(特权 pod + vllm_ascend 的真实局限,详见下)。**需要你拍板** 走哪条解法。
3. 🔄 **进行中**:Qwen3-8B 单卡 chroot 在 Verified :100 上跑**训练前忠实基线**(任务 6837)。早上看结果。
4. ✅ **swesmith 判分线彻底收口**:健壮、无假阳性;产出率取决于实例数据完整性。

---

## 1. 验证结果(已确认)

- **脚手架**:之前用 SWE-agent 的 XML 脚手架跑香草模型 → 30/30 `exit_format`、全空 patch(模型产不出那个格式)。换 **mini-swe-agent(bash,模型无关)** 后:Qwen3-4B 单卡 chroot :30 → **19/30 非空 patch**。脚手架修对了。
- **环境**:chroot `/testbed` 构建 + pytest 判分跑通(swebench gold 5/5 早已验证;swesmith reward 也验证会区分对错)。**没发现环境偏差阻塞**。
- 4B 在 :30 上 resolved=0(4B 在 Verified 上本就极弱,远低于 7B 的 ~10%;验证的是管线不是分数)。

## 2. 🚧 30B 多卡受阻(需你决策)

**根因(读 vllm_ascend 源码确认)**:chroot 必须用**特权 pod**;特权 pod 看得到宿主机所有 NPU(k8s 设备插件不做隔离重编号)。vllm_ascend 的多进程 worker(`worker/worker.py:_init_device`)直接 `torch.npu.set_device(npu:{local_rank})`,在特权环境下把 local_rank 当**物理绝对卡号**(抢物理 0..N-1=别人的卡)→ `aclInit 107001 Invalid device ID`。**单卡(TP1)能用**(worker 在引擎进程内、按 `ASCEND_RT_VISIBLE_DEVICES` 重映射);**TP≥2 必中此 bug**。这套环境**之前从没跑过特权多卡**,是新坑。

我已修的(serve 进程里把 `ASCEND_RT_VISIBLE_DEVICES` 钉成分配卡)能让 env 非空,但 worker 仍按 local_rank 抢绝对卡——**根因在 vllm_ascend 的 worker,不在我们的脚本**。

**两条解法,选一个:**
- **A) 解耦**(推荐,保持 chroot 忠实):非特权 pod 多卡服务 30B(已验证非特权多卡能跑)+ 特权 0 卡 pod 跑 chroot 客户端(只建环境+判分+调远程 API,不需 NPU),走 `run_sweagent_formal_remote_infer.sh`。**唯一未知**:跨 pod 网络可达性(需 hostnetwork/pod IP)。我没在无人值守下赌它烧 8 卡。
- **B) 补丁 vllm_ascend**:改 `worker.py:_init_device` 让它走 `device_id_to_physical_device_id(local_rank)` 映射。**风险**:它是 editable 共享安装,你 KTAscendRL 等并发任务也在用,改了可能波及——**所以我没擅自改**。

**单卡仍可用**:塞得下 1 卡(64GB)的模型(≤~14B)走单卡 chroot 没问题(就是 6837 在做的 8B)。

## 3. 🔄 进行中:Qwen3-8B 单卡 chroot 基线(任务 6837)

为什么 8B:30B 多卡受阻;8B 是**我们要 SFT 的目标量级**,跑它拿到的就是**训练前忠实基线**。走已验证单卡路径,无多卡 bug。`:100` 切片。
**流程是两步**:① 生成(6837,产 100 个 patch,8B 约 48/73 非空)→ ② **判分**(对 preds.json 跑 `run_swebench_scoring_ascend.sh` + chroot 配置,拿真实 resolved%)。6837 生成完我接判分,早上给真实分数。
(注:全量 500 单卡跑不完一夜;要全量得靠多卡=30B 解耦,或接环境缓存加速。)

## 4. ✅ swesmith 判分线(收口)

4 个全局加固(都在 `sandbox_deployment.py` / `serve_qwen_ascend.sh` / `customer_instance.py`):
- depth fetch 5(拿到含测试的 `HEAD^`)+ reset 从 `HEAD^` 恢复被"Remove F2P Tests"删掉的考核测试;
- install 钉 `'pytest>=7,<8'` + pytest-cov(避开 pytest 9 钩子崩 + --cov 缺插件);
- `--continue-on-collection-errors` + **缺失测试文件过滤**(避免缺文件整体崩)。
验证:gold→1 / buggy→0 会区分(无假阳性);8 个零依赖仓库泛化 = **0 个 F2P 回归**(核心 depth/HEAD^ 修复通用);剩余 reward=0 = 个别实例引用了镜像里缺失的测试 → **数据本身不全、无法判分(正确判 0)**。采样时按"引用测试齐全"筛实例即可。

## 5. 改动的文件(本地,符合"本地改码/pod 跑实验")

- `SWE-agent/sweagent/environment/repo.py`(tar 解压清残留+stderr;swesmith prefetch depth 5)
- `sandboxdev/swesandbox/sandbox_deployment.py`(reset HEAD^;install pytest 钉版本+cov;test-cmd 加固+缺文件过滤;debug 开关)
- `sandboxdev/swesandbox/utils.py`(tar_extract chmod 可写)
- `sandboxdev/swesandbox/customer_instance.py`(oauthlib 补 cryptography)
- `sandboxdev/swesandbox/install_script.py`(chardet 离线 wheelhouse)
- `sh/serve_qwen_ascend.sh`(ASCEND_RT 设备 pinning + 日志;MoE 优化开关 EP/async/perf;eager 可配)
- `sh/run_minisweagent_gen.sh`(多卡 TP 服务分支)
- `SWE-agent/.../swe_sbenv.py`(`SWE_SANDBOX_PRECHECK_NO_GOLD` 诊断开关)
- 新增 YAML/脚本:`sh/tp_eval_*.yaml`、`sh/screen_swesmith_repos.sh`

## 6. 早上待决 / 建议下一步

1. **30B**:选 A(解耦,我来调跨 pod 网络)还是 B(授权我补 vllm_ascend worker.py)?
2. **8B 基线(6837)**:看结果;要不要拼环境缓存跑全量 500。
3. 然后进主线:teacher 采样(swesmith 训练集,按数据完整性筛实例)→ reward 过滤 → SFT 8B → 评测 lift。

(详细技术细节见 memory `ascend-eval-serve.md` / `sft-data-and-recipes.md`。)

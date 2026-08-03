# 任务:从 SWE-smith 官方镜像批量导出精确环境规格

## 目标
SWE-smith 每个 repo 的完整测试环境只存在于其官方 x86 docker 镜像里(build 时 `conda env create` 装好,依赖清单未发布)。请在有 docker 的机器上,把每个唯一镜像内的环境规格导出成文本,打包回传。我们将用它在无 docker 机器上精确复原 venv。

## 输入:唯一镜像清单
```python
from datasets import load_dataset
ds = load_dataset("SWE-bench/SWE-smith", split="train")
images = sorted(set(ds["image_name"]))
```
镜像名形如 `jyangballin/swesmith.x86_64.{owner}_1776_{repo}.{commit8}`(约 100–300 个,全部都要)。

## 每个镜像导出内容
镜像内约定:conda 在 `/opt/miniconda3`,环境名 `testbed`,仓库在 `/testbed`。

```bash
docker run --rm <image> bash -lc '
  source /opt/miniconda3/bin/activate testbed
  python -V
  pip freeze
  conda env export
  conda list --explicit
  dpkg -l'
```
五段输出分别存为独立文件(见下),另用 `docker inspect --format "{{index .RepoDigests 0}}" <image>` 记录 digest。

## 输出布局
```
envdump/
  manifest.jsonl                 # 每镜像一行:{"image":..., "digest":..., "python_version":..., "ok":true/false, "error":null|str}
  jyangballin__swesmith.x86_64.<owner>_1776_<repo>.<commit8>/   # 镜像名中 / 换成 __
    python_version.txt
    pip_freeze.txt
    conda_env.yml
    conda_explicit.txt
    dpkg.txt
```
完成后整体打包 `envdump.tar.gz` 回传。

## 约束
1. **磁盘控制**:逐个 `docker pull → 导出 → docker rmi`,不要同时留存全部镜像(单个 1–3GB)。
2. **失败不中断**:某镜像 pull 失败/环境结构不符,记入 manifest(`ok:false` + error)继续下一个;若 `testbed` env 不存在,回退用镜像默认 `python`,并在 error 里注明 "no testbed env"。
3. **幂等**:输出目录已存在且含 `conda_env.yml` 的镜像直接跳过,支持中断重跑。
4. 并发 pull 不超过 2,避免打满带宽/触发 Docker Hub 限流(匿名拉取有 rate limit,若遇 429 可 `docker login` 后重试)。

## 验收
- `manifest.jsonl` 中 ok 数 / 总数(预期 ok ≥ 95%);
- 抽查 3 个 repo 的 `conda_env.yml`:应包含 pytest 及若干 pytest-* 插件与版本 pin;
- 回传 tar.gz + manifest 的失败清单摘要。

# 任务:审计 SWE-smith 镜像里"刻意 apt 装的系统包"(交叉验证)

## 背景 / 为什么
我们已从 x86 镜像导出每个环境的 `pip freeze`(见 envdump),用它在 aarch64 无容器下重建 Python 依赖。但**系统层的 apt 包不在 pip freeze 里**。从 swesmith 的 `profiles/python.py` 代码里已定位出 6 个 repo 刻意 apt 装了系统工具(见文末清单)。本任务用 docker 独立**交叉验证这 6 个是不是全集**——因为我们手里的 `dpkg -l` 分不清"刻意装的"和"被 pip 依赖自动拉进来的",而 `apt-mark showmanual` 能区分。

**目标**:列出每个镜像中"超出基线的、刻意 apt 安装的系统包"。预期只有那 6 个 repo 会有输出;若冒出第 7 个,就是代码审计漏掉的卡点,正是要抓的。

## 输入
- `image_list.txt`:222 个镜像名(每行一个),已随附(源:`vendor/envdump_image_list.txt`)。形如
  `jyangballin/swesmith.x86_64.<owner>_1776_<repo>.<commit8>:latest`

## 要跑的命令
```bash
# 1) 基线:funcy 镜像确定没有 per-repo apt,用它的 manual 集合做基准
docker pull jyangballin/swesmith.x86_64.suor_1776_funcy.207a7810:latest
docker run --rm jyangballin/swesmith.x86_64.suor_1776_funcy.207a7810:latest \
  bash -lc 'apt-mark showmanual | sort' > base_manual.txt

# 2) 逐镜像 diff,列出超出基线的"刻意装的系统包"
: > manual_apt_delta.txt
while read -r img; do
  [ -z "$img" ] && continue
  docker pull -q "$img" >/dev/null 2>&1 || { echo "$img : PULL_FAIL" >> manual_apt_delta.txt; continue; }
  cur=$(docker run --rm "$img" bash -lc 'apt-mark showmanual | sort' 2>/dev/null)
  extra=$(comm -13 base_manual.txt <(echo "$cur") | tr '\n' ' ')
  [ -n "$extra" ] && echo "$img : $extra" >> manual_apt_delta.txt
  docker rmi "$img" >/dev/null 2>&1   # 控磁盘:用完即删
done < image_list.txt

echo "=== 有额外 apt 系统包的镜像 ==="
cat manual_apt_delta.txt
```

## 约束
- **磁盘**:逐个 `pull → 跑 → rmi`,别同时留全部镜像(单个 1–3GB)。基线 funcy 镜像可保留。
- **限流**:匿名 pull 有 rate limit,遇 429 先 `docker login` 再继续;并发保持 1。
- **失败不中断**:pull 失败记 `PULL_FAIL` 继续下一个。
- **可中断重跑**:已在 `manual_apt_delta.txt` 里的镜像可跳过(如需断点续跑,先 grep 已完成的镜像名过滤 image_list)。

## 回传
- `manual_apt_delta.txt`(核心产物,通常只有几行)
- `base_manual.txt`(基线,便于我复核)

## 验收 / 我这边怎么用
把 `manual_apt_delta.txt` 发我。我用它交叉核对下面这份**代码审计出的 6 个 repo**:
| repo | commit8 | 期望出现的额外系统包 |
|---|---|---|
| pdfplumber | 02ff4313 | ghostscript |
| pipdeptree | c31b6418 | graphviz |
| scrapy | 35212ec5 | libxml2-dev libxslt-dev libjpeg-dev |
| hydra | 0f03eb60 | openjdk-17-jdk openjdk-17-jre |
| pydantic | acb0f10f | locales pipx |
| conan | 86f29e13 | build-essential cmake automake autoconf pkg-config meson ninja-build |

- 若 delta **只覆盖这 6 个**(或其子集)→ 确认系统层卡点已完全枚举,envdump(pip) + 这 6 条 apt 命令即可补全,aarch64 重建路径打通。
- 若 delta **出现第 7 个 repo** → 代码审计漏项,我把它的 apt 命令补进沙盒注入(chroot 是 privileged,能跑 apt)。

> 注:`apt-mark showmanual` 可能把基础镜像自带的少量 manual 包也带出;用 funcy 做基线正是为了把这些消掉,只留 per-repo 的净增量。

# Repository Guidelines

## Project Structure & Module Organization
This repository is a workspace centered on the top-level MiniSandbox code, with several embedded upstream projects alongside it. Treat `sandboxdev/swesandbox/` as the primary local package: it contains sandbox deployment logic, dataset helpers, and SWE-bench utility code. Root-level tests live in `tests/` and currently focus on `no-chroot` behavior. Documentation sources are in `docs/` with site configuration in `mkdocs.yml`. Shell setup scripts live in `sh/`, while directories such as `SWE-agent/`, `SWE-ReX/`, `SWE-smith/`, `SWE-bench/`, `SkyRL/`, and `R2E-Gym/` are dependency trees or adapted upstream components and should be changed only when the task explicitly requires it.

## Build, Test, and Development Commands
- `cd sandboxdev && pip install -e .`: install the local `swesandbox` package in editable mode.
- `python tests/test_nochroot.py`: run the root smoke tests for path rewriting and shell startup logic.
- `python tests/test_nochroot_integration.py`: run the integration tests against `swerex.runtime.sandbox.LocalRuntime`; requires the extra runtime dependencies noted in the test header.
- `mkdocs serve`: preview the documentation site locally.
- `mkdocs build`: build the static docs to verify navigation and page rendering.

## Coding Style & Naming Conventions
Python uses 4-space indentation, module-level functions, and snake_case names for files, functions, and variables. Preserve the existing style in `sandboxdev/swesandbox/`: explicit imports, type hints where already used, and short docstrings on public helpers. Keep new modules focused and avoid cross-editing vendored subprojects unless necessary. No top-level formatter or linter is configured here, so keep changes small, readable, and consistent with nearby code.

## Testing Guidelines
Add or update root tests in `tests/` for behavior that changes in the local sandbox package. Follow the existing `test_*.py` naming pattern and name test functions `test_*`. Prefer fast smoke coverage first, then add an integration case when runtime/session behavior changes. If a test depends on external packages or Linux namespace features, state that clearly in the file header.

## Commit & Pull Request Guidelines
Recent history favors short, imperative subjects, often with prefixes like `feat:` and `test:`. Use that format when practical, for example `feat: add no-chroot fallback logging`. Keep commits scoped to one change. Pull requests should describe the affected area, list validation commands run, link any related issue, and include screenshots only for documentation or UI changes.


### Context Hub（认知蒸馏）
- 对话结束时（或用户说"收工"/"结束"），按 `.context-hub/SCHEMA.md` 格式提取 1-3 条 observation
- 写入 `.context-hub/observations/<project>/YYYY-MM-DD.md`（把 `<project>` 替换成当前项目名，如 `synzh`）
- 只记录有判断含量的内容（做了什么决策、为什么），不记录纯执行动作
- 优先记录项目本身的判断（技术选择、质量策略、架构决策等）；关于工具/流程/基础设施的 meta 观察每天最多 1 条
- 主动关注 [失败] 类 observation——翻车、回退、预期落空的判断对长期蒸馏价值最高，不要只记"做对了什么"
- 做判断时可参考 `.context-hub/axioms/axioms.md` 中的原则（只读，不要修改）
- 写完后提醒用户运行 `cd .context-hub && bash sync.sh` 同步

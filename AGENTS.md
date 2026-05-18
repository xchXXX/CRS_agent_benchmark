## 职责
只负责benchmark的构建，不允许修改项目中任何前后端的代码
## 交互语言（强制）
- Codex 在本仓库的所有交互内容必须使用中文（包含需求讨论、方案说明、变更说明、PR描述等）。

## 流程路由（按需触发）
- 双窗口规划委派：`$change-flow-plan-delegate`
- 单窗口严格执行：`$change-flow-strict-exec`
- 未触发上述 skill 时，按默认轻量协作流程执行（不强制重流程）。

## 项目结构与模块隔离（强制）
- 模块功能必须隔离：不同功能模块使用独立文件夹，各自开发、各自测试与调试。
- 文档与代码必须分离：
  - 所有文档统一放在 `docs/` 下，并按功能模块隔离。
  - 所有代码统一放在 `modules/` 下，并按功能模块隔离。
  - `modules/` 下禁止放模块文档（例如 README、设计说明、契约等），模块文档必须放在 `docs/modules/<module-name>/`。
- `docs/modules/<module-name>/` 必须按用途分层：
  - `contract/`：数据契约（字段/Schema/DTO/示例 payload）
  - `implementation/`：实现逻辑说明（流程、时序、关键规则/算法；不放代码）
  - `implement/`：工程落地与实施指南（不放代码），必须包含：
    - `logic/`：逻辑拆分与实现映射（与代码目录/包/入口对齐）
    - `engineering/`：工程落地指南（本地运行、配置、依赖、CI/部署、排障）
    - `work-split/`：分工实现指南（面向“单模块 Codex”的任务拆分、交付物与验收点）
- 代码结构必须与 `docs/modules/<module-name>/implement/logic/` 对齐，并在文档中维护“逻辑 → 代码路径/入口”映射。
- PoC/Spike 必须独立管理：代码放 `modules/arch-poc/`，文档放 `docs/modules/arch-poc/`（同样遵循 `contract/`、`implementation/`、`implement/` 分层）。
- PoC/Spike 运行产物（日志、报告、临时文件、大文件等）只允许落在 `modules/arch-poc/debug/out/`，默认不提交（需加入 `.gitignore`）。
- 跨模块/全局配置（CI/容器/统一入口）建议集中在 `config/`；少量入口级文件可保留根目录（如 `.env.example`、`docker-compose.yml`、`Makefile`）。
- 全局与模块配置需区分“模板 vs 本地值”：模板用 `*.example.*`；本地/敏感配置（如 `.env`、`config/local/*`、`*.local.*`）必须加入 `.gitignore`，通过环境变量或 CI Secrets 注入。
- 配置契约与说明必须文档化：全局规则写在 `docs/architecture.md`，模块配置写在对应模块的 `docs/modules/<module-name>/contract/`。

## 提交与 PR 规范
- 提交信息建议采用 Conventional Commits（类型前缀 + 中文描述），例如：
  - `docs: 更新时间筛选规则`
  - `feat: 新增来源清单模块`
  - `fix: 修复城市规范化映射`
- PR 必须说明：变更目的、影响模块、对应文档更新点；涉及 UI 时补充截图（后续前端阶段）。

## 规范
每当用户提问时，需要停下任何手头的工作，对问题进行回答
当有任何改动时必须先给出相应计划，不许直接实施。

## 编码与写入规则（强制）
- 在 Windows / PowerShell 环境下，凡是修改包含中文的 `json`、`md`、`txt`、`html`、`csv` 等文本文件，必须显式按 `UTF-8` 读写。
- 禁止通过 PowerShell here-string、命令行中文字面量、或其他未经编码控制的 shell 传参方式，直接把中文内容写入 Python / 脚本后再落盘；这类写法容易把中文破坏成 `?` 或 `å...` 串码。
- 如必须通过 shell 调用脚本批量改中文内容，必须采用 ASCII-safe 方式传参（例如 `unicode escape`、外部 UTF-8 临时文件、或纯文件内处理），并在脚本内部完成解码后再以 `UTF-8` 写回。
- 对正式文件的任何覆盖写入，必须在写入前就保证编码正确；不允许采用“先写进去，再检查，再修复”的流程。
- 只要当前写入链路无法在事前保证中文正确，就不得直接覆盖正式文件；必须先改用可证明安全的链路。
- 涉及中文的结构化文件改动，推荐流程固定为：
  1. 在受控链路中生成候选结果（例如 Python 内存对象、UTF-8 临时文件、ASCII-safe 参数传递后的脚本输出）
  2. 在覆盖正式文件前先做编码校验
  3. 只有校验通过后，才允许覆盖正式文件
- 覆盖正式文件前的编码校验至少确认：
  - 内容能被 `UTF-8` 正常读取
  - 关键中文字段未变成 `?`
  - 未出现 `å`、`ä¸`、`ç` 等典型串码特征
- 禁止把“结果最终修对了”视为合格过程；过程本身必须避免把错误编码写入正式文件。

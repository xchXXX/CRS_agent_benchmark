# CRS Agent

这是新项目根目录。

当前阶段的目标不是直接把旧项目代码搬进来，而是先建立一套干净的项目骨架，明确：

- 新后端的目录边界
- Agent Loop 的运行时落点
- 旧项目能力未来如何通过复制或 adapter 接入
- 前端如何在保持现有视觉风格的前提下迁移
- `ask_user_question` 如何作为统一的人机澄清入口接入 Agent Loop

## 当前目录

- `backend/`: 新后端工程骨架
- `frontend/`: 新前端工程占位
- `设计方案/`: 迁移和架构设计文档

## 当前状态

- 新后端已经接入 `Pydantic AI` 运行时，不再是纯占位骨架
- `/chat/completions` 已可返回真实 Agent 结果
- `ask_user_question` 已按 deferred external tool 接入，可在后续请求中继续执行
- `doc_search`、故障诊断、Mem0 仍是下一阶段接入项

## 启动方式

- 本地联调：`npm run start`
  会后台拉起后端、用户端和管理端。
- 仅启动后端：`npm run start:backend`
- 生产方式启动后端：`npm run start:prod`
  等价于 `npm run start:backend:prod`，后端以 `4 workers` 后台运行。
- 停止后端：`npm run stop:backend`
- 重启生产后端：`npm run restart:prod`

## 迁移原则

- 不直接修改旧项目 `doc_search`
- 需要复用的能力，复制到新项目或通过 adapter 包装
- 先建立新项目边界，再分阶段接入资料搜索、故障诊断、AskUser、Mem0

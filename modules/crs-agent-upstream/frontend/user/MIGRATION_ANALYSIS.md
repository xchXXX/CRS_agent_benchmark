# User Frontend Migration Analysis

## 1. 目标

将旧项目用户端 `doc_search` 相关前端迁移到新项目：

- 保留旧项目的视觉风格和交互体验
- 保持目录独立：`frontend/user`
- 不把旧项目前端作为依赖
- 适配新后端的 `Agent Loop` 协议，而不是继续依赖旧 `clarify_business` 状态机

## 2. 当前现状

### 新项目

- `frontend/admin/`：只有占位说明
- `frontend/miniapp/`：只有占位说明
- `frontend/user/`：此前不存在，现已补出落点

### 旧项目

- `frontend/user/`：完整 React + Vite 用户端
- `frontend/admin/`：完整 React + Ant Design 管理端

结论：

- 用户端是当前迁移重点
- 管理端先保留 `frontend/admin` 分层，不在本阶段一起搬

## 3. 旧用户端结构判断

旧用户端技术栈：

- React 18
- Vite 5
- TypeScript
- Tailwind CSS
- Axios
- Zustand
- React Query

旧用户端主要入口和依赖：

- `src/App.tsx`
- `src/styles/index.css`
- `src/components/ClarifyWizard.tsx`
- `src/components/DocumentViewer.tsx`
- `src/services/api.ts`
- `src/shared/types/index.ts`

结构特征：

- `App.tsx` 过大，承担了消息编排、流式处理、生命周期、澄清向导、文档查看、图片诊断等多种职责
- `ClarifyWizard` 已经是成熟的交互壳，可以复用视觉和大部分 DOM 结构
- 样式集中在 `src/styles/index.css`，是旧项目设计语言的主要来源

## 4. 旧设计里必须保留的部分

这些是迁移时不应重设计的内容：

- 顶部白色亮面主题和网格背景
- `Outfit + JetBrains Mono` 字体组合
- 消息气泡、结果卡片、状态条、加载态样式
- `ClarifyWizard` 的折叠式澄清体验
- `Top1` 快捷入口卡片和“就是这个”按钮
- 文档结果列表和结果摘要区域
- 诊断/搜索/问答三种示例问题和功能入口的视觉组织

结论：

- 新前端应当是“旧 UI 的源码迁移 + 新协议适配”
- 不应重写成另一套通用聊天壳

## 5. 新旧协议差异

### 旧用户端假设

旧前端主要围绕这些协议工作：

- `/chat/api/chat/completions`
- `/chat/api/chat/stream`
- `/chat/api/chat/session/:id`
- `/chat/api/chat/history/:id`
- `/chat/api/chat/stream/abort`
- `/chat/api/file/:id/preview`
- `/chat/api/stats`

旧澄清协议核心是：

- `type=clarify_business`
- `content.message/query/results_count/top_result/existence_info`
- `clarify_options`
- 前端本地维护 `clarify_wizard`

### 新项目当前后端

当前只明确提供：

- `/chat/completions`
- `/health`

并且新的人机交互中心是：

- `type=ask_user`
- `ask_user.question`
- `ask_user.options`
- `ask_user.context`
- `ask_user_answer`

### 差异结论

前端迁移不能直接照搬旧 `api.ts` 和旧 `ChatResponse` 消费逻辑。

必须新增一层前端协议适配，把：

- 新后端 `ask_user`
- `selection_payload`
- `ask_user.context.top_result/existence_info/message/query`

映射为旧 UI 可消费的 `ClarifyWizard` 状态。

## 6. 已确认的 doc_search 适配点

新后端现在已经能提供这些前端需要的数据：

- `ask_user.options[].selection_payload`
- `ask_user.context.message`
- `ask_user.context.query`
- `ask_user.context.results_count`
- `ask_user.context.top_result`
- `ask_user.context.existence_info`
- `search_documents` 返回 `summary`
- `search_documents` 返回 `result_summary`

这意味着：

- 旧 `ClarifyWizard` 可以继续用
- 旧 `Top1` 快捷确认 UI 可以继续用
- 旧 summary 文案展示可以继续保留
- 但事件入口要从 `clarify_business` 改成 `ask_user`

## 7. 迁移建议的目录结构

建议在新项目中保留以下结构：

```text
frontend/
├── admin/
│   └── README.md
├── miniapp/
│   └── README.md
└── user/
    ├── README.md
    ├── MIGRATION_ANALYSIS.md
    ├── package.json
    ├── vite.config.ts
    ├── tsconfig.json
    ├── index.html
    ├── public/
    └── src/
        ├── App.tsx
        ├── main.tsx
        ├── styles/
        ├── components/
        ├── services/
        ├── protocol/
        ├── types/
        ├── shared/
        └── utils/
```

新增建议：

- `src/protocol/`
  用来放新后端协议到旧 UI ViewModel 的适配器

## 8. 推荐的迁移策略

### Phase A：先复制用户端壳子

从旧项目复制这些内容到新项目 `frontend/user`：

- `package.json`
- `vite.config.ts`
- `tsconfig.json`
- `index.html`
- `public/`
- `src/styles/index.css`
- `src/components/`
- `src/utils/`

注意：

- 先不复制旧 `services/api.ts` 原样使用
- 先不复制整份 `App.tsx` 直接上线

### Phase B：拆协议层

新建：

- `src/protocol/agentLoopAdapter.ts`
- `src/types/agentLoop.ts`

职责：

- 把新后端 `ChatResponse`
- `ask_user`
- `ask_user_answer`
- `selection_payload`

转换成旧 UI 内部状态。

### Phase C：迁移 `doc_search` 最小闭环

优先只打通：

- 文本提问
- 资料搜索结果展示
- `ask_user -> ClarifyWizard`
- `top_result -> quick confirm`
- `selection_payload` 回传
- 结果 summary 展示

先不带入：

- 图片诊断
- 批量诊断
- 反馈
- 会话历史恢复
- 文档预览之外的复杂分支

### Phase D：再决定是否迁移其它业务

在 `doc_search` 跑通后，再评估：

- 流式输出
- fault diagnosis UI
- session/history
- feedback
- miniapp 复用

## 9. 关键风险

### 风险 1：旧 `App.tsx` 过于庞大

问题：

- 直接复制会把旧状态机和多业务耦合一起带进来

处理：

- 复制 UI 组件和样式
- 重写协议层和 `App` 中的消息编排

### 风险 2：新后端接口缺口较大

当前新项目尚未提供旧前端依赖的这些接口：

- `chat/stream`
- `chat/session/:id`
- `chat/history/:id`
- `chat/stream/abort`
- `file/:id/preview`
- `stats`

处理：

- 用户端第一阶段只接 `/chat/completions`
- UI 中把依赖缺口模块先显式降级或暂时关闭

### 风险 3：旧前端把澄清视为 `clarify_business`

问题：

- 新后端统一为 `ask_user`

处理：

- 由前端协议适配层把 `ask_user` 转成 `ClarifyWizard` 的 ViewModel
- 不要求后端回退成旧协议

## 10. 下一步执行建议

前端迁移的下一步应当是：

1. 在 `frontend/user` 复制旧用户端工程基础文件
2. 保留旧样式和组件
3. 新建协议适配层，优先适配 `ask_user`
4. 只打通 `doc_search` 用户端闭环

不建议下一步直接做：

- 管理端迁移
- 图片诊断迁移
- 批量诊断迁移
- 全量流式协议恢复

## 11. 结论

前端迁移应该采用：

- 目录分离
- 旧设计复用
- 新协议适配
- 先用户端、后管理端
- 先 `doc_search`、后其他业务

当前最合理的正式实施顺序是：

- 先建设 `frontend/user`
- 复制旧用户端视觉壳
- 用新 `ask_user` 协议替换旧 `clarify_business` 接线
- 完成 `doc_search` 的用户端闭环

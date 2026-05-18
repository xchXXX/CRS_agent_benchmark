# Pydantic AI 二次开发关键技术细节

> 编写日期：2026-03-19
> 状态：方案讨论阶段

## 一、概述

本文档梳理基于 Pydantic AI 框架进行二次开发时需要解决的关键技术问题，包括 Agent Loop 运行机制、对话历史管理、流式输出适配、模型兼容性处理，以及与现有系统的对接策略。

## 二、Agent Loop 运行机制

### 核心循环

```
agent.run(用户输入, message_history=历史消息)
    ↓
组装 system prompt + 历史 + 用户输入 → 发给 LLM
    ↓
┌→ LLM 返回 ToolCallPart → 框架自动执行 tool → 结果回灌 → 再次调 LLM ─┐
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
    ↓
LLM 返回 TextPart → 循环终止，返回最终结果
```

框架负责 tool call 解析、执行、结果回灌的完整闭环，业务侧只需注册 tool 和配置循环参数。

### 循环控制（防死循环）

通过 `UsageLimits` 设定：
- **最大轮次**：建议 10 轮，防止无限循环
- **单次回复 token 上限**：建议 4000 token
- **timeout 机制**：框架层超时 + 业务层超时双保险

## 三、现有系统对接映射

### 保留的模块

| 现有模块 | 角色变化 |
|---------|---------|
| SearchEngine（混合检索） | 不变，被 doc_search tool 内部调用 |
| DiagnosisClient（故障码） | 不变，被 dtc_diagnosis tool 内部调用 |
| Redis SessionMgr | 不变，继续作为会话持久化层 |

### 替换的模块

| 现有模块 | 替换为 | 原因 |
|---------|-------|------|
| LLMClient | Pydantic AI 模型抽象层 | 统一多模型管理、自动处理 tool calling 协议差异 |
| ChatOrchestrator | Pydantic AI Agent Loop | 从手动编排升级为 LLM 自主决策循环 |
| IntentRouter | 弱化/移除 | LLM 在 loop 中自主选择 tool，不再需要前置意图分类 |

### 包装为 tool 的模块

现有的 Handler 逻辑通过 `@agent.tool` 装饰器包装，业务代码基本不改，只是调用入口从 Handler 变为 tool 函数。

## 四、对话历史管理

### 序列化与持久化

Pydantic AI 不内置持久化，但提供序列化 API。每次 run 结束后将新消息序列化存入 Redis，下次 run 前反序列化恢复。与现有 Redis 会话层直接对接。

### 上下文膨胀问题（核心难点）

用户可能在一个 session 里聊 50 轮，每轮 loop 内部可能有 3-5 次 tool call，消息体快速膨胀。

**应对策略：history_processors 机制**

Pydantic AI 提供 `history_processors` 钩子，在每次 run 前对历史消息做预处理：

- 消息数超过阈值时，用便宜模型摘要早期消息
- 保留最近 N 条完整消息（建议 6 条），确保 LLM 有足够的近期上下文
- 借鉴 nanobot 的热/冷状态分层：当前轮次消息（热）+ 摘要归档（冷）

**关键约束：** 压缩时必须保证 tool call 和 tool return 成对出现。截掉一个 ToolCallPart 但保留对应的 ToolReturnPart 会导致 LLM 报错。需要自行实现配对检查逻辑。

## 五、流式输出与 Tool 调用的兼容

### 问题

Tool 执行期间没有流式输出。如果 loop 跑了 3 轮 tool call，用户会经历一段空白等待后突然开始输出。

### 解决方案：事件流

使用 Pydantic AI 的 `run_stream_events()` 获取完整事件流，将中间状态推送给前端：

```
[FunctionToolCallEvent]  → 前端显示 "正在搜索文档..."
[FunctionToolResultEvent] → 前端显示 "搜索完成"
[PartDeltaEvent/TextPartDelta] → 前端流式渲染文本
```

### 前端 SSE 协议扩展

在现有纯文本流基础上新增事件类型：

| 事件类型 | 含义 | 前端处理 |
|---------|------|---------|
| `text_delta` | 文本流式片段 | 追加渲染（现有逻辑） |
| `tool_status` | tool 调用进度 | 显示进度提示（如"正在分析故障码..."） |
| `ask_user` | 结构化提问 | 渲染问题卡片（详见 AskUserQuestion 设计方案） |
| `done` | 本轮结束 | 结束流式状态 |

前端可增量适配——先只处理 `text_delta` 和 `done`，其他类型逐步添加。

## 六、廉价模型的 Tool Calling 适配

### 问题

DeepSeek V3 / Gemini Flash 等低成本模型的 tool calling 能力弱于旗舰模型，常见问题：参数格式错误、选错 tool、不该调 tool 时调了。

### 应对策略

**1. FallbackModel 兜底**

Pydantic AI 支持配置降级链——便宜模型先试，失败后自动切换到贵的模型。

**2. 降低 tool 选择难度**

- 每个 tool 参数不超过 3-4 个
- docstring 明确写清什么场景该调用、什么场景不该调用
- 避免嵌套复杂参数结构
- tool 数量控制在合理范围（建议不超过 8 个）

**3. retries 自动重试**

Pydantic AI 的参数验证失败会自动把错误信息发回 LLM 让它修正参数，对小模型特别有用。建议每个 tool 配置 3 次重试。

## 七、难度评估

| 难点 | 难度 | 说明 |
|------|------|------|
| AskUserQuestion 交互 | ★★★★★ | 中断-恢复模式，状态管理复杂，前端需要适配 |
| 对话历史压缩 | ★★★★ | tool call/return 配对约束，需自行实现压缩逻辑 |
| 流式 + Tool 调用体验 | ★★★ | 需要 run_stream_events + 前端展示中间状态 |
| 廉价模型适配 | ★★★ | FallbackModel + retries 可缓解，需持续调优 |
| Agent Loop 本身 | ★★ | 框架已封装，主要是 tool 包装工作 |
| Redis 会话持久化 | ★★ | 序列化 API 完善，和现有 Redis 层直接对接 |

## 八、渐进式迁移路径

建议分阶段推进，每阶段可独立验证：

**阶段一：基础 Loop**
- 引入 Pydantic AI，将现有 Handler 包装为 tool
- 替换 ChatOrchestrator 为 Agent Loop
- 保持现有前端不变，只处理 text_delta 和 done

**阶段二：流式体验**
- 接入 run_stream_events，前端增加 tool_status 展示
- 实现对话历史的 Redis 序列化/反序列化

**阶段三：AskUserQuestion**
- 实现 Deferred Tools 中断-恢复机制
- 前端增加问题卡片 UI
- 实现会话状态管理（awaiting_user 状态）

**阶段四：上下文治理**
- 实现 history_processors 压缩机制
- 实现 tool call/return 配对检查
- 接入分层模型策略（按场景动态切换模型）

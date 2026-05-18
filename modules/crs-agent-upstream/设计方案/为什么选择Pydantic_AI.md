# 为什么选择 Pydantic AI

> 编写日期：2026-03-19
> 状态：方案讨论阶段

## 一、选型背景

项目需要从当前的 Orchestrator + Handler 单跳架构演进到 Agent Loop 架构，核心诉求：

- LLM 自主选择 tool 并多步推理
- 支持向用户主动提问（AskUserQuestion）
- 易于扩展新 skill
- 支持多模型动态切换，控制商业化成本
- 与现有 FastAPI 后端低摩擦对接

## 二、候选框架评估

| 框架 | 排除原因 |
|------|---------|
| 从零自建 | tool call 解析、结果回灌、循环控制、多模型适配全部自己写，周期 3-6 个月 |
| LangGraph | Human-in-the-loop 最成熟，但学习曲线陡、LangChain 抽象层过重、调试困难、依赖链长 |
| Agno（原 PhiData） | 无 Human-in-the-loop 机制，硬伤 |
| AutoGen | 正在与 Semantic Kernel 合并，API 不稳定；多 Agent 设计对单 Agent + 多 tool 场景过度 |
| CrewAI | 完整 HITL 锁在企业版，开源版粗糙；偏向批处理任务而非实时对话 |
| Dify | 平台型方案，与现有 FastAPI 后端是并列关系而非嵌入关系，深度定制受限 |
| nanobot | 偏向多渠道 bot 平台（Slack/Telegram），渠道层复杂度对 Web App 场景是负担 |
| Pi | 偏向 coding agent，monorepo 结构重，会话分支/fork 对维修诊断场景过度设计 |

## 三、选择 Pydantic AI 的理由

### 1. 技术栈一致

现有后端是 FastAPI，Pydantic AI 由同一团队设计，依赖注入模式几乎一致。集成零摩擦，不引入新的心智模型。

### 2. Tool 注册简洁

`@agent.tool` 装饰器 + Pydantic 类型注解，参数校验自动完成。现有 Handler 业务逻辑可直接包装为 tool，不改业务代码。

### 3. 多模型全覆盖

原生支持 OpenAI、Anthropic、Gemini、Groq、Mistral 等 25+ 提供商。DeepSeek、Qwen 通过 OpenAI 兼容接口接入，Ollama 通过社区包或兼容模式接入。支持 FallbackModel 降级链——便宜模型先试，失败自动切换。

### 4. 轻量无黑盒

框架代码量小，抽象层薄。出了问题能直接看源码调试，对 Agent Loop 有完全控制权。商业化后做成本优化、性能调优时不会被框架挡路。

### 5. AskUserQuestion 可实现

DeferredTools 机制提供中断信号，配合 Redis 持久化和 FastAPI 异步端点，可以实现"中断-持久化-恢复"的用户交互流程。虽然不如 LangGraph 开箱即用，但我们的场景（问个问题等用户选一下）复杂度可控。

### 6. MCP 支持

未来可将 skill 包装为 MCP Server，对接第三方工具（如 ECU 厂商提供的 MCP 服务）。

### 7. 商业化无障碍

MIT 许可证，100% 测试覆盖，生产级成熟度。

## 四、现有系统对接映射

### 保留

| 模块 | 角色 |
|------|------|
| SearchEngine（混合检索） | 被 doc_search tool 内部调用 |
| DiagnosisClient（故障码） | 被 dtc_diagnosis tool 内部调用 |
| Redis SessionMgr | 继续作为会话持久化层 |

### 替换

| 现有模块 | 替换为 | 原因 |
|---------|-------|------|
| LLMClient | Pydantic AI 模型抽象层 | 统一多模型管理，自动处理 tool calling 协议差异 |
| ChatOrchestrator | Pydantic AI Agent Loop | 从手动编排升级为 LLM 自主决策循环 |
| IntentRouter | 弱化/移除 | LLM 在 loop 中自主选择 tool |

### 包装为 tool

现有 Handler 的业务逻辑通过 `@agent.tool` 包装，业务代码基本不改，只是调用入口变化。

## 五、已知局限

| 局限 | 应对 |
|------|------|
| HITL 不如 LangGraph 开箱即用 | 场景复杂度可控，自建编排层工作量可接受 |
| 不内置对话持久化 | 序列化 API 完善，对接现有 Redis 层即可 |
| 不内置上下文压缩 | 通过 history_processors 钩子自行实现，或引入外部方案 |
| 框架较年轻（2024 年底发布） | Pydantic 团队维护，迭代快，社区增长迅速 |

如果后续 HITL 复杂度超出预期（如需要跨天暂停、多步审批），可考虑引入 LangGraph 替换编排层，但现阶段不需要。

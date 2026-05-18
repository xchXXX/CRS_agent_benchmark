# Agent Loop 迁移实施与项目结构设计

> 编写日期：2026-03-20
> 状态：方案讨论阶段

## 一、目标

这份文档回答三个问题：

1. 旧项目如何迁移到 Agent Loop 架构
2. 新架构如何接入现有后端、前端、外部服务
3. 迁移后的项目结构应如何设计

本文档不是只讨论原则，而是给出可落地的迁移路径和目录设计。

## 二、当前旧项目的真实情况

### 1. 主链路仍然是单跳编排

旧项目的聊天主链路是：

```text
API / WebSocket
  -> ChatOrchestrator
  -> SessionManager
  -> IntentRouter
  -> Handler
  -> ChatResponse / Stream Event
```

这意味着：

- 请求先做前置意图判断
- 一旦路由到某个业务 handler，本轮处理路径基本固定
- “下一步做什么”由代码写死，而不是由 LLM 在 loop 中动态决策

### 2. 现有业务能力本身并不弱

虽然架构旧，但业务能力已经比较完整：

- 资料搜索：`ggzj` 外部检索 + 结果适配 + 规则澄清 + LLM 智能澄清
- 故障诊断：故障码识别、ECU 反查、报告生成、异步状态更新
- 通用问答：行业提示词 + 历史上下文注入
- 会话持久化：Redis 已经承担 session/context/history 职责
- 小程序前端：已具备流式文本、澄清卡片、结果卡片、故障卡片等基础交互

问题不在于“功能太弱”，而在于：

- 能力分散在多个 handler 中
- 交互状态机嵌在业务逻辑里
- 没有统一的 Agent Runtime
- 无法自然支持多工具联动、统一 AskUser、长期记忆和可观测 loop

### 3. 迁移必须坚持“保留业务能力，替换编排方式”

这次升级不能理解成“推翻旧项目重做一个 AI 系统”。

正确的理解是：

- 保留已有业务能力和前端形态
- 把旧的手工编排链路替换为 Agent Loop
- 把旧能力从 handler 状态机中拆出来，逐步变为 tool / adapter

## 三、迁移设计的核心原则

### 原则 1：保留现有 API 和前端交互面

迁移初期，不改动对外入口：

- 保留 `/chat/completions`
- 保留 `/chat/ws`
- 保留当前小程序页面结构和视觉风格
- 保留现有 `start/chunk/done/fallback/error/diagnosis_status_update` 基本事件语义

原因很简单：

- 用户端已经跑通
- 小程序对 WebSocket 的支持已经成熟
- 风险应该集中在运行时和编排逻辑，不应该同时推翻协议和 UI

### 原则 2：先接入 Agent Runtime，再逐步拆旧 handler

迁移不要一步到位地把所有 handler 彻底重写。

正确路径是：

1. 先把 Agent Runtime 搭起来
2. 用 adapter 包装旧能力接入 runtime
3. 系统先跑通
4. 再逐步把旧 handler 内部的状态机拆成纯 tool

### 原则 3：AskUser 统一出口

所有需要用户补充信息的场景，最终都应统一走：

- `ask_user_question`

这条原则对 `doc_search`、`fault_diagnosis`、未来的参数确认、维修步骤确认都成立。

业务 tool 可以发现“该问什么”，但真正问用户这件事，统一由 AskUser skill 负责。

### 原则 4：Redis 不退场，Mem0 不越位

迁移后：

- Redis 继续承担短期会话状态、消息历史、pending deferred tool、异步任务状态
- Mem0 只承担长期事实记忆和跨轮次经验复用

Mem0 不是 Redis 的替代品。

### 原则 5：迁移期间必须支持灰度和回滚

新旧链路在一段时间内要并存。

因此系统必须支持至少三种运行模式：

- `legacy`：完全走旧链路
- `agent_loop`：走新链路
- `shadow`：用户返回仍走旧链路，但后台并行跑新链路做日志对比

## 四、目标架构

### 1. 后端目标链路

```text
HTTP/WS Chat Gateway
  -> Runtime Selector
  -> AgentLoopService
      -> Pydantic AI Agent
          -> instructions / dynamic instructions
          -> tool registry
          -> history processors
          -> deferred tools
      -> Session Store (Redis)
      -> Long-term Memory (Mem0)
      -> Event Translator
      -> Observability
  -> WebSocket / HTTP Response
```

### 2. 分层职责

#### Gateway 层

职责：

- 继续提供 `/chat/completions` 和 `/chat/ws`
- 解析 token / user_id / session_id
- 选择运行时（legacy 或 agent_loop）
- 把 Agent 事件翻译成现有前端可消费的协议

#### Runtime 层

职责：

- 创建和执行 Pydantic AI Agent
- 注入 `message_history`
- 接收 `run_stream_events()`
- 管理 `DeferredToolRequests / DeferredToolResults`
- 控制 usage limits / retries / fallback

#### Tool 层

职责：

- 提供纯能力接口
- 不持有会话状态机
- 输入清晰、输出结构化
- 参数尽量简单，方便模型可靠调用

#### Adapter 层

职责：

- 兼容旧服务、旧 handler、外部客户端
- 把旧项目中的复杂业务能力转为新工具的稳定输入输出

#### Memory 层

职责：

- Redis：热状态、消息历史、pending call、任务状态
- Mem0：长期事实记忆、偏好、已确认诊断信息

#### Frontend Protocol 层

职责：

- 保持现有视觉设计
- 在现有 WS 事件协议上做小步扩展
- 复用现有卡片 UI

## 五、旧模块如何映射到新架构

## 1. 继续保留的模块

### API 入口

- `backend/app/api/chat.py`

保留原因：

- 已经承载 HTTP + WebSocket 两个入口
- 小程序和服务端的连接关系稳定

改造方式：

- 增加 runtime selector
- 旧的 `chat_orchestrator` 改为可切换依赖

### Session / Redis

- `backend/app/services/chat/session_manager.py`
- `backend/app/services/storage/redis_client.py`

保留原因：

- 现有 session 管理已经成熟
- Redis 已能管理 state/context/history

改造方式：

- 新增 agent 专用字段和 key
- 增加 Pydantic AI 消息历史序列化存储
- 增加 deferred tool pending state 存储

### 外部服务客户端

- `backend/app/services/ggzj/search_client.py`
- `backend/app/services/diagnosis/diagnosis_client.py`
- 相关 adapter / resolver / subscriber

保留原因：

- 这些是真实业务能力，不应重写

改造方式：

- 作为 tool 内部依赖或 adapter 依赖继续使用

### 前端页面

- `frontend/miniapp/src/pages/chat/index.tsx`
- `frontend/miniapp/src/services/chatWs.ts`

保留原因：

- 当前交互形态已经覆盖主要场景
- 用户体验不需要推倒重来

改造方式：

- 扩展事件类型
- 复用现有 `clarify_*` 卡片样式过渡到 `ask_user`

## 2. 需要替换的模块

### `ChatOrchestrator`

旧角色：

- 手工编排总控

新角色：

- 由 `AgentLoopService` 替代

处理方式：

- 保留旧 orchestrator 作为兼容实现
- 不再作为默认主链路

### `IntentRouter`

旧角色：

- 前置意图分类

新角色：

- 退出主路径

处理方式：

- 初期保留为观测和兜底工具
- 最终不再主导“先分流后处理”

### `GeneralChatHandler`

旧角色：

- 直接调传统 LLM client

新角色：

- 其系统提示词和历史整理逻辑吸收到 Agent `instructions`

处理方式：

- 不建议把它包成 tool
- 否则会变成“Agent 里再调一个聊天模型”，结构上会很别扭

## 3. 需要先包一层再拆的模块

### `DocSearchHandler`

当前问题：

- 搜索逻辑、澄清逻辑、上下文状态机都混在一起

迁移策略：

- Phase 1：做 `legacy_doc_search_adapter`
- Phase 2：拆成 `search_documents` + `analyze_search_ambiguity`
- Phase 3：把 AskUser 从内部状态机剥离

### `FaultDiagHandler`

当前问题：

- 故障码识别、ECU 澄清、报告执行、状态返回混在一起

迁移策略：

- Phase 1：做 `legacy_fault_diag_adapter`
- Phase 2：拆成 `parse_fault_code`、`lookup_ecu_candidates`、`dtc_diagnosis`
- Phase 3：ECU 澄清统一接入 AskUser

## 六、迁移后的目录结构设计

目标是：不打散旧目录，但把新架构明确收敛到 `services/agent` 下。

建议结构如下：

```text
backend/app/services/agent/
├── __init__.py
├── runtime/
│   ├── __init__.py
│   ├── agent_loop_service.py
│   ├── agent_factory.py
│   ├── deps.py
│   ├── event_stream.py
│   ├── runtime_selector.py
│   ├── usage_policy.py
│   └── fallback_policy.py
├── prompts/
│   ├── __init__.py
│   ├── base_instructions.py
│   ├── diagnosis_instructions.py
│   ├── search_instructions.py
│   └── dynamic_instructions.py
├── models/
│   ├── __init__.py
│   ├── agent_context.py
│   ├── tool_outputs.py
│   ├── ask_user_models.py
│   ├── runtime_events.py
│   └── memory_models.py
├── tools/
│   ├── __init__.py
│   ├── ask_user_question.py
│   ├── search_documents.py
│   ├── analyze_doc_search_ambiguity.py
│   ├── lookup_ecu_candidates.py
│   ├── dtc_diagnosis.py
│   ├── search_repair_knowledge.py
│   └── search_circuit_diagram.py
├── adapters/
│   ├── __init__.py
│   ├── legacy_doc_search_adapter.py
│   ├── legacy_fault_diag_adapter.py
│   ├── ggzj_adapter.py
│   ├── diagnosis_adapter.py
│   └── frontend_protocol_adapter.py
├── memory/
│   ├── __init__.py
│   ├── redis_session_store.py
│   ├── message_history_store.py
│   ├── history_processors.py
│   ├── deferred_state_store.py
│   └── mem0_store.py
├── observability/
│   ├── __init__.py
│   ├── loop_tracer.py
│   ├── tool_logger.py
│   └── runtime_metrics.py
└── migration/
    ├── __init__.py
    ├── legacy_bridge.py
    ├── shadow_runner.py
    └── compare_report.py
```

## 七、各目录职责说明

### `runtime/`

这是 Agent Loop 的真正运行时。

核心职责：

- 创建 Agent
- 发起 run / run_stream_events
- 装载历史消息
- 接收 deferred tool 恢复结果
- 输出统一运行结果

关键文件建议：

- `agent_loop_service.py`
  - 新主入口
- `agent_factory.py`
  - 负责构造 Pydantic AI Agent
- `runtime_selector.py`
  - 决定走 legacy / agent_loop / shadow
- `event_stream.py`
  - 把 Pydantic AI 原始事件翻译成前端协议

### `prompts/`

职责：

- 管理 Agent 的基础 instructions
- 管理运行时动态注入的上下文说明
- 管理不同场景下的约束语

这里不要继续散落在旧 `handler` 内拼字符串。

### `models/`

职责：

- 统一声明 Agent 内部使用的 Pydantic 数据结构
- 统一 tool 输入输出协议
- 统一 AskUser 问题模型

如果没有这层，后续工具和事件协议会很快变乱。

### `tools/`

职责：

- 只放纯工具定义
- 不做复杂持久化
- 不直接接触前端协议

特别说明：

- `ask_user_question.py` 是一级核心 tool
- `search_documents.py` 不直接问用户
- `analyze_doc_search_ambiguity.py` 负责提炼“该问什么”

### `adapters/`

职责：

- 对旧系统和新 tool 做接口桥接
- 尽量把历史包袱隔离在这里

例如：

- `legacy_doc_search_adapter.py`
  - 先复用旧搜索能力
  - 输出新格式的搜索结果和歧义分析结果

### `memory/`

职责：

- 管理 Redis 和 Mem0 的职责边界
- 管理 message history 持久化
- 管理 history processors
- 管理 deferred tool pending state

### `observability/`

职责：

- 记录每轮 loop 做了什么
- 记录 tool 调用和参数
- 记录为什么问用户、为什么收束

没有这一层，后续 prompt 调优和工具质量优化都会很困难。

### `migration/`

职责：

- 支持新旧链路并存
- 支持 shadow 模式对比
- 支持逐步切流

## 八、`doc_search` 的专项迁移设计

`doc_search` 是本次迁移中最重要、也最容易误拆的模块。

### 1. 不应该怎么迁

错误方式是：

- 直接把旧 `DocSearchHandler.handle()` 包成一个 tool
- 让它继续内部返回 `clarify_business`
- 让它继续维护 `pending_clarify_facet`

这样虽然表面接进了 Agent，但本质上只是把旧状态机塞进了新 runtime。

### 2. 正确迁移路径

#### Phase 1：兼容接入

保留旧搜索、排序、规则澄清、LLM 智能澄清能力，但输出统一变成内部结构：

- `final_results`
- `need_clarify`
- `question`
- `options`
- `selection_payload`

此时不再由 `doc_search` 直接问用户。

#### Phase 2：拆成两段

拆成两个能力：

- `search_documents`
- `analyze_doc_search_ambiguity`

这样 Agent 可以：

- 先看结果
- 再判断是否需要问用户
- 再统一调用 `ask_user_question`

#### Phase 3：彻底去掉内部交互态

逐步移除 `DocSearchContext` 中仅为交互服务的字段：

- `pending_clarify_facet`
- `clarify_history`
- `llm_clarify_mapping`

保留真正与搜索结果相关的上下文：

- 原始 query
- 当前 filters
- cache results

## 九、`fault_diagnosis` 的专项迁移设计

`fault_diagnosis` 也不能整体照搬。

建议拆成三个独立工具：

- `lookup_ecu_candidates(fault_code)`
- `dtc_diagnosis(fault_code, ecu_model)`
- `get_diagnosis_task_status(task_id)`

同时保留异步状态推送能力：

- `diagnosis_status_update`

如果故障码有多个 ECU 候选，则：

- tool 返回候选 ECU
- Agent 决定调用 `ask_user_question`
- 用户选定 ECU 后再调 `dtc_diagnosis`

这会比旧版在 handler 内部直接做 ECU 澄清更符合新架构。

## 十、Agent 与 Mem0 / Redis 的接入方式

### 1. Redis 存什么

Redis 继续存：

- session state
- 会话上下文
- Pydantic AI message history
- pending deferred tool call
- 诊断任务状态和推送关系
- agent trace 简要信息

### 2. Mem0 存什么

Mem0 只存长期、可复用、已确认的事实：

- 用户常用车型 / 品牌 / ECU
- 已确认故障码
- 已测量的电压 / 电阻 / 工况
- 明确维修结论
- 用户稳定偏好

### 3. Run 前如何读

每轮 Agent 执行前：

1. 读取 Redis message history
2. 基于用户输入和 user_id 检索 Mem0
3. 把 Mem0 命中的记忆作为动态 instructions 注入

不要把 Mem0 命中的记忆永久写回 message history。

### 4. Run 后如何写

每轮 Agent 执行后：

1. 把 message history 序列化写回 Redis
2. 异步提取高价值事实写入 Mem0
3. 失败不阻塞主回复

## 十一、前端接入方式

前端目标是“小步适配，不翻页面”。

### 1. 保持不变的部分

- 页面布局
- 消息流式渲染方式
- 文档结果卡片
- 故障卡片
- 当前的 WebSocket 连接与队列机制

### 2. 需要新增的协议能力

建议在现有事件协议上增加：

- `tool_status`
- `ask_user`
- `text_delta`

但为了兼容现状：

- `text_delta` 初期可复用为 `chunk`
- `ask_user` 初期可复用现有 `clarify_business` 卡片 UI

### 3. 前端代码层建议

#### 保留文件

- `frontend/miniapp/src/pages/chat/index.tsx`
- `frontend/miniapp/src/services/chatWs.ts`

#### 增量改造

- `chatWs.ts`
  - 扩展 `WsEventType`
  - 增加 `tool_status` / `ask_user` 解析

- `shared/types/index.ts`
  - 增加新事件模型
  - 增加 AskUser 输入类型定义

- `chat/index.tsx`
  - 把 `ask_user` 映射到现有澄清卡片
  - 后续再细分为单选、多选、数值输入、自由输入

## 十二、迁移实施路径

### Phase 0：设计冻结

输出：

- 本文档
- DocSearch AskUser 专项设计
- 运行时协议定义

### Phase 1：搭建 Agent Runtime 骨架

目标：

- `services/agent/runtime` 目录建立
- Pydantic AI Agent 可运行
- Redis message history 可序列化存储
- `/chat/ws` 可以切换到新 runtime

此阶段先只接一个最简单的 tool。

### Phase 2：接入 legacy adapters

目标：

- `legacy_doc_search_adapter`
- `legacy_fault_diag_adapter`

让新 runtime 能调用旧业务能力，但不要求立刻彻底拆纯。

### Phase 3：接流式工具事件

目标：

- 接 `run_stream_events()`
- 前端显示 `tool_status`
- 让用户看到“正在搜索文档 / 正在分析故障码”

### Phase 4：接入 AskUser Deferred Tool

目标：

- 实现 `ask_user_question`
- Redis 持久化 pending tool call
- 用户回答后恢复 loop

### Phase 5：拆 `doc_search` 和 `fault_diagnosis`

目标：

- 从旧 handler 状态机中剥离交互状态
- 形成纯工具 + Agent 编排

### Phase 6：接入 Mem0

目标：

- 跑通长期记忆读写
- 先只写高价值事实
- 观察是否真的提升复用率和用户体验

### Phase 7：灰度切流并退役旧主链路

目标：

- 大部分请求走 `agent_loop`
- `legacy` 模式只保留回滚用途
- 最终移除 `IntentRouter` 的主路径作用

## 十三、运行时切换设计

建议增加配置项，例如：

```text
chat_runtime_mode = legacy | agent_loop | shadow
```

网关层根据这个配置选择：

- `legacy`
  - 走 `chat_orchestrator`
- `agent_loop`
  - 走 `agent_loop_service`
- `shadow`
  - 用户响应走 legacy
  - 同时后台跑 agent_loop 并记录结果对比

这样迁移时风险最低。

## 十四、最重要的设计结论

### 结论 1

这次迁移不是重做业务能力，而是重做编排方式。

### 结论 2

新架构应以 `backend/app/services/agent/` 为收敛点，而不是把 Agent 代码继续散落到 `chat/`、`handlers/`、`llm/` 里。

### 结论 3

迁移应该先让新 runtime 跑起来，再逐步拆旧 handler，不能一上来“全量纯化”。

### 结论 4

`doc_search` 和 `fault_diagnosis` 都要保留原有业务能力，但交互状态要逐步从业务模块中剥离。

### 结论 5

Redis 负责热状态，Mem0 负责长期记忆，WebSocket 继续保留，前端设计保持现有项目风格不变。

一句话总结：

**旧项目迁移到 Agent Loop 的正确方式，是在不破坏现有业务能力和前端体验的前提下，把“手工编排 + 业务内状态机”逐步替换为“统一 runtime + 纯工具 + 统一 AskUser + 分层记忆”。**

# Agent Loop 重构实施方案：从单业务线路由到纯 Agent Loop 架构

## 1. 文档目标

本文档详细说明将当前 CRS Agent 从"intent router 单业务线分流 + service 层 bypass 拦截"架构，重构为"纯 Agent Loop"架构的完整实施方案。

包含：改进原因、当前架构问题分析、目标架构设计、工具注册方案、每一步的具体改动说明、以及验证方式。

---

## 2. 改进原因

### 2.1 为什么要改

当前架构的核心问题是：**一个请求进来，intent_router 在最早阶段就把它锁定到单一业务线**，后续的工具选择、结果处理、响应类型都围绕这条业务线展开。

这导致：

1. **无法处理复合问题**：用户问"报码 P0101，有没有相关资料"，系统只能走故障诊断或资料搜索其中一条线，无法两者都做
2. **能力之间无法协同**：诊断过程中如果需要查针脚参数，当前架构无法自然地在诊断流程中穿插参数查询
3. **service 层逻辑膨胀**：为了在 agent 之外拦截和处理特定工具结果，`service.py` 中堆积了大量 bypass 逻辑（resume 拦截、result 提取、error salvage），每增加一个能力就要加一套拦截代码

### 2.2 目标形态

采用经典的 **ReAct Agent Loop** 模式：

```
用户问题 → LLM 思考 → 调用工具 → 结果回到上下文 → LLM 再思考 → 再调工具 → ... → 问题解决 → 最终回答
```

核心理念：
- **LLM 本身就是编排器**，不需要额外的规划层和决策层
- **上下文就是证据池**，每次工具调用结果都追加到上下文中
- **所有工具平等注册**，LLM 根据问题自由选择，可以在一次对话中调用多个不同能力的工具
- Pydantic AI 的 `Agent.run()` 天然支持这个循环模式，不需要重写底座

---

## 3. 当前架构问题详解

### 3.1 intent_router 硬分流

**文件**: `backend/app/agent/runtime/intent_router.py`

当前 `RequestIntentRouter.route()` 使用关键词匹配将请求归类为 5 种 intent：

| Intent | 触发条件 |
|--------|----------|
| `DOC_SEARCH` | 包含"电路图"、"手册"、"资料"等关键词 |
| `PARAM_QUERY` | 包含"针脚"、"脚位"、"电压"等关键词 |
| `FAULT_DIAGNOSIS` | 检测到故障码且诊断服务可用 |
| `FAULT_DIAGNOSIS_LLM` | 检测到故障码但诊断服务不可用 |
| `GENERAL_CHAT` | 默认兜底 |

**问题**：一旦归类，后续行为就被锁定。例如归为 `DOC_SEARCH` 后，system prompt 中的 `ROUTING_HINT` 会强制 LLM 只调 `search_documents`，不会去查维修知识或参数。

### 3.2 ROUTING_HINT 装饰

**文件**: `backend/app/agent/runtime/service.py` 第 1258-1313 行

`_decorate_user_prompt()` 方法在用户消息前面注入 `[ROUTING_HINT]` 前缀，告诉 LLM 当前是什么 intent，应该调什么工具。

例如 `DOC_SEARCH` 的 hint：
```
[ROUTING_HINT]
intent=doc_search
This request is explicitly asking for technical documents or materials.
You should start with `search_documents` and follow the doc_search clarify rules.
```

这直接限制了 LLM 的工具选择自由度。

### 3.3 service 层 bypass 逻辑

**文件**: `backend/app/agent/runtime/service.py`

当前 `process()` 方法有 5 层拦截逻辑，在 agent 循环之外处理工具结果：

| 拦截层 | 方法 | 作用 | 行号 |
|--------|------|------|------|
| Resume 拦截 1 | `_maybe_resume_parameter_query_clarify()` | 用户回答参数查询澄清后，直接调 service.query()，不经过 agent | 668-714 |
| Resume 拦截 2 | `_maybe_resume_doc_search_clarify()` | 用户回答文档搜索澄清后，直接调 adapter.search()，不经过 agent | 849-889 |
| Result 提取 1 | `_maybe_build_parameter_query_response()` | agent 运行完后，从消息历史中提取 query_parameters 结果 | 716-742 |
| Result 提取 2 | `_maybe_build_doc_search_response()` | agent 运行完后，从消息历史中提取 search_documents 结果 | 891-925 |
| Error 兜底 1 | `_maybe_salvage_parameter_query_on_error()` | agent 报错时，尝试从部分历史中恢复参数查询结果 | - |
| Error 兜底 2 | `_maybe_salvage_doc_search_on_error()` | agent 报错时，尝试从部分历史中恢复文档搜索结果 | - |

**问题**：每新增一个能力，就要在 process() 和 stream() 中各加一套 resume + build + salvage 拦截代码。这不可扩展。

### 3.4 response_business 绑定

`_resolve_response_business()` 方法将 intent 映射为响应业务类型（`DOC_SEARCH`、`PARAM_QUERY`、`FAULT_DIAGNOSIS`、`GENERAL_CHAT`），后续的 result 提取逻辑依赖这个类型来决定去哪个工具的结果中取数据。

**问题**：如果一个请求同时触发了文档搜索和参数查询，只能按 intent 归类选其一。

---

## 4. 应该注册为 Tool 的能力

### 4.1 核心业务工具

以下工具全部平等注册给 LLM，由 LLM 根据上下文自行决定调用：

| Tool 名称 | 能力说明 | 适用场景 | 当前状态 |
|-----------|----------|----------|----------|
| `search_documents` | 搜索技术文档、维修手册、电路图、线路图 | 用户明确要求查资料、查手册、查电路图 | 已有，保留 |
| `analyze_doc_search_ambiguity` | 分析文档搜索结果是否存在歧义，需要用户进一步筛选 | search_documents 返回大量结果时，判断是否需要让用户选择品牌/车系/车型 | 已有，保留 |
| `lookup_ecu_candidates` | 根据故障码查询可能的 ECU 候选列表 | 用户提供故障码，需要确定对应哪个 ECU | 已有，保留 |
| `dtc_diagnosis` | 使用故障码 + ECU 型号执行故障诊断 | ECU 确定后，执行完整诊断分析 | 已有，保留 |
| `lookup_repair_knowledge_titles` | 搜索本地维修知识标题库（Excel 维修经验库） | 用户问故障排查、维修思路、怎么修等问题 | 已有，保留 |
| `get_repair_knowledge_context` | 根据标题 ID 加载维修知识的完整内容 | LLM 判断标题相关后，加载详细维修经验 | 已有，保留 |
| `query_parameters` | 查询 ECU 针脚定义、引脚编号、接插件脚位、开路/静态/怠速电压等参数 | 用户问针脚定义、电压值，或诊断过程中需要参数数据支撑 | 已有，保留 |

### 4.2 交互工具

| Tool 名称 | 能力说明 | 适用场景 | 当前状态 |
|-----------|----------|----------|----------|
| `ask_user_question` | 向用户提问获取缺失信息，支持单选/多选/文本输入 | 任何需要用户补充信息的场景（ECU 选择、品牌/车系/车型筛选、故障现象补充等） | 已有，保留 CallDeferred 机制 |

### 4.3 为什么这样划分

**所有业务能力都是 Tool**：资料搜索、故障诊断、维修知识、参数查询——这些都是 LLM 可以在推理过程中按需调用的外部能力。它们不应该有调用顺序的硬约束，而是由 LLM 根据问题语境决定。

**ask_user_question 是唯一的暂停点**：当 LLM 判断缺少关键信息时，通过这个工具向用户提问。这是整个循环中唯一会暂停等待外部输入的节点。

**不需要额外的"编排工具"或"规划工具"**：LLM 本身就是编排器，它的推理能力足以决定调什么工具、什么顺序、调几次。

### 4.4 多能力协同示例

重构后，以下场景将自然实现：

**场景 1：故障码 + 维修知识**
```
用户："P0101 怎么修，先查哪里"
LLM → 调 lookup_ecu_candidates("P0101") → 得到 ECU 候选
LLM → 调 lookup_repair_knowledge_titles("P0101") → 得到维修经验标题
LLM → 判断需要用户选 ECU → 调 ask_user_question
用户选择 ECU 后 →
LLM → 调 dtc_diagnosis("P0101", "选中的ECU") → 得到诊断结果
LLM → 调 get_repair_knowledge_context([匹配的entry_id]) → 得到维修详情
LLM → 综合诊断结果和维修知识 → 输出组合型回答
```

**场景 2：诊断中需要参数数据**
```
用户："尿素泵不工作，报码 P20EE"
LLM → 调 lookup_ecu_candidates("P20EE") → 得到 ECU 候选
LLM → 判断需要查针脚数据 → 调 query_parameters("后处理ECU尿素泵控制针脚")
LLM → 结合参数数据继续推理 → 给出排查建议
```

**场景 3：资料搜索 + 维修知识**
```
用户："帮我找东风天锦电路图，并说下这个故障一般怎么查"
LLM → 调 search_documents("东风天锦电路图") → 得到文档候选
LLM → 调 lookup_repair_knowledge_titles("东风天锦故障") → 得到维修经验
LLM → 综合输出：附上相关文档 + 维修排查建议
```

---

## 5. 目标架构设计

### 5.1 核心流程

重构后的 `process()` 流程简化为：

```
┌─────────────────────────────────────────────────────────┐
│  1. 接收请求                                              │
│     - 加载 message_history（Redis）                       │
│     - 如果有 ask_user_answer → 加载 deferred_state        │
│       → 构造 deferred_tool_results                        │
├─────────────────────────────────────────────────────────┤
│  2. 运行 Agent Loop                                      │
│     agent.run(                                           │
│       user_prompt=用户原始消息（不加 ROUTING_HINT）,        │
│       message_history=历史消息,                            │
│       deferred_tool_results=恢复的工具结果（如果有）         │
│     )                                                    │
│                                                          │
│     Agent 内部循环：                                       │
│     LLM 思考 → 选择工具 → 执行 → 结果回到上下文             │
│     → 继续思考 → 可能再调工具 → ... → 决定最终回答          │
├─────────────────────────────────────────────────────────┤
│  3. 处理输出                                              │
│     - 保存 message_history（Redis）                       │
│     - 如果输出是 DeferredToolRequests:                     │
│         保存 deferred_state → 返回 ask_user 响应           │
│     - 如果输出是 str:                                     │
│         检查消息历史中的工具结果：                            │
│         → 有 search_documents 结果 → 构建 documents 响应   │
│         → 有 query_parameters 结果 → 构建 param_request   │
│         → 都没有 → 返回 message 响应                       │
└─────────────────────────────────────────────────────────┘
```

### 5.2 与当前架构的对比

| 维度 | 当前架构 | 目标架构 |
|------|----------|----------|
| 意图判断 | intent_router 关键词规则硬分流 | LLM 自行判断，无硬分流 |
| 工具选择 | ROUTING_HINT 约束 LLM 只能调特定工具 | LLM 自由选择所有已注册工具 |
| 多工具组合 | 不支持（锁定单业务线） | 天然支持（Agent Loop 循环） |
| 用户消息处理 | 加 ROUTING_HINT 前缀 | 直接传原始消息 |
| 工具结果处理 | service 层 bypass 拦截（5 层） | agent 循环内处理，结束后统一提取 |
| ask_user 恢复 | 按业务线分别处理（param_query / doc_search 各一套） | 统一走 deferred_tool_results 注入 |
| 响应类型决定 | 按 intent 路由结果决定 | 按实际调用的工具结果决定 |
| 新能力接入成本 | 高（每个能力需要加 resume + build + salvage 代码） | 低（注册工具 + 写好描述即可） |

### 5.3 保留的机制

以下机制经过验证，继续保留不动：

- **Pydantic AI Agent 底座**：`Agent.run()` / `Agent.run_stream()` 的工具调用循环
- **CallDeferred 机制**：`ask_user_question` 通过 `raise CallDeferred()` 暂停 agent 循环
- **DeferredToolRequests / deferred_tool_results**：恢复暂停的 agent 循环
- **Message History 存储**：Redis 持久化消息历史，支持多轮对话
- **Deferred State 存储**：Redis 持久化暂停状态，支持跨请求恢复
- **流式输出**：`Agent.run_stream()` + SSE 事件流
- **现有工具实现**：所有工具的内部实现逻辑不变

---

## 6. 详细实施步骤

### Step 1: 简化 `AgentLoopService.process()` 主流程

**文件**: `backend/app/agent/runtime/service.py`

**改动原因**：当前 process() 有 5 层 bypass 拦截，每层都在 agent 之外处理特定工具的结果。这些拦截逻辑是因为 intent_router 锁定业务线后，service 层需要"帮"agent 做结果提取和格式转换。在纯 Agent Loop 架构中，agent 自己完成推理和工具调用循环，service 层只需要在循环结束后统一处理输出。

**具体删除的方法**：

| 方法 | 删除原因 |
|------|----------|
| `_maybe_resume_parameter_query_clarify()` | 不再在 agent 外直接调用 `parameter_query_service.query()`。用户回答后，通过 `deferred_tool_results` 注入 agent 循环，由 agent 自己决定下一步 |
| `_maybe_resume_doc_search_clarify()` | 同上，不再在 agent 外直接调用 `LegacyDocSearchAdapter.search()` |
| `_maybe_build_parameter_query_response()` | 不再在 agent 正常完成后按 business 类型去提取特定工具结果。改为统一提取 |
| `_maybe_build_doc_search_response()` | 同上 |
| `_maybe_salvage_parameter_query_on_error()` | 不再做 per-tool 的错误兜底。agent 报错就返回错误 |
| `_maybe_salvage_doc_search_on_error()` | 同上 |
| `_finalize_parameter_query_response()` | 被上述方法依赖，一起删除 |
| `_finalize_doc_search_response()` | 同上 |

**重写后的 process() 伪代码**：

```python
async def process(self, request, runtime_deps=None):
    # 1. 准备
    active_deps = runtime_deps or self._deps
    session_id = request.session_id or uuid4().hex

    # 2. 加载状态
    message_history, deferred_tool_results = self._prepare_run_state(request, ...)
    user_prompt = (request.message or "").strip() or None

    # 3. 运行 agent（LLM 自由选择工具，循环直到完成）
    with capture_run_messages() as captured:
        result = await self._agent.run(
            user_prompt=user_prompt,
            deps=active_deps,
            message_history=message_history,
            deferred_tool_results=deferred_tool_results,
        )

    # 4. 保存历史
    serialized = result.all_messages_json().decode("utf-8")
    active_deps.message_history_store.save_serialized_history(session_id, serialized)

    # 5. 处理输出
    if isinstance(result.output, DeferredToolRequests):
        # agent 需要用户输入 → 返回 ask_user
        ask_user = self._extract_ask_user_question(result.output)
        active_deps.deferred_state_store.save(session_id, DeferredState(...))
        return self._build_ask_user_response(ask_user, ...)

    # 6. 检查是否有结构化工具结果需要特殊响应类型
    messages = result.new_messages()
    doc_response = self._try_extract_documents_response(messages, ...)
    if doc_response:
        return doc_response

    param_response = self._try_extract_param_response(messages, ...)
    if param_response:
        return param_response

    # 7. 默认返回文本回答
    return self._build_message_response(content=result.output, ...)
```

### Step 2: 简化 `AgentLoopService.stream()` 流式流程

**文件**: `backend/app/agent/runtime/service.py`

**改动原因**：stream() 中的 bypass 逻辑与 process() 对称，同样需要简化。

**具体变化**：
- 移除 `_maybe_resume_parameter_query_clarify` 的流式版调用（第 325-353 行）
- 移除 `_maybe_resume_doc_search_clarify` 的流式版调用（第 355-383 行）
- 移除 `suppress_text_stream` 判断（第 398 行）—— 不再按 intent 抑制流式文本
- 移除 stream 中的 per-tool result 提取和 salvage 逻辑
- 流式输出直接传递 LLM 生成的文本 delta

### Step 3: 移除 intent router 的硬分流

**文件**: `backend/app/agent/runtime/service.py`

**改动原因**：intent_router 是当前架构中"锁定单业务线"的根源。移除它的硬分流后，LLM 才能自由选择工具。

**具体删除**：
- `_build_user_prompt()` 方法 —— 不再调用 intent_router，不再加 ROUTING_HINT 前缀
- `_decorate_user_prompt()` 方法 —— 不再存在
- `_resolve_response_business()` 方法 —— 不再根据 intent 决定响应类型
- `_intent_router` 属性初始化 —— 从 `__init__` 中移除

**文件**: `backend/app/agent/runtime/intent_router.py`
- 文件暂时保留，不删除
- 后续可用于监控/分析/日志，但不影响 agent 主流程
- 如确认完全不需要，后续再清理

### Step 4: 重写 system prompt

**文件**: `backend/app/core/config.py`

**改动原因**：当前 system prompt 有很强的"这个场景只能用这个工具"的约束，与纯 Agent Loop 的自由选择理念冲突。

**重写原则**：
1. **描述能力，不限制组合**：每个工具的描述说清楚它能做什么、适用什么场景，但不禁止在其他场景中使用
2. **鼓励多工具协同**：明确告诉 LLM 可以在一次对话中调用多个工具，综合回答
3. **保留质量约束**：回答格式（markdown、中文）、ask_user 使用规范、不编造结果等约束继续保留
4. **保留工具使用的最佳实践**：例如 search_documents 后如果结果多应该调 analyze_doc_search_ambiguity，这是操作指导而非硬约束

**新 system prompt 结构**：

```
你是 CRS 汽车维修智能助手。你可以使用以下工具来帮助用户解决问题。

## 可用工具

### 资料搜索
- search_documents: 搜索技术文档、维修手册、电路图、线路图等资料
- analyze_doc_search_ambiguity: 当搜索结果较多或存在歧义时，分析是否需要用户进一步筛选

### 故障诊断
- lookup_ecu_candidates: 根据故障码查询可能的 ECU 候选
- dtc_diagnosis: 使用故障码和 ECU 型号执行诊断分析

### 维修知识
- lookup_repair_knowledge_titles: 搜索本地维修经验库的标题目录
- get_repair_knowledge_context: 加载维修知识的详细内容

### 参数查询
- query_parameters: 查询 ECU 针脚定义、引脚编号、电压参数等

### 用户交互
- ask_user_question: 当缺少关键信息时，向用户提问。这是获取用户输入的唯一方式。

## 工作方式

- 你可以在一次对话中自由组合使用多个工具
- 根据问题需要，你可以先用一个工具获取信息，再用另一个工具补充
- 例如：诊断过程中如果需要针脚数据，可以调用 query_parameters 获取后继续诊断
- 当多个能力都与用户问题相关时，综合使用并给出组合型回答

## 使用规范

- 不要编造工具没有返回的数据
- ask_user_question 是向用户提问的唯一方式，不要在回答中用文字提问
- 用中文回答
- 使用 markdown 格式组织回答
...（保留现有的格式和质量约束）
```

### Step 5: 统一 deferred tool 恢复机制

**文件**: `backend/app/agent/runtime/service.py`

**改动原因**：当前 param_query 和 doc_search 各有一套独立的 deferred 恢复逻辑（自己的 tool_name、自己的 resume 方法、自己直接调 service）。在纯 Agent Loop 架构中，所有 deferred 都应该统一处理。

**统一方案**：

1. **所有 deferred 都通过 `ask_user_question`**：无论是参数查询澄清、文档搜索筛选还是其他场景，都由 LLM 调用 `ask_user_question` 发起
2. **恢复时统一走 `deferred_tool_results` 注入**：用户回答后，Pydantic AI 将答案注入到对应的 `ask_user_question` 工具调用中，agent 循环恢复
3. **agent 自己决定后续动作**：拿到用户回答后，LLM 根据上下文决定下一步调什么工具（可能是 `query_parameters(selection_payload=...)`，也可能是 `search_documents(selection_payload=...)`）

**需要调整的地方**：
- `ParameterQueryResponseAdapter`：不再构造独立的 deferred state，由 LLM 通过 ask_user_question 处理
- `DocSearchResponseAdapter`：同上
- `_prepare_run_state()`：简化为只处理通用的 deferred_tool_results，不再区分 tool_name

### Step 6: 保留响应类型，改为通用提取

**文件**: `backend/app/agent/runtime/service.py`

**改动原因**：前端已经有 `documents` 和 `param_request` 的渲染逻辑，不需要改动前端。后端仍然提取结构化数据并返回对应响应类型，但提取逻辑不再依赖 intent_router 的判断。

**新的提取逻辑**：

```python
def _try_extract_structured_response(self, messages, ...):
    """agent 运行完成后，检查消息历史中是否有需要特殊展示的工具结果"""

    # 检查是否有文档搜索结果
    doc_envelope = extract_latest_tool_envelope(messages, "search_documents")
    if doc_envelope and doc_envelope.get("status") == "ok":
        return self._build_documents_response(doc_envelope, ...)

    # 检查是否有参数查询结果
    param_envelope = extract_latest_tool_envelope(messages, "query_parameters")
    if param_envelope and param_envelope.get("status") == "ok":
        return self._build_param_request_response(param_envelope, ...)

    # 没有结构化结果，返回 None
    return None
```

**与当前的区别**：
- 当前：根据 `response_business`（来自 intent_router）决定去提取哪个工具的结果
- 重构后：不管 intent 是什么，统一检查所有工具结果，按优先级返回

### Step 7: 前端保持不变

**改动原因**：不需要改前端。后端仍然返回 `message`、`ask_user`、`documents`、`param_request`、`error` 这些响应类型，前端渲染逻辑完全兼容。

### Step 8: 更新测试

**文件**: `backend/tests/test_agent_loop_service.py`

**具体变化**：
- 删除 intent_router 相关测试（`test_routes_to_general_chat` 等）
- 更新 process() 测试适配新的简化流程
- 新增多工具组合调用测试：验证 agent 可以在一次运行中调用多个不同能力的工具
- 保留 ask_user deferred/resume 测试：验证 CallDeferred → 保存状态 → 用户回答 → 恢复循环

---

## 7. 验证方式

### 7.1 单元测试

```bash
pytest backend/tests/test_agent_loop_service.py -v
```

验证点：
- [x] 纯文本问答正常返回 message
- [x] ask_user deferred → 保存状态 → 恢复 → 继续循环
- [x] 文档搜索结果正确提取为 documents 响应
- [x] 参数查询结果正确提取为 param_request 响应
- [x] agent 报错正确返回 error 响应

### 7.2 集成测试（真实 LLM）

| 测试场景 | 预期行为 |
|----------|----------|
| "帮我找东风天锦电路图" | agent 调 search_documents → 返回 documents 响应 |
| "P0101 什么意思" | agent 调 lookup_ecu_candidates → 可能调 ask_user → 调 dtc_diagnosis → 返回诊断结果 |
| "报码 P0101，有没有相关资料" | agent 调多个工具（诊断 + 搜索）→ 综合回答 |
| "后处理ECU的CANH在哪个针脚" | agent 调 query_parameters → 返回 param_request 响应 |
| "尿素泵不工作，帮我看下怎么排查" | agent 调 repair_knowledge + 可能调 query_parameters → 综合维修建议 |

### 7.3 流式测试

验证 stream 接口：
- SSE 事件正常发送
- 文本 delta 实时流出
- 最终响应包含正确的响应类型

---

## 8. 关键文件清单

| 文件 | 改动类型 | 改动量 |
|------|----------|--------|
| `backend/app/agent/runtime/service.py` | **大幅简化** | 删除约 600 行 bypass 代码，重写 process() 和 stream() |
| `backend/app/core/config.py` | **重写** | system prompt 重写 |
| `backend/app/agent/runtime/factory.py` | **小调整** | 工具描述优化（可选） |
| `backend/app/agent/runtime/intent_router.py` | **解耦** | 从主流程中移除引用，文件保留 |
| `backend/app/agent/runtime/deps.py` | **不改** | 依赖注入不变 |
| `backend/app/schemas/chat.py` | **不改** | 响应类型保留 |
| `backend/app/agent/tools/registry.py` | **不改** | 工具注册保留 |
| `backend/app/agent/adapters/` | **小调整** | 移除 bypass 相关的适配方法 |
| `backend/tests/test_agent_loop_service.py` | **更新** | 适配新流程，新增组合测试 |
| `frontend/` | **不改** | 前端完全不动 |

---

## 9. 风险控制

### 9.1 风险：LLM 工具选择不准确

**描述**：移除 intent_router 后，LLM 可能在某些场景选错工具或遗漏工具。

**控制方式**：
- 通过 system prompt 的工具描述和使用指导来引导 LLM
- 如果发现特定场景下 LLM 表现不好，可以在 system prompt 中针对性补充指导（soft hint），而非硬分流
- 可以保留 intent_router 作为监控/日志工具，对比 LLM 的实际工具选择与规则判断的差异

### 9.2 风险：单轮工具调用次数过多

**描述**：LLM 自由选择可能导致一轮对话中调用过多工具，增加延迟。

**控制方式**：
- Pydantic AI Agent 有 `retries` 参数控制最大重试次数
- 可以在 system prompt 中引导 LLM 优先选择高价值工具
- 监控实际的工具调用次数，必要时加限制

### 9.3 风险：回退困难

**描述**：大幅重构 service.py 后难以回退。

**控制方式**：
- 在独立分支上开发
- 保留 intent_router.py 文件，不删除
- 如有需要可以快速恢复 ROUTING_HINT 装饰逻辑

---

## 10. 总结

本次重构的核心是**三个移除 + 一个重写**：

1. **移除 intent_router 硬分流** —— 不再在请求入口锁定单一业务线
2. **移除 service 层 bypass 逻辑** —— 不再在 agent 之外拦截和处理工具结果
3. **移除 ROUTING_HINT 装饰** —— 不再限制 LLM 的工具选择
4. **重写 system prompt** —— 描述能力而非限制组合

改完之后，系统就从"单业务线收敛器"变成了真正的"Agent Loop"——LLM 在一个上下文中自由调用工具、多次推理、直到问题解决。

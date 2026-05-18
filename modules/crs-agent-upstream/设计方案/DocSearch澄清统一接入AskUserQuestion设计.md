# DocSearch 澄清统一接入 AskUserQuestion 设计

> 编写日期：2026-03-20
> 状态：方案讨论阶段

## 一、这份文档要解决什么问题

在旧项目里，`doc_search` 不只是“搜索文档”，还承担了“判断是否需要澄清”“生成澄清选项”“把澄清问题直接返回前端”“接收用户选择后继续过滤结果”这一整套职责。

这套设计在单跳架构里能工作，但在 Agent Loop 架构里会产生一个明显的问题：

- `doc_search` 既是业务 tool，又在直接驱动用户交互
- AskUser 的入口分散在业务 tool 内部，无法形成统一的中断-恢复机制
- 前端虽然看到的是统一的澄清卡片，但后端的“问用户”逻辑实际上分散在多个业务模块里

因此，这次升级里需要明确一个设计原则：

**`doc_search` 仍然可以负责发现歧义、提炼澄清点，但真正向用户发问这件事，统一由 `ask_user_question` 承担。**

这不是删掉旧版的智能澄清能力，而是把“澄清决策”和“用户提问”拆开。

## 二、旧版 `doc_search` 当前的澄清行为

### 1. 规则澄清阈值

旧版澄清逻辑由 `ClarifyService` 驱动。

- `clarify_target_results` 默认是 5
- `clarify_result_threshold` 默认也是 5

也就是说，当结果数量仍然大于 5 条时，系统会继续判断是否需要澄清。

对应位置：

- `backend/app/services/clarify_service.py`
- `target_results`
- `result_threshold`
- `analyze()`

其中关键判断是：

- 结果数小于等于目标值，不澄清
- 结果数大于阈值，继续分析是否需要澄清维度

### 2. `doc_search` 内部直接发起澄清

旧版 `DocSearchHandler._execute_search()` 的处理流程是：

1. 先搜文档
2. 跑规则澄清
3. 如果规则澄清需要用户确认，则直接构造 `clarify_business`
4. 如果规则澄清没有收束，且结果仍然很多，则调用 `LLMClarifyService`
5. `LLMClarifyService` 基于标题差异提取特征，再直接返回 `clarify_business`

对应位置：

- `backend/app/services/handlers/doc_search_handler.py`
- `_execute_search()`
- `_build_clarify_result()`
- `_build_llm_clarify_result()`

### 3. 旧版 LLM 智能澄清的本质

这一点非常重要。旧版 LLM 智能澄清做的其实不是“问用户”，而是两步：

1. 从大量候选文档中提炼差异特征
2. 把这些差异特征包装成用户可选项

例如：

- 用户搜“发动机抖动”
- 命中了十几篇资料
- 规则维度无法有效区分
- `LLMClarifyService` 观察文件标题后提炼出：
  - 冷启动抖动
  - 怠速抖动
  - 加速抖动
  - 喷油器相关
  - 预热系统相关

旧版是把这一步的结果直接变成 `clarify_business` 返回前端。

在新版里，这个“特征提炼”能力应该保留，而且必须保留，因为它本身就是 `doc_search` 的核心业务价值之一。

## 三、升级后的核心设计原则

### 原则 1：保留 `doc_search` 的歧义分析能力

新版不能因为引入了 Agent Loop，就把旧版“结果多于 5 条时做智能澄清”的能力删掉。

恰恰相反，这部分能力应该继续存在，并作为 `doc_search` 的标准输出之一。

也就是说：

- 结果多于 5 条时，仍然允许 `doc_search` 做规则澄清分析
- 规则澄清不够时，仍然允许 `doc_search` 调用 LLM 做特征提炼
- 这部分业务判断仍由 `doc_search` 持有

### 原则 2：`doc_search` 不再直接“问用户”

新版里，`doc_search` 的职责到“发现歧义并产出澄清候选信息”为止。

它不再直接承担下面这些动作：

- 不再直接生成最终的 `clarify_business` 响应
- 不再直接把问题推给前端
- 不再自己维护“等待用户回答”的交互状态
- 不再把“用户这次回答的是哪一个问题”这件事藏在自己的上下文状态机里

这些动作统一交给 `ask_user_question`。

### 原则 3：AskUser 是统一的人机交互出口

在 Agent Loop 架构中，所有“需要用户补充信息”的场景，最终都应收敛到一个统一 skill：

- `ask_user_question`

因此，`doc_search` 只是告诉 Agent：

- 现在结果还太多
- 我已经分析出一组对用户有意义的区分选项
- 如果你觉得需要继续收束，请把这些选项交给 `ask_user_question`

然后由 Agent 决定：

- 立刻问用户
- 先结合其他 tool 结果再问
- 或者信息已经够用，直接回复

## 四、新版职责拆分

### 1. `doc_search` 的职责

新版 `doc_search` 应只保留以下职责：

- 执行文档搜索
- 对搜索结果做规则过滤和排序
- 判断结果是否足够收束
- 当结果过多时，分析最适合澄清的区分维度
- 必要时调用 LLM 提取“用户可理解的差异特征”
- 产出结构化的澄清候选结果

### 2. `ask_user_question` 的职责

`ask_user_question` 统一负责：

- 把结构化问题渲染为前端可展示的提问卡片
- 中断当前 Agent Loop
- 持久化 pending 的 tool call
- 等待用户输入
- 用户回答后，把回答作为 tool return 注入消息链
- 恢复 Agent Loop

### 3. Agent 的职责

Agent 是真正的编排者，负责：

- 解读 `doc_search` 的输出
- 判断当前是否应该问用户
- 决定调用 `ask_user_question`
- 在用户回答后，带着用户选择再次调用 `doc_search`
- 最终综合搜索结果和其他 tool 结果生成回复

## 五、目标运行方式

### 旧版链路

```text
用户提问
  -> doc_search
  -> doc_search 内部判断结果太多
  -> doc_search 内部调用规则澄清 / LLM 智能澄清
  -> doc_search 直接返回 clarify_business
  -> 用户选择
  -> doc_search 内部继续过滤
```

### 新版链路

```text
用户提问
  -> Agent
  -> Agent 调用 doc_search
  -> doc_search 返回：
     1. 搜索结果
     2. 是否需要澄清
     3. 澄清问题和选项
     4. 每个选项对应的过滤条件或文件集合
  -> Agent 判断需要继续收束
  -> Agent 调用 ask_user_question
  -> Loop 中断，等待用户
  -> 用户回答
  -> Agent 恢复
  -> Agent 带着用户选择再次调用 doc_search
  -> doc_search 返回更精确结果
  -> Agent 综合生成最终回复
```

## 六、`doc_search` 在新版里应该返回什么

为了让 AskUser 接管交互，`doc_search` 需要把“澄清分析结果”做成结构化输出，而不是直接输出一个前端响应。

建议新增一种内部返回结构，例如：

```json
{
  "status": "need_clarify",
  "query": "发动机抖动",
  "results_count": 12,
  "clarify_source": "rule|llm",
  "question": "您想查哪一种情况？",
  "options": [
    {
      "key": "冷启动抖动",
      "label": "冷启动抖动",
      "description": "启动后短时间内抖动明显"
    },
    {
      "key": "怠速抖动",
      "label": "怠速抖动",
      "description": "车辆静止怠速时抖动"
    }
  ],
  "selection_payload": {
    "冷启动抖动": {
      "filters": {
        "_llm_file_ids": ["file_1", "file_3", "file_8"]
      }
    },
    "怠速抖动": {
      "filters": {
        "_llm_file_ids": ["file_2", "file_4"]
      }
    }
  }
}
```

这个结构里最关键的是：

- `question`
- `options`
- `selection_payload`

也就是说，`doc_search` 不只要告诉 Agent“要问什么”，还要告诉 Agent“用户每个选项对应的后续搜索参数是什么”。

## 七、规则澄清和 LLM 澄清在新版中的统一抽象

新版不要把规则澄清和 LLM 澄清当成两条完全不同的交互链路，而应该统一抽象成：

- `doc_search` 的歧义分析输出

只是它们的来源不同：

- 规则澄清：来源于维度统计和过滤规则
- LLM 澄清：来源于 LLM 对标题差异的特征提炼

但对 Agent 来说，它们都应该长成同一种内部结构：

- 问题
- 选项
- 描述
- 选项对应的过滤载荷

这样 Agent 完全不需要知道：

- 这个问题是规则生成的
- 还是 LLM 提炼出来的

Agent 只需要统一地调用 `ask_user_question`。

## 八、为什么这个拆法比旧版更合理

### 1. 保留原有业务能力

旧版最有价值的部分不是“返回 clarify_business”，而是：

- 当结果很多时，仍能找到一个最适合让用户确认的区分点

这个能力在新版中被完整保留。

### 2. 统一所有 AskUser 入口

升级后，`doc_search`、`fault_diagnosis`、未来的参数查询、维修步骤确认，都走同一个 `ask_user_question`。

这样前端协议、Deferred Tool 恢复逻辑、会话状态管理都只维护一套。

### 3. 避免 `doc_search` 持有交互状态机

旧版 `DocSearchContext` 里有：

- `pending_clarify_facet`
- `clarify_round`
- `clarify_history`
- `llm_clarify_mapping`

这些字段说明 `doc_search` 当前不只是个搜索能力，而是一个带交互状态机的业务流程。

Agent Loop 版要逐步把这类“交互态”从 `doc_search` 中剥离出来，收敛到 Agent Runtime 和 AskUser。

### 4. 让 Agent 有机会做更高层决策

旧版一旦命中澄清条件，就只能立刻问用户。

新版里，Agent 在拿到 `doc_search` 的“需要澄清”信号后，还可以结合其他工具结果做更高层判断，例如：

- 先查一下故障码解释，再决定要不要问
- 先查一下维修案例，再决定是否还能直接给建议
- 如果已有上下文已经足够，也可以不问，直接生成回答

这就是 Agent Loop 相比单跳架构更强的地方。

## 九、落地建议

### 第一阶段：保留旧业务判断，改交互出口

先不要急着重写 `doc_search` 的全部内部逻辑。

第一阶段可以这样做：

- 保留旧版规则澄清和 LLM 智能澄清能力
- 但不要再让它直接返回 `clarify_business`
- 改成返回内部结构化 `need_clarify` 结果
- 由 Agent 统一调用 `ask_user_question`

这一步的目标是先把设计思想落地，而不是一次性重写所有搜索逻辑。

### 第二阶段：逐步去掉 `doc_search` 自身的交互态

后续再逐步收缩这些字段和流程：

- `pending_clarify_facet`
- `clarify_history`
- `llm_clarify_mapping`
- `handle_clarify()`

这些东西最终应迁移为：

- Agent Runtime 持有 pending deferred tool
- AskUser 持有问题定义
- `doc_search` 只接收“搜索条件”和“用户已确认条件”

### 第三阶段：把规则澄清和 LLM 澄清统一为一个输出协议

最终形成统一接口：

- `doc_search(query, filters, user_selection)`
  - 返回 `final_results`
  - 或返回 `clarify_candidate`

这样 Agent 的编排逻辑最清晰。

## 十、最终结论

这次升级里，关于 `doc_search` 澄清的设计思想应明确为：

**旧版“结果多于 5 条时做规则/LLM 澄清”的业务能力必须保留。**

**但新版里，`doc_search` 不再直接问用户，而是把澄清候选信息交给 Agent，再由 Agent 统一调用 `ask_user_question`。**

一句话概括：

**`doc_search` 负责发现该问什么，`ask_user_question` 负责真正去问。**

这条原则既保留了旧版智能澄清的业务价值，又符合 Agent Loop 下“AskUser 是统一交互出口”的整体架构方向。

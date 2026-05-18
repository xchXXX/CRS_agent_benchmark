# doc_search 多轮状态流转说明

> 文档口径提示：
> 本文保留历史阶段编号，用于说明既有多轮状态流转。
> 当前 `doc_search` 真实项目模糊用户模拟施工，统一以
> [doc_search真实项目模糊用户模拟施工方案](../implement/engineering/doc_search真实项目模糊用户模拟施工方案.md)
> 与
> [doc_search模糊用户模拟阶段总览与文档口径说明](../implement/engineering/doc_search模糊用户模拟阶段总览与文档口径说明.md)
> 为准；若本文与其冲突，以后两者为准。

## 1. 文档目的

本文档冻结阶段 1 下 benchmark 运行器的外部状态机。
目标是说明 benchmark 该如何推进会话、何时停止、何时判为协议失败。

## 2. 真源约束

本状态机只服务于当前已落地协议：

- 首轮自由文本请求
- `ask_user` 结构化澄清
- 用 `session_id + ask_user_answer + selection_payload` 恢复
- 最终返回 `documents` / `message` / `error`

## 3. 状态定义

当前外部状态固定为 7 个：

### 3.1 `INIT`

尚未发出任何请求。

### 3.2 `FIRST_REQUEST_SENT`

已发送首轮请求，正在等待首轮响应。

### 3.3 `WAITING_CLARIFY_SELECTION`

收到 `ask_user`，正在等待 benchmark 侧生成一个可提交的结构化选项选择。

### 3.4 `RESUME_REQUEST_SENT`

已带 `session_id + ask_user_answer + metadata.selection_payload` 发出恢复轮请求，正在等待恢复轮响应。

### 3.5 `TERMINAL_DOCUMENTS`

终态，最终响应为 `documents`。

### 3.6 `TERMINAL_MESSAGE`

终态，最终响应为 `message`。

### 3.7 `TERMINAL_ERROR`

终态，最终响应为 `error`，或者因为关键字段缺失而无法继续。

## 4. 当前没有“撤回状态”

当前阶段故意不定义以下状态：

- `WAITING_ROLLBACK`
- `ROLLBACK_REQUEST_SENT`
- `ROLLBACK_COMPLETED`

原因不是 benchmark 不想支持，而是当前真实协议还没有这条链路。

因此：

- “用户想撤回”可以被记录
- “真正执行撤回”当前不能进入状态机主链路

## 5. 状态转移

### 5.1 首轮

```text
INIT -> FIRST_REQUEST_SENT -> {WAITING_CLARIFY_SELECTION | TERMINAL_DOCUMENTS | TERMINAL_MESSAGE | TERMINAL_ERROR}
```

含义：

- 首轮响应是 `ask_user` 时，进入澄清等待态
- 首轮响应是 `documents` 时，直接结束
- 首轮响应是 `message` 时，直接结束
- 首轮响应是 `error` 或关键字段缺失时，直接失败结束

### 5.2 恢复轮

```text
WAITING_CLARIFY_SELECTION -> RESUME_REQUEST_SENT -> {WAITING_CLARIFY_SELECTION | TERMINAL_DOCUMENTS | TERMINAL_MESSAGE | TERMINAL_ERROR}
```

含义：

- 恢复轮仍可能再次返回 `ask_user`
- 恢复轮也可能直接返回 `documents`
- 恢复轮也可能返回 `message`
- 任一轮出现 `error` 或恢复所需字段缺失，结束于 `TERMINAL_ERROR`

## 6. 每轮必须记录的最小信息

阶段 2 的内部回合模型至少要承载以下信息：

- `turn_index`
- `request_kind`
  - `initial_message`
  - `ask_user_resume`
- `request_payload`
- `response_body`
- `response_type`
- `session_id`
- `tool_call_id`
- `selected_option_key`
- `selected_option_label`
- `selected_selection_payload`
- `is_terminal`

新增冻结：

- 还要能记录 AI 用户是否出现“故意选错”
- 还要能记录 AI 用户是否出现“想撤回”
- 还要能记录“撤回未执行”的原因

## 7. 澄清轮选择规则

当前主线规则冻结如下：

- benchmark 在 `ask_user` 到来后，只能基于当轮实际返回的选项做选择
- benchmark 不能靠预先写死“第几轮点哪个字段值”来冒充真实用户
- 后续要由 AI 用户根据当轮看到的问题和选项自主决定

## 8. 误选与撤回意图的处理口径

当前代码真源下，误选后的结果可能有三种：

1. 结果仍然很多，再次进入 `ask_user`
2. 过滤为空，返回 `message`
3. 过滤后仍有结果，但结果是错误文件，系统不会自动回退

因此当前 benchmark 口径冻结如下：

- “故意选错”属于可定义场景
- “想撤回”属于可记录场景
- “撤回真的执行成功”不属于当前主线

## 9. 停止条件

运行器必须在以下任一条件满足时停止：

- 响应 `type = documents`
- 响应 `type = message`
- 响应 `type = error`
- 缺少继续恢复所需的 `session_id`
- 缺少继续恢复所需的 `ask_user.tool_call_id`
- `clarify_options` 与 `ask_user.options` 都不可用
- 超过 `max_turns`

## 10. 协议失败条件

以下情况应判为协议失败或阻断失败：

- `ask_user` 响应缺少 `session_id`
- `ask_user` 响应缺少 `ask_user.tool_call_id`
- `ask_user` 响应没有可消费选项
- 恢复轮发送时无法构造 `metadata.selection_payload`
- 连续返回无法消费的响应类型
- 超过 `max_turns` 仍未进入任何终态

## 11. 对阶段 2 的约束

阶段 2 的内部回合模型必须服从本状态机：

- 一轮请求加一轮响应必须显式标注为首轮还是恢复轮
- 一轮 `ask_user` 必须能映射到后续一个真实提交动作
- 多轮交互轨迹必须能复盘会话如何从 `INIT` 走到终态
- 如果 AI 用户产生撤回意图，也必须被记录为“意图未执行”，而不是伪造成已经执行

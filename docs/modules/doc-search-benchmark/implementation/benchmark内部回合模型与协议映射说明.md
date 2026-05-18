# benchmark 内部回合模型与协议映射说明

> 文档口径提示：
> 本文保留历史阶段编号，用于说明既有模型到协议的映射方式。
> 当前 `doc_search` 真实项目模糊用户模拟施工，统一以
> [doc_search真实项目模糊用户模拟施工方案](../implement/engineering/doc_search真实项目模糊用户模拟施工方案.md)
> 与
> [doc_search模糊用户模拟阶段总览与文档口径说明](../implement/engineering/doc_search模糊用户模拟阶段总览与文档口径说明.md)
> 为准；若本文与其冲突，以后两者为准。

## 1. 文档目的

本文档说明阶段 2 冻结后的内部模型，如何映射到阶段 1 已冻结的外部协议。

## 2. 映射总原则

- 外部协议按真实 `/chat/completions` 请求响应承载
- 内部模型按 benchmark 自己的回合记录承载
- 一次外部请求加一次外部响应，对应内部一条 `turn`
- AI 用户场景配置只描述行为边界，不直接指定每一轮点哪个选项

## 3. 请求映射

### 3.1 首轮请求

外部协议：

- `message`
- `context`
- `mode`

内部模型映射到：

- `turn.request_kind = initial_message`
- `turn.request_payload = 首轮请求体`

### 3.2 恢复轮请求

外部协议：

- `session_id`
- `ask_user_answer.tool_call_id`
- `ask_user_answer.answer`
- `ask_user_answer.metadata.selection_payload`

内部模型映射到：

- `turn.request_kind = ask_user_resume`
- `turn.session_id = 当前 session_id`
- `turn.tool_call_id = 当前恢复使用的 tool_call_id`
- `turn.selected_option_label / key`
- `turn.selected_selection_payload`
- `turn.request_payload = 恢复轮请求体`

## 4. 响应映射

### 4.1 `documents`

外部协议：

- `type = documents`
- `content.results`

内部模型映射到：

- `turn.response_type = documents`
- `turn.response_body = 完整响应体`
- `turn.is_terminal = true`

### 4.2 `message`

外部协议：

- `type = message`
- `content.message`

内部模型映射到：

- `turn.response_type = message`
- `turn.response_body = 完整响应体`
- `turn.is_terminal = true`

### 4.3 `ask_user`

外部协议：

- `type = ask_user`
- `ask_user.tool_call_id`
- `ask_user.question`
- `clarify_options`

内部模型映射到：

- `turn.response_type = ask_user`
- `turn.tool_call_id = ask_user.tool_call_id`
- `turn.ask_user_question = ask_user.question`
- `turn.clarify_options_snapshot = 可消费选项快照`
- `turn.is_terminal = false`

## 5. AI 用户场景配置映射

AI 用户场景配置与协议的关系冻结如下：

- `user_simulation_config.driver`
  - 决定后续阶段由 AI 驱动，而不是脚本驱动
- `user_simulation_config.scenario`
  - 决定 AI 用户大致行为模式
  - 例如 `image_parsing_required` 用于表示“用户文字已知刻意受限，用户视角不能从图片中补充线索；该样本用于测试被测系统是否能依赖图片解析补足线索”的图文样本
- `user_simulation_config.wrong_selection_budget`
  - 决定最多允许故意误选几次
- `user_simulation_config.rollback_intent_mode`
  - 决定是否要出现“想撤回”的意图
- `user_simulation_config.rollback_min_round_gap`
  - 决定滞后撤回至少相隔几轮

这些字段都不直接映射到某一个 HTTP 字段，而是 benchmark 内部对 AI 用户的高层约束。

## 6. 撤回意图映射

当前代码真源下没有真实撤回协议，因此冻结如下映射：

- `turn.rollback_intent_mode`
  - 记录用户这一轮是否想撤回
- `turn.rollback_target_round`
  - 记录想回到第几轮
- `turn.rollback_supported = false`
  - 当前阶段固定表示协议不支持
- `turn.capability_gap`
  - 写明“当前 ask_user 主线不支持撤回”

也就是说：

- 撤回意图进入内部模型
- 撤回执行不进入外部协议主链路

## 7. 最终答案映射

标准答案仍按业务结果保存：

- `accepted_titles`
  - 最终正确文件名
- `accepted_pages`
  - 最终正确页码
- `accepted_page_ranges`
  - 最终正确页码范围

## 8. 五次完整重跑映射

- 同一 case 未来应支持完整重跑 5 次
- 每次都从头开始，不能复用上一次会话状态
- 每次完整执行结果都应独立记录 `attempt_index`

当前阶段只把这些信息写入模型，不在本阶段执行。

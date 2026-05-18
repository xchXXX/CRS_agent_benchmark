# benchmark 多轮运行器流程说明

> 文档口径提示：
> 本文保留历史阶段编号，用于说明既有多轮运行器流程。
> 当前 `doc_search` 真实项目模糊用户模拟施工，统一以
> [doc_search真实项目模糊用户模拟施工方案](../implement/engineering/doc_search真实项目模糊用户模拟施工方案.md)
> 与
> [doc_search模糊用户模拟阶段总览与文档口径说明](../implement/engineering/doc_search模糊用户模拟阶段总览与文档口径说明.md)
> 为准；若本文与其冲突，以后两者为准。
>
> 实现同步（2026-05-15）：
> 当前流程中，`stop` 已是运行器正式消费分支，不再归类为 `invalid_user_decision`。

## 1. 文档目的

本文说明阶段 5 多轮运行器的真实执行流程，重点解释它如何把当前代码真源下的 `/chat/completions` 多轮链路跑成闭环。

## 2. 总流程

运行器按以下顺序执行：

1. 展开 suite 下的全部 case
2. 每个 case 按 `case_repeat_count` 展开为多个 attempt
3. 每个 attempt 先发送首轮请求
4. 若响应为 `ask_user`，调用 AI 模拟用户生成结构化决策
5. 若决策为选项选择，则继续发送恢复轮请求
6. 若进入终态或触发停止原因，则结束当前 attempt

## 3. attempt 初始化

每次 attempt 都会重新创建：

- `CaseRunResult`
- `workflow.turns`
- `workflow.messages`
- 新的会话起点

这意味着：

- 同一 case 的第 2 次重跑不会继承第 1 次重跑的 `session_id`
- 同一 case 的 5 次重跑互相独立

## 4. 首轮执行

### 4.1 用户输入

首轮请求固定使用：

- `task.initial_user_message`

同时把这条消息写入：

- `workflow.messages`

### 4.2 首轮请求

运行器调用：

- `DocSearchServiceAdapter.build_initial_chat_call()`

然后发送：

- `POST /chat/completions`

### 4.3 首轮记录

首轮响应会写入：

- `workflow.turns[0]`
- `artifacts.raw_response_paths`

若此时直接返回：

- `documents`
- `message`
- `error`

则 attempt 立即结束。

## 5. ask_user 处理

当某一轮返回 `ask_user` 时，运行器会先做三件事：

1. 归一化真实选项
2. 检查能否继续恢复会话
3. 构造给 AI 模拟用户的决策提示

### 5.1 真实选项来源

运行器会同时查看：

- `ask_user.options`
- `clarify_options`

然后合并成统一选项列表。

### 5.2 继续会话的硬前提

以下任一项缺失，attempt 立即停止：

- `session_id`
- `tool_call_id`
- 可消费选项

对应停止原因分别为：

- `missing_session_id`
- `missing_tool_call_id`
- `missing_selection_payload`

## 6. AI 结构化决策

运行器会把以下内容一起交给 AI 模拟用户：

- case 指令
- 当前场景配置
- 已发生的交互轨迹
- 当前 `ask_user.question`
- 当前真实选项

当前交互轨迹就是之前讨论过的 `transcript`，这里指完整多轮记录。

### 6.1 正常选项选择

若 AI 返回 `choose_option`：

1. 运行器校验该选项确实存在于真实选项中
2. 记录：
   - `selected_option_key`
   - `selected_option_label`
   - `selected_selection_payload`
   - `user_decision_kind`
   - `user_decision_reason`
3. 把用户选择写入 `workflow.messages`
4. 构造恢复轮请求并继续执行

### 6.2 撤回意图

若 AI 返回 `declare_rollback_intent`：

1. 当前 attempt 立刻停止
2. 当前轮记录：
   - `rollback_target_round`
   - `rollback_supported = false`
   - `capability_gap`
3. `workflow.capability_gaps` 追加能力缺口文案

这里不会伪造任何“撤回成功”的协议动作。

### 6.3 合法早停

若 AI 返回 `stop`：

1. 当前 attempt 立刻停止
2. 当前轮记录：
   - `user_decision_kind = stop`
   - `user_stop_reason_code`
   - `user_decision_evidence`
3. `workflow` 记录：
   - `stopped_by_user_simulation = true`
   - `simulation_stop_count`
4. `response.final_status = stopped_by_user_simulation`
5. `workflow.stop_reason = user_simulation_stop`

### 6.4 无效决策

以下情况都记为 `invalid_user_decision`：

- 返回 `initial_message`
- 选了不存在的选项
- 模型输出的 JSON 解析失败

## 7. 恢复轮执行

当 AI 成功选中真实选项后，运行器会发送恢复轮请求：

```json
{
  "session_id": "<session_id>",
  "ask_user_answer": {
    "tool_call_id": "<tool_call_id>",
    "answer": "<selected_option_label_or_key>",
    "metadata": {
      "selection_payload": {}
    }
  }
}
```

恢复轮响应继续走同一套处理逻辑：

- 若还是 `ask_user`，继续下一轮
- 若进入终态，结束
- 若出错，结束

## 8. max_turns 处理

如果已经记录的 turn 数达到 `task.max_turns`，而当前仍停留在 `ask_user`：

- 不再发送下一轮请求
- 当前 attempt 按 `max_turns_exceeded` 停止

## 9. 原始响应落盘

每一轮响应都会独立写文件，命名至少包含：

- `case_id`
- `attempt_index`
- `turn_index`
- `request_kind`

这样可以避免：

- 同一 case 的 5 次重跑互相覆盖
- 同一 attempt 的多轮响应互相覆盖

## 10. 与 judge 的衔接

阶段 5 不改动文件命中和页码命中的 judge 入口，只负责提供更真实的运行结果：

- `workflow.turns`
- `workflow.messages`
- `prediction.top_k_documents`
- `prediction.predicted_pages`
- `response.final_status`

撤回意图类场景在当前阶段仍会因为协议能力缺口而停止，不会被伪装成成功样本。

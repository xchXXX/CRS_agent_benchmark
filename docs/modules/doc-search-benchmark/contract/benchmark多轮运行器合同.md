# benchmark 多轮运行器合同

> 文档口径提示：
> 本文保留历史阶段编号，用于说明既有多轮运行器合同。
> 当前 `doc_search` 真实项目模糊用户模拟施工，统一以
> [doc_search真实项目模糊用户模拟施工方案](../implement/engineering/doc_search真实项目模糊用户模拟施工方案.md)
> 与
> [doc_search模糊用户模拟阶段总览与文档口径说明](../implement/engineering/doc_search模糊用户模拟阶段总览与文档口径说明.md)
> 为准；若本文与其冲突，以后两者为准。
>
> 实现同步（2026-05-15）：
> 当前运行器已经正常消费 `stop`，并把它记录为
> `final_status = stopped_by_user_simulation` / `stop_reason = user_simulation_stop`，
> 不再把 `stop` 视为非法决策。

## 1. 文档目的

本文冻结阶段 5 的 benchmark 多轮运行器合同。这里的“多轮运行器”指 benchmark 侧真正驱动 `/chat/completions` 会话闭环的执行层。

## 2. 阶段 5 目标

阶段 5 只负责把以下闭环真实跑起来：

1. 发送首轮请求
2. 收到 `ask_user`
3. 调用 AI 模拟用户做结构化决策
4. 若决策为选项选择，则发送恢复轮请求
5. 直到进入终态，或因为协议能力不足而停止

## 3. 运行器输入

运行器至少消费以下输入：

- `TaskCase.initial_user_message`
- `TaskCase.request_context`
- `TaskCase.max_turns`
- `TaskCase.case_repeat_count`
- `TaskCase.user_simulation_config`
- `RunConfig.user_strategy`
- `RunConfig.user_model`
- `RunConfig.user_provider`

冻结结论：

- 首轮消息继续固定使用 `task.initial_user_message`
- `ask_user` 轮不消费 `initial_message`
- `ask_user` 轮若收到 `stop`，按合法用户模拟早停处理

## 4. 运行器执行单元

### 4.1 attempt

同一 case 的一次完整执行记为一个 `attempt`。

冻结结论：

- 同一 case 要完整重跑 `case_repeat_count` 次
- 当前默认完整重跑 5 次
- 每次完整重跑都必须新开会话
- 不允许复用上一次重跑的 `session_id`

### 4.2 turn

一次请求加一次响应记为一个 `turn`。

`request_kind` 当前只允许两类：

- `initial_message`
- `ask_user_resume`

## 5. ask_user 决策消费范围

当前真实协议下，阶段 5 运行器实际消费三类 AI 决策：

- `choose_option`
- `declare_rollback_intent`
- `stop`

其余决策处理方式：

- `initial_message`
  - 视为 `invalid_user_decision`

## 6. 真实选项归一化

运行器必须从真实响应中归一化出可消费选项。

允许读取的真实来源：

- `ask_user.options`
- `clarify_options`

每个归一化后的选项至少包含：

- `key`
- `label`
- `description`
- `selection_payload`

恢复轮实际提交时必须回传：

- `session_id`
- `ask_user_answer.tool_call_id`
- `ask_user_answer.answer`
- `ask_user_answer.metadata.selection_payload`

## 7. 停止原因

阶段 5 冻结以下停止原因：

- `documents`
- `message`
- `error`
- `max_turns_exceeded`
- `rollback_unsupported`
- `user_simulation_stop`
- `invalid_user_decision`
- `missing_session_id`
- `missing_tool_call_id`
- `missing_selection_payload`

## 8. 撤回类场景口径

当前代码真源下，一旦 AI 用户在 `ask_user` 轮表达撤回意图：

- 本次完整重跑立即停止
- 不再继续发送恢复轮请求
- 当前轮写入：
  - `user_decision_kind = declare_rollback_intent`
  - `rollback_supported = false`
  - `rollback_target_round`
  - `capability_gap`
- `workflow.capability_gaps` 追加能力缺口说明

当前固定能力缺口文案：

- `当前新版 ask_user 主线暂不支持撤回上一轮，请重新发起查询。`

## 8.1 合法早停口径

当前代码真源下，一旦 AI 用户在 `ask_user` 轮返回 `stop`：

- 本次 attempt 立即停止
- 不再继续发送恢复轮请求
- 当前轮记录：
  - `user_decision_kind = stop`
  - `user_stop_reason_code`
  - `user_decision_evidence`
- `workflow` 记录：
  - `stopped_by_user_simulation = true`
  - `simulation_stop_count`
- `response.final_status = stopped_by_user_simulation`
- `workflow.stop_reason = user_simulation_stop`

## 9. 结果落盘要求

每次 attempt 都必须完整落盘：

- `workflow.turns`
- `workflow.messages`
- `workflow.stop_reason`
- `workflow.stopped_by_user_simulation`
- `workflow.simulation_stop_count`
- `artifacts.raw_response_paths`

原始响应文件必须至少按以下维度区分，避免覆盖：

- `case_id`
- `attempt_index`
- `turn_index`
- `request_kind`

## 10. 阶段 5 代码映射

- `benchmark/doc_search_bench/envs/base.py`
  - attempt 级调度
- `benchmark/doc_search_bench/envs/doc_search/env.py`
  - 多轮运行器主循环
- `benchmark/doc_search_bench/envs/doc_search/adapters.py`
  - 首轮与恢复轮请求构造
- `benchmark/doc_search_bench/user.py`
  - AI 结构化决策调用
- `benchmark/doc_search_bench/run.py`
  - 运行参数入口

## 11. ask_user 轮数验收补充口径

当 `TaskCase.required_ask_user_rounds > 0` 时，运行器与评测层必须把它视为正式验收约束，而不是观察性 warning。

冻结规则：
- 若 `benchmark_track != search_api`
- 且 `required_ask_user_rounds > 0`
- 且本次 attempt 的 `workflow.ask_user_rounds < required_ask_user_rounds`
- 则该 attempt 进入正式阻断失败 `ASK_USER_ROUNDS_INSUFFICIENT`

该规则的目的不是要求固定脚本路径，而是确认：
- benchmark 的确接入了真实后端的 `ask_user`
- case 的确经过了真实澄清轮
- 没有把 chat case 退化成“直接返回 documents/message 的伪单轮”

# benchmark 内部回合模型合同

> 文档口径提示：
> 本文保留历史阶段编号，用于说明既有内部模型。
> 当前 `doc_search` 真实项目模糊用户模拟施工，统一以
> [doc_search真实项目模糊用户模拟施工方案](../implement/engineering/doc_search真实项目模糊用户模拟施工方案.md)
> 与
> [doc_search模糊用户模拟阶段总览与文档口径说明](../implement/engineering/doc_search模糊用户模拟阶段总览与文档口径说明.md)
> 为准；若本文与其冲突，以后两者为准。
>
> 实现同步（2026-05-15）：
> 当前内部回合模型已扩充 stop 诊断字段、用户已知线索字段与分析层 stop 统计字段；
> 以下“最小字段”以现有代码结构为准。

## 1. 文档目的

本文档冻结阶段 2 的 benchmark 内部数据模型。
这里的“内部回合模型”不是后端业务结构，而是 benchmark 为记录一次测评过程所维护的统一结构。

## 2. 名词解释

- `case`
  - 一道题
- `turn`
  - 一轮请求加一轮响应
- 交互轨迹
  - 就是完整多轮记录
  - 之前提到的 `transcript` 指的就是它
- AI 用户场景配置
  - 只描述用户的大致行为模式
  - 不预设每一轮必须点哪个选项

## 3. 阶段 2 冻结结论

阶段 2 当前冻结以下四条口径：

1. 标准答案仍以“最终文件名 + 最终页码”为准。
2. 用户模拟必须以 AI 驱动为主，不再保留点击脚本模型。
3. “故意选错”“想撤回”“滞后撤回”都要能在模型里表达。
4. 当前代码真源下，撤回类场景只能记为能力缺口，不能记成已执行能力。

## 4. 内部模型分层

### 4.1 题目层

表示一个 case 的静态定义。

必须能表达：

- 首轮问题文本
- 图片输入
- 最终标准答案
- 最大轮次
- 默认完整重跑次数
- AI 用户场景配置

### 4.2 AI 用户场景配置层

表示“这个 case 希望 AI 用户以什么行为模式参与”。

每个配置至少表达：

- 是否 AI 驱动
- 场景名称
- 是否允许故意选错
- 最多故意错几次
- 是否带撤回意图
- 如果带撤回意图，是立即撤回还是滞后撤回

### 4.3 回合层

表示 benchmark 的一轮请求响应记录。

每轮至少要表达：

- 当前第几轮
- 是首轮请求还是恢复轮请求
- 请求体
- 响应体
- 响应类型
- 会话 id
- 当前是否已结束
- 用户这一轮是否故意误选
- 用户这一轮是否产生撤回意图
- 当前协议是否支持执行该撤回
- 如果不支持，不支持原因是什么

### 4.4 重跑层

表示同一 case 的第几次完整执行。

冻结结论：

- 一个 `CaseRunResult` 表示同一 case 的一次完整执行
- 同一 case 若未来要完整重跑 5 次，则生成 5 份 `CaseRunResult`
- 每份结果必须显式带 `attempt_index`
- 每次完整执行都必须新开会话，不能复用上一次 `session_id`

## 5. 题目层最小字段

- `case_id`
- `question_text`
- `question_images`
- `accepted_titles`
- `accepted_pages`
- `accepted_page_ranges`
- `interaction_mode`
- `max_turns`
- `case_repeat_count`
- `user_simulation_config`

## 6. AI 用户场景配置最小字段

- `driver`
- `scenario`
- `wrong_selection_budget`
- `rollback_intent_mode`
- `rollback_min_round_gap`
- `notes`

说明：

- `driver`
  - 当前固定应为 `ai`
- `scenario`
  - 场景名，例如：
    - `normal`
    - `intentional_wrong_choice`
    - `intentional_wrong_then_immediate_rollback_intent`
    - `intentional_wrong_then_delayed_rollback_intent`
- `wrong_selection_budget`
  - 最多允许故意错几次
- `rollback_intent_mode`
  - `none` / `immediate` / `delayed`
- `rollback_min_round_gap`
  - 滞后撤回至少隔几轮

## 7. 回合层最小字段

- `turn_index`
- `request_kind`
- `request_payload`
- `response_http_status`
- `response_body`
- `response_type`
- `session_id`
- `business`
- `tool_call_id`
- `ask_user_question`
- `clarify_options_snapshot`
- `selected_option_key`
- `selected_option_label`
- `selected_selection_payload`
- `user_decision_source`
- `user_decision_kind`
- `user_decision_reason`
- `user_stop_reason_code`
- `user_decision_evidence`
- `user_response_text`
- `rollback_intent_mode`
- `rollback_target_round`
- `rollback_supported`
- `capability_gap`
- `is_terminal`
- `stop_reason`

## 8. 结果层最小字段

- `case_id`
- `attempt_index`
- `execution`
- `response`
- `prediction`
- `workflow`
- `validation`
- `metrics`

其中新增要求：

- `workflow` 里要能收纳能力缺口列表
- 能单独记录“用户想撤回，但协议不支持”
- `workflow` 里要能记录：
  - `stopped_by_user_simulation`
  - `simulation_stop_count`

当前 `doc_search` 真实项目阶段 4 还补充：

- `analysis`
  - `final_hit`
  - `turn_count`
  - `decision_trace`
  - `correction_count`
  - `ambiguous_turn_count`
  - `stop_reason`
  - `failure_reason`
  - `stopped_by_user_simulation`
  - `simulation_stop_count`
  - `simulation_valid_stop`
  - `user_stop_reason_code`

## 9. 与阶段 3、4、5 的边界

阶段 2 只负责把内部模型定下来并写入代码结构。

阶段 2 明确不做：

- 真正发起多轮 HTTP 请求
- 真正执行撤回请求
- 真正消费 AI 用户场景配置
- 真正把同一 case 从头重跑 5 次

这些分别属于：

- 阶段 3：会话适配器
- 阶段 4：AI 用户模拟
- 阶段 5：多轮运行器

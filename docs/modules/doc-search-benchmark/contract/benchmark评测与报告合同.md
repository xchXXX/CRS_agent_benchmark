# benchmark 评测与报告合同

> 文档口径提示：
> 本文按
> [doc_search真实项目模糊用户模拟施工方案](../implement/engineering/doc_search真实项目模糊用户模拟施工方案.md)
> 的 `阶段 4` 口径冻结；
> 当前第一批只落最小版。
>
> 实现同步（2026-05-15）：
> 当前标准报告已补充用户模拟早停相关字段：
> `stopped_by_user_simulation`、`simulation_stop_count`、`simulation_valid_stop`、
> `user_stop_reason_code`，且 `decision_trace` 已能携带 stop 证据。

## 1. 文档目的

本文冻结 `doc_search benchmark` 的评测与报告合同，重点回答两件事：

1. 终点是否命中目标文档
2. 交互过程中到底发生了什么

## 2. 当前冻结结论

当前仍保留既有评测主线：

- 正式通过线固定为 `attempt_level`
- `case_level` 仍作为稳定性辅助视图
- `能力缺口` 进入正式失败

但从当前施工线开始，报告必须补充“过程性可解释字段”。

当前 V1 judge 额外冻结两条总原则：

- `文档是否成功召回` 只依据用户前端最终可见的 `documents` 文档集判定
- judge 正式口径只消费标准化后的 `prediction.top_k_documents`，不读取后端内部未暴露候选集作为正式召回依据

## 3. V1 指标口径

V1 保留 4 个评测维度，但区分 `official` 与 `shadow`：

- `功能性`
  - `official`：`Doc Recall@K`
  - `shadow`：`Page Recall@K`
- `排序质量`
  - `official`：`Gold Doc Hit@1`、`Gold Doc Hit@3`、`Gold Doc MRR`
  - `shadow`：页级排序命中指标
- `交互效率`
  - `official`：`Total Turns`
- `系统性能`
  - `official`：`Latency`

冻结规则：

- `official gate` 只看文档层召回结果，不受页码与区块定位能力影响
- 页码相关指标当前只进入 `shadow` 观察区，不参与正式通过线
- 区块定位能力尚未正式接入前，不新增区块级正式 gate

## 4. 报告分层

### 3.1 internal artifacts

内部运行资产用于适配器调试、离线诊断、失败复盘。

允许保留：

- 原始请求体
- 原始响应体
- 原始 `selection_payload`
- 完整选项快照

### 3.2 standard reports

标准报告用于正式评测、case 汇总与用户查看。

要求：

- 不得直接暴露原始 `selection_payload`
- 可以保留：
  - `selected_option_key`
  - `selected_option_label`
  - 脱敏后的摘要
  - 哈希或标记

## 5. 当前最小新增字段

第一批范围内，标准报告当前补以下字段：

- `final_hit`
  - 最终是否命中目标文档
- `turn_count`
  - 总轮次
- `decision_trace`
  - 每轮问题、选项、选择、理由摘要
- `stop_reason`
  - 为什么停止
- `failure_reason`
  - 若失败，主要失败类型是什么
- `stopped_by_user_simulation`
  - 当前 attempt 是否因用户模拟合法早停结束
- `simulation_stop_count`
  - 当前 attempt 内发生了多少次用户模拟早停
- `simulation_valid_stop`
  - 当前早停是否属于口径认可的合法 stop
- `user_stop_reason_code`
  - 当前 stop 的主原因码
- `request_mode`
  - 本次 `/chat/completions` 首轮请求使用的 mode
  - 召回专项固定应为 `doc_search`
- `max_attempts_per_case`
  - 本次运行是否覆盖 fixture 默认 `case_repeat_count`
  - 日常烟测固定为 `1`，稳定性评测可为空

- `correction_count`
  - 发生了多少次显式纠错
- `ambiguous_turn_count`
  - 有多少轮属于近邻犹豫后决策

## 6. decision_trace 合同

`decision_trace` 至少应能回放：

1. 用户首轮说了什么
2. 每轮 `ask_user.question`
3. 每轮可见选项快照
4. 用户选了哪个 `key/label`
5. 选择理由摘要
6. `stop_reason_code / decision_evidence`
7. 后端最终回了什么类型

标准报告中的 `decision_trace` 不得直接带出：

- 原始 `selection_payload`
- `target_doc` 真值全量字段

## 7. 失败分类口径

不依赖预定义路径时，失败分析建议采用以下分类：

- `target_miss`
  - 最终没有命中目标文档
- `option_understanding_error`
  - 用户对选项文案理解错了
- `reasonable_ambiguity_miss`
  - 近邻项过于相似，误选后未纠回
- `insufficient_clarification`
  - 系统澄清链路不足，用户正常认知下无法继续收敛
- `protocol_capability_gap`
  - 真实协议能力限制导致流程停止
- `simulation_valid_stop`
  - 用户在合理认知边界下选择了合法早停
- `system_clarification_failure`
  - 用户早停暴露出系统当前澄清问题空间错误或跑偏

## 8. 能力缺口失败合同

当前正式失败码继续启用：

- `CAPABILITY_GAP_PRESENT`

冻结规则：

- 只要 `workflow.capability_gaps` 非空，就进入 `CAPABILITY_GAP_PRESENT`
- 当能力缺口已经成立时，不再额外叠加与之无关的主失败根因

## 9. 与现有文件评测口径的兼容关系

当前第一批范围内：

- `accepted_titles` 仍是文件命中主依据
- `target_doc` 是新增终点真值与失败解释真值
- 老 judge 仍可继续工作
- 新报告字段是增量补充，不要求一次性推翻现有计分框架
- `prediction.top_k_documents` 是文件命中与文档排序指标的唯一正式输入
- assistant 文本命中只作为补充告警与诊断信号，不替代可见文档集判定

## 10. 页码口径

当前页码口径继续保留：

- `disabled`
- `shadow`
- `required`

当前第一批范围不要求页码进入新的正式 gate 扩展。

## 11. 代码映射

- `benchmark/doc_search_bench/types.py`
- `benchmark/doc_search_bench/envs/doc_search/env.py`
- `benchmark/doc_search_bench/judges/contract.py`
- `benchmark/doc_search_bench/judges/file.py`
- `benchmark/doc_search_bench/judges/page.py`
- `benchmark/doc_search_bench/judges/failure.py`
- `benchmark/doc_search_bench/judges/trace.py`
- `benchmark/doc_search_bench/run.py`

## 12. 阶段 4 最小版完成标准

满足以下条件即可视为当前 `阶段 4` 最小版合同完成：

1. 标准报告能看出最终命中与否
2. 标准报告能回放基本交互过程
3. 标准报告能给出基本失败原因
4. 标准报告能产出 `correction_count / ambiguous_turn_count`
5. 标准报告不直接暴露原始 `selection_payload`
6. 正式通过线只受文档层 official 指标影响

## 13. chat train 多轮验收补充口径

当 case 明确声明 `required_ask_user_rounds > 0` 时，评测层新增正式阻断失败码：

- `ASK_USER_ROUNDS_INSUFFICIENT`
  - 含义：该 case 预期至少进入若干轮真实 `ask_user`，但本次 attempt 实际未达到要求

对应失败解释口径冻结为：
- 若未发生能力缺口，且 `ASK_USER_ROUNDS_INSUFFICIENT` 成立
- `failure_reason` 归类为 `insufficient_clarification`

这样报告能明确区分两类失败：
- 后端没有把 case 带入真实澄清链路
- 已进入澄清链路，但最终仍未命中目标文档

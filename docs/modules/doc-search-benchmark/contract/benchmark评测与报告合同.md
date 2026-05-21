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

当前第一阶段还需要补充：

3. 多目标文档 case 如何正式评分
4. 标准报告如何表达多目标命中、漏召回与覆盖率

## 2. 当前冻结结论

当前仍保留既有评测主线：

- 正式通过线固定为 `attempt_level`
- `case_level` 仍作为稳定性辅助视图
- `能力缺口` 进入正式失败

但从当前施工线开始，报告必须补充“过程性可解释字段”。

当前 V1 judge 额外冻结两条总原则：

- `文档是否成功召回` 只依据用户前端最终可见的 `documents` 文档集判定
- judge 正式口径只消费标准化后的 `prediction.top_k_documents`，不读取后端内部未暴露候选集作为正式召回依据

从“多目标文档 benchmark”第一阶段开始，文档级正式真值升级为：

- `target_docs`
- `target_match_mode`

兼容期冻结规则：

- V2 样本优先按 `target_docs` 主判定
- V1 样本允许通过兼容读取回退为单元素 `target_docs`
- 旧 `accepted_titles` 不再作为长期主判定真值，只作为兼容别名输入

## 3. 正式指标口径

当前阶段保留 4 个评测维度，但区分 `official` 与 `shadow`：

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

多目标改造后的正式解释如下：

- `Doc Recall@K`
  - 继续作为文档层正式通过线
  - 最终是否通过取决于 `target_match_mode`
- `Hit@1 / Hit@3 / MRR`
  - 继续保留
  - 多目标场景按“所有合法目标中的最佳命中 rank”计算
- `target_coverage_rate`
  - 作为多目标覆盖率指标
  - 当前进入标准报告与汇总，但不单独替代 `Doc Recall@K`

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

## 5. 多目标评测正式口径

### 5.1 正式判定输入

文档级正式判定只允许消费：

- 标准化后的 `prediction.top_k_documents`
- `gold.target_docs`
- `gold.target_match_mode`

不允许使用以下数据替代正式判定：

- assistant 文本中口头提到但用户前端未见的文档
- 后端内部候选集
- 未标准化的调试字段

### 5.2 正式判定规则

多目标 case 的文件级正式判定必须先计算：

- `matched_targets`
- `missed_targets`
- `matched_target_count`
- `target_doc_count`
- `target_coverage_rate`
- `best_target_rank`

然后再根据 `target_match_mode` 生成最终结论：

- `any_of`
  - `matched_target_count >= 1` 则 `final_hit = true`
- `all_of`
  - `matched_target_count == target_doc_count` 才 `final_hit = true`

冻结说明：

- `final_hit` 是标准报告中的最终通过字段
- `recall_hit`、`Doc Recall@K` 与 `final_hit` 在文档级正式口径下保持一致解释
- 多目标 case 的“部分命中”不能直接等价为最终通过，必须再看 `target_match_mode`

### 5.3 V1 / V2 兼容规则

兼容期内的评测读取规则冻结如下：

1. 若存在 `target_docs`，只以 `target_docs` 为主真值
2. 若不存在 `target_docs`，允许从 `target_doc + accepted_titles` 回退构造单目标判定集
3. 若 V1/V2 字段同时存在且冲突，以 V2 为准
4. 旧字段在报告中可保留兼容快照，但不得反向覆盖 V2 评分结论

## 6. 当前最小新增字段

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

多目标改造后，标准报告至少还必须补以下字段：

- `target_match_mode`
  - 当前 case 的正式多目标判定策略
- `target_doc_count`
  - 合法目标文档总数
- `target_doc_ids`
  - 合法目标文档标识集合
- `target_doc_titles`
  - 合法目标文档标题集合
- `matched_targets`
  - 本次 attempt 实际命中的目标文档集合
- `missed_targets`
  - 本次 attempt 未命中的目标文档集合
- `matched_target_count`
  - 本次命中目标数
- `target_coverage_rate`
  - 本次对目标集合的覆盖率
- `all_targets_hit`
  - 是否完成全量目标覆盖
- `best_target_rank`
  - 所有合法目标中的最佳命中 rank

兼容期说明：

- `target_doc_file_id`
  - 允许保留，但只代表首个目标文档快照
- `target_doc_title`
  - 允许保留，但只代表首个目标文档快照
- 这两个旧字段不得再被解释为完整真值

## 7. decision_trace 合同

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
- `target_docs` 真值全量字段
- `target_doc` 真值全量字段

## 8. 失败分类口径

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

多目标改造后，文件级失败语义还应显式区分：

- `MULTI_TARGET_PARTIAL_HIT`
  - 命中了部分目标，但最终未达到当前 case 判定要求
- `TARGET_SET_INCOMPLETE`
  - `all_of` 场景下只覆盖了部分目标集合

冻结解释：

- `any_of` 场景只要命中任一目标即可正式通过
- `any_of` 场景下“只命中部分目标”不应单独记为正式失败
- `all_of` 场景下未覆盖全量目标时，应明确落入 `TARGET_SET_INCOMPLETE`

## 9. 能力缺口失败合同

当前正式失败码继续启用：

- `CAPABILITY_GAP_PRESENT`

冻结规则：

- 只要 `workflow.capability_gaps` 非空，就进入 `CAPABILITY_GAP_PRESENT`
- 当能力缺口已经成立时，不再额外叠加与之无关的主失败根因

## 10. 与现有文件评测口径的兼容关系

当前第一批范围内：

- `target_docs` 是 V2 文件命中主依据
- `target_match_mode` 是 V2 正式判定策略
- `accepted_titles` 仅保留为兼容别名集合
- `target_doc` 是 V1 兼容真值入口与失败解释辅助字段
- 老 judge 需要兼容新主真值，不能继续把 `accepted_titles` 视为唯一主判定
- 新报告字段是增量补充，但最终要能独立表达多目标评分结论
- `prediction.top_k_documents` 是文件命中与文档排序指标的唯一正式输入
- assistant 文本命中只作为补充告警与诊断信号，不替代可见文档集判定

## 11. 页码口径

当前页码口径继续保留：

- `disabled`
- `shadow`
- `required`

多目标第一阶段额外冻结以下规则：

- 页码真值必须下沉到 `target_docs[i]` 维度
- case 级 `accepted_pages / accepted_page_ranges` 只允许作为 V1 兼容入口
- 页码评测当前仍保持 `shadow`
- 在目标维度页码真值未补齐前，不允许把多目标页码判定切入 `official gate`

## 12. 代码映射

- `benchmark/doc_search_bench/types.py`
- `benchmark/doc_search_bench/envs/doc_search/env.py`
- `benchmark/doc_search_bench/judges/contract.py`
- `benchmark/doc_search_bench/judges/file.py`
- `benchmark/doc_search_bench/judges/page.py`
- `benchmark/doc_search_bench/judges/failure.py`
- `benchmark/doc_search_bench/judges/trace.py`
- `benchmark/doc_search_bench/run.py`

## 13. 阶段 4 最小版完成标准

满足以下条件即可视为当前 `阶段 4` 最小版合同完成：

1. 标准报告能看出最终命中与否
2. 标准报告能回放基本交互过程
3. 标准报告能给出基本失败原因
4. 标准报告能产出 `correction_count / ambiguous_turn_count`
5. 标准报告不直接暴露原始 `selection_payload`
6. 正式通过线只受文档层 official 指标影响
7. 多目标 case 能在标准报告中明确展示命中、漏召回与覆盖率
8. V1 / V2 样本能在同一评测链路下兼容解释

## 14. chat train 多轮验收补充口径

当 case 明确声明 `required_ask_user_rounds > 0` 时，评测层新增正式阻断失败码：

- `ASK_USER_ROUNDS_INSUFFICIENT`
  - 含义：该 case 预期至少进入若干轮真实 `ask_user`，但本次 attempt 实际未达到要求

对应失败解释口径冻结为：
- 若未发生能力缺口，且 `ASK_USER_ROUNDS_INSUFFICIENT` 成立
- `failure_reason` 归类为 `insufficient_clarification`

这样报告能明确区分两类失败：
- 后端没有把 case 带入真实澄清链路
- 已进入澄清链路，但最终仍未命中目标文档

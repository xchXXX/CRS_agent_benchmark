# benchmark评测与报告流程说明

> 文档口径提示：
> 本文保留历史阶段编号，用于说明既有评测与报告流程。
> 当前 `doc_search` 真实项目模糊用户模拟施工，统一以
> [doc_search真实项目模糊用户模拟施工方案](../implement/engineering/doc_search真实项目模糊用户模拟施工方案.md)
> 与
> [doc_search模糊用户模拟阶段总览与文档口径说明](../implement/engineering/doc_search模糊用户模拟阶段总览与文档口径说明.md)
> 为准；若本文与其冲突，以后两者为准。

## 1. 文档目的

本文说明阶段 7 评测与报告的实际落地流程，也就是：

- 运行器如何产出每一次尝试结果
- judge 如何把能力缺口变成正式失败
- 报告如何同时产出 `attempt_level` 和 `case_level`

## 2. 阶段 7 冻结方案

当前阶段固定按以下顺序执行：

1. 先跑真实运行器，拿到每一次尝试的 `CaseRunResult`
2. 先做合同判定，再做文件判定、页码判定、页级 locator 判定、坐标判定
3. 尝试级汇总直接形成 `attempt_level`
4. 再把同一个 case 的多次尝试折叠成 `case_rollups`
5. 基于 `case_rollups` 再生成 `case_level`
6. 正式通过线固定读取 `attempt_level`

## 3. 运行阶段

入口在：

- `benchmark/doc_search_bench/envs/doc_search/env.py`

当前运行阶段会先产出逐次尝试结果，其中已包含：

- `attempt_index`
- `response.final_status`
- `workflow.stop_reason`
- `workflow.capability_gaps`
- `validation.blocking_failures`
- `validation.warnings`
- 文件与页码指标
- 页级 locator 指标
- 坐标级指标
- `analysis.final_hit`
- `analysis.turn_count`
- `analysis.decision_trace`
- `analysis.correction_count`
- `analysis.ambiguous_turn_count`
- `analysis.failure_reason`

这一步不负责最终报告聚合，只负责把“真发生了什么”写进结果对象。
其中过程性分析由独立模块生成，而不是混进文件召回 judge。

## 4. 判定阶段

判定顺序固定为：

1. `judge_contract()`
2. `judge_file()`
3. `judge_page()`
4. `judge_locator()`
5. `judge_coord()`

对应代码：

- `benchmark/doc_search_bench/judges/contract.py`
- `benchmark/doc_search_bench/judges/file.py`
- `benchmark/doc_search_bench/judges/page.py`
- `benchmark/doc_search_bench/judges/locator.py`
- `benchmark/doc_search_bench/judges/coord.py`
- `benchmark/doc_search_bench/judges/trace.py`

### 4.1 合同判定

合同判定当前新增了一条正式规则：

- 只要 `workflow.capability_gaps` 非空，就追加 `CAPABILITY_GAP_PRESENT`

同时冻结以下收口规则：

- 若能力缺口已经成立，不再追加 `EXPECTED_DOCUMENTS_RESPONSE`
- 这样报告里能更稳定地保留“根因是能力缺口”

### 4.2 文件判定

文件判定会把以下根阻断视为“不要继续派生文件召回失败”：

- `SCHEMA_INVALID`
- `HTTP_OR_RUNTIME_ERROR`
- `CAPABILITY_GAP_PRESENT`
- `OCR_CONTEXT_MISSING`
- `EXPECTED_DOCUMENTS_RESPONSE`

这一步的意义是：

- 防止已经明确是能力缺口的尝试，再被二次污染成 `FILE_RECALL_MISS`

### 4.3 页码判定

页码判定当前只负责：

- 识别 `disabled / shadow / required`
- 产出页码命中指标
- 在 `shadow` 模式下只保留观察性告警，不进入正式 gate

### 4.4 locator 判定

locator 判定负责：

- 判断文档命中后的页级 body_search 结果是否命中真值页
- 输出 `locator_hit_at_1 / locator_hit_at_k`

### 4.5 坐标判定

坐标判定负责：

- 在 `document_hit && page_hit` 成立后比较 `accepted_region_groups`
- 把 `highlight_boxes_px` 归一化后与 `boxes_norm` 做几何比较
- 在多页场景下应用“任意命中页命中任意合法 group 即成功”的规则
- 输出 `coord_hit / coord_failure_reason`

## 5. 尝试级汇总

尝试级汇总入口是：

- `benchmark/doc_search_bench/judges/failure.py`
- `benchmark/doc_search_bench/run.py`

这里的 `attempt_level` 是正式视图。

它会保留：

- 文件分数
- 页码分数
- locator 分数
- coord 分数
- 失败统计
- `failure_reason` 统计
- 停止原因统计
- 最终状态统计
- 能力缺口统计

当前正式 gate 读取：

- `summary.attempt_level.file.pass`

兼容字段：

- `summary.file`
- `summary.page`
- `summary.failures`

它们当前仍然指向尝试级汇总结果。

## 6. case级稳定性汇总

`case_level` 的实现分两步：

1. 先调用 `build_case_rollups()`
2. 再调用：
   - `aggregate_case_rollup_files()`
   - `aggregate_case_rollup_page()`
   - `aggregate_case_rollup_failures()`

含义是：

- 同一个 case 跑 5 次后，不再直接把 5 条结果平铺看
- 而是先形成一条稳定性摘要，再看这个 case 是否稳定

当前 rollup 会保留：

- `attempt_count`
- `pass_attempt_count`
- `pass_attempt_rate`
- `all_attempts_pass`
- `any_attempt_pass`
- `final_hit_attempt_count`
- `avg_turn_count`
- `avg_correction_count`
- `avg_ambiguous_turn_count`
- `capability_gap_attempt_count`
- `capability_gap_counts`
- `failure_reason_counts`
- `stop_reason_counts`
- `final_status_counts`
- `blocking_failure_counts`
- `warning_counts`
- `attempts`

同时每个 `attempts[*]` 摘要会补：

- `final_hit`
- `turn_count`
- `correction_count`
- `ambiguous_turn_count`
- `failure_reason`

## 7. 页码统计口径

阶段 7 后，页码报告必须显式告诉你三件事：

1. 有多少 case 或 attempt 根本没启用页码
2. 有多少 case 或 attempt 只是 shadow 观察
3. 有多少 case 或 attempt 已经进入 required 正式要求

因此报告中新增并固定：

- `disabled_cases`
- `shadow_cases`
- `required_cases`
- `shadow_eligible_cases`
- `required_eligible_cases`

其中：

- `attempt_level.page`
  - 这些数字按尝试次数统计
- `case_level.page`
  - 这些数字按唯一 case 统计

## 8. 坐标统计口径

坐标统计必须显式告诉你三件事：

1. 有多少 attempt/case 根本不具备坐标判分资格
2. 有多少是在文档未命中或页未命中处提前失败
3. 有多少真正进入了坐标比较但未命中合法区域组

因此报告中至少固定：

- `coord_eligible`
- `coord_hit_rate`
- `coord_hit_given_doc_hit_rate`
- `coord_hit_given_page_hit_rate`
- `coord_failure_reason_counts`

## 9. 当前能力缺口边界

阶段 7 不假装系统已经支持撤回。

当前边界是：

- AI 模拟用户仍然可以表达撤回意图
- 运行器会把这类结果记成能力缺口
- 能力缺口进入正式失败

因此“撤回类 case”当前仍然属于：

- 能力缺口可观测
- 正式 fail
- 暂不进入正常通过样本

## 10. 标准报告脱敏层

当前标准报告中的 `cases` 字段必须做脱敏序列化。

冻结规则：

- 保留结构化 trace 与选项文案
- 不直接暴露 `selection_payload`
- 不直接暴露 `selected_selection_payload`
- 原始 payload 只留在内部运行资产

## 11. 代码映射

- `benchmark/doc_search_bench/envs/doc_search/env.py`
  - 逐次尝试结果生产与分析收口
- `benchmark/doc_search_bench/types.py`
  - 标准报告脱敏序列化
- `benchmark/doc_search_bench/judges/contract.py`
  - 能力缺口转正式失败
- `benchmark/doc_search_bench/judges/file.py`
  - 文件口径与根阻断收口
- `benchmark/doc_search_bench/judges/page.py`
  - 页码三模式与页码汇总
- `benchmark/doc_search_bench/judges/locator.py`
  - 页级 body_search 定位判定
- `benchmark/doc_search_bench/judges/coord.py`
  - 坐标定位判定
- `benchmark/doc_search_bench/judges/failure.py`
  - 尝试级失败统计
- `benchmark/doc_search_bench/judges/trace.py`
  - 过程性分析与失败原因归因
- `benchmark/doc_search_bench/run.py`
  - `case_rollups`
  - `attempt_level`
  - `case_level`
  - `official_gate`

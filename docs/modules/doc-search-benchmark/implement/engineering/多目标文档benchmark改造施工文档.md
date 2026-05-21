# 多目标文档 benchmark 改造施工文档

## 1. 文档目的

本文用于把《多目标文档 benchmark 改造方案》落到可执行的 benchmark 施工步骤。

本施工文档只覆盖 benchmark 模块：

- `benchmark/`
- `docs/modules/doc-search-benchmark/`

不允许修改任何前后端业务代码。

## 2. 施工边界

### 2.1 允许改动

- `benchmark/doc_search_bench/types.py`
- `benchmark/doc_search_bench/judges/`
- `benchmark/doc_search_bench/run.py`
- `benchmark/doc_search_bench/envs/doc_search/`
- `benchmark/doc_search_bench/chat_export/`
- `benchmark/doc_search_bench/utils/regenerate_train_from_xls.py`
- `docs/modules/doc-search-benchmark/contract/`
- `docs/modules/doc-search-benchmark/implementation/`
- `docs/modules/doc-search-benchmark/implement/`

### 2.2 禁止改动

- `modules/crs-agent-upstream/backend/**`
- `modules/crs-agent-upstream/frontend/**`

## 3. 总施工原则

施工顺序必须固定为：

1. 先改合同与类型
2. 再改 judge 与 report
3. 再改 review 与辅助脚本
4. 最后再迁移样本数据

禁止反向施工：

- 先改样本，再逼代码适配
- 先改 report，不改 judge
- 先改页码正式口径，不补目标维度页码真值

## 4. 代码触点总览

### 4.1 类型与装载

- `benchmark/doc_search_bench/types.py`

职责：

- 定义多目标真值结构
- 兼容读取 V1 / V2 gold
- 向 `CaseRunResult` 注入新的元数据字段

### 4.2 文件级评测

- `benchmark/doc_search_bench/judges/file.py`
- `benchmark/doc_search_bench/envs/doc_search/matchers.py`

职责：

- 多目标匹配
- `any_of / all_of` 判定
- 多目标覆盖率与最佳 rank 统计

### 4.3 运行收口与聚合

- `benchmark/doc_search_bench/envs/doc_search/env.py`
- `benchmark/doc_search_bench/run.py`
- `benchmark/doc_search_bench/judges/failure.py`
- `benchmark/doc_search_bench/judges/trace.py`

职责：

- 把新评测字段写入 attempt 结果
- 更新 `case_rollups`
- 更新 suite summary 与 official gate

### 4.4 报告与 review

- `benchmark/doc_search_bench/chat_export/render_case_review_html.py`
- `benchmark/doc_search_bench/chat_export/render_first_attempt_review_html.py`
- `benchmark/doc_search_bench/chat_export/render_round_case_review_html.py`

职责：

- 展示目标文档列表
- 展示命中与漏召回目标
- 展示 `target_match_mode`

### 4.5 样本生成与迁移

- `benchmark/doc_search_bench/utils/regenerate_train_from_xls.py`
- `benchmark/doc_search_bench/envs/doc_search/data/**/*.gold.json`

职责：

- 产出 V2 gold 结构
- 分阶段迁移样本

## 5. 分阶段施工

## 5.1 阶段一：合同与类型冻结

### 目标

让 benchmark 内核先理解“多目标 case”是什么。

### 改造内容

在 `benchmark/doc_search_bench/types.py` 中新增或调整：

- `TargetDocumentTruth`
  - 增加页码真值字段
- `TaskCase`
  - 新增 `target_docs`
  - 新增 `target_match_mode`
- `TaskMetadataRecord`
  - 新增：
    - `target_doc_count`
    - `target_doc_ids`
    - `target_doc_titles`
    - `target_match_mode`

兼容读取规则：

- 有 `target_docs` 时走新路径
- 没有 `target_docs` 时，用 `target_doc + accepted_titles` 回退生成单目标集合

### 产出

- benchmark 能同时加载旧 case 与新 case

### 验收

- 旧 `gold.json` 不改也能继续加载
- 新 `gold.json` 可表达多个目标文档

## 5.2 阶段二：文件级 judge 改造

### 目标

让文件级判定从“单目标匹配”升级为“目标集合匹配”。

### 改造内容

在 `benchmark/doc_search_bench/judges/file.py` 中完成：

- 从 `accepted_titles` 主判定切换到 `target_docs` 主判定
- 先收集 `matched_targets`
- 计算：
  - `matched_target_count`
  - `missed_targets`
  - `target_coverage_rate`
  - `best_target_rank`
- 根据 `target_match_mode` 计算最终：
  - `recall_hit`
  - `hit_at_1`
  - `hit_at_3`
  - `mrr`

### 推荐口径

- `any_of`
  - 任一目标命中即可 `recall_hit=true`
- `all_of`
  - 只有目标全集覆盖完成才 `recall_hit=true`

排序指标：

- 继续按最佳命中 rank 计算

### 产出

- 多目标文件级判定结果

### 验收

- 单目标 case 分数不回退
- 多目标 `any_of` / `all_of` 得分符合预期

## 5.3 阶段三：结果类型与报告字段改造

### 目标

让 attempt 结果、标准报告、score report 能表达多目标结论。

### 改造内容

在以下位置补字段：

- `benchmark/doc_search_bench/types.py`
- `benchmark/doc_search_bench/envs/doc_search/env.py`
- `benchmark/doc_search_bench/run.py`

建议新增字段：

- `matched_targets`
- `missed_targets`
- `matched_target_count`
- `target_doc_count`
- `target_coverage_rate`
- `all_targets_hit`
- `best_target_rank`
- `target_match_mode`

### 重点

`build_case_rollups()` 也要一起升级，至少新增：

- 某 case 各 attempt 的目标覆盖情况
- 是否存在“部分命中但未全覆盖”

### 产出

- actual report 与 score report 可读可复盘

### 验收

- JSON 报告能直接看出多目标命中详情
- suite summary 不再只依赖单个目标标题

## 5.4 阶段四：失败分析口径改造

### 目标

让失败原因能区分“完全未命中”和“部分命中但未满足策略”。

### 改造内容

在以下位置补充分层失败语义：

- `benchmark/doc_search_bench/judges/failure.py`
- `benchmark/doc_search_bench/judges/trace.py`

建议新增或明确区分：

- `TARGET_SET_INCOMPLETE`
- `MULTI_TARGET_PARTIAL_HIT`

建议归因逻辑：

- `any_of` 未命中任一目标
  - 仍归为 `target_miss`
- `all_of` 只覆盖部分目标
  - 归为 `TARGET_SET_INCOMPLETE`

### 产出

- 多目标失败分析结果

### 验收

- 报告能清楚区分：
  - 完全失败
  - 部分命中
  - 已满足 `any_of`

## 5.5 阶段五：HTML review 改造

### 目标

让人工复盘页面可以直接看懂多目标 case。

### 改造内容

修改：

- `benchmark/doc_search_bench/chat_export/render_case_review_html.py`
- `benchmark/doc_search_bench/chat_export/render_first_attempt_review_html.py`
- `benchmark/doc_search_bench/chat_export/render_round_case_review_html.py`

必须展示：

- `target_match_mode`
- 全部目标文档列表
- 已命中目标文档列表
- 未命中目标文档列表
- 当前 attempt 的覆盖率

### 产出

- 多目标版 review 页面

### 验收

- review 页面不再只显示单个 `target_doc_title`

## 5.6 阶段六：页码真值重构

### 目标

消除“多目标 case 下页码真值挂在 case 级”的合同歧义。

### 改造内容

在 `target_docs` 维度下承接页码真值：

- `accepted_pages`
- `accepted_page_ranges`

当前阶段页码 judge 不要求升级为新 official gate，只需要：

- 可识别目标维度页码真值
- 在数据未迁完时继续兼容旧字段

### 产出

- 多目标兼容的页码真值结构

### 验收

- 不出现“文件命中 A，页码却拿 B 的真值比”的结构问题

## 5.7 阶段七：样本生成与迁移

### 目标

让数据生产链路能产出 V2 样本，并按风险递增顺序迁移。

### 改造内容

修改：

- `benchmark/doc_search_bench/utils/regenerate_train_from_xls.py`

迁移顺序：

1. `train`
2. `dev`
3. `test`

迁移原则：

- 保持 `case_id` 稳定
- 旧字段可短期保留
- 新字段必须成为主真值

### 产出

- 可批量生成 V2 gold 的工具链

### 验收

- 迁移后的 train / dev 能先行跑通

## 6. 推荐提交批次

建议按以下批次提交，避免一次改太大：

### 批次 1

- 合同文档
- `types.py`
- 样本双格式读取

### 批次 2

- `judges/file.py`
- `run.py`
- `env.py`

### 批次 3

- `failure.py`
- `trace.py`
- `chat_export/*.py`

### 批次 4

- 样本生成脚本
- `train/dev` 数据迁移

### 批次 5

- `test` 数据迁移
- official gate 校验

## 7. 每阶段最小验证项

### 7.1 类型阶段

- 能加载旧 gold
- 能加载含 `target_docs` 的新 gold

### 7.2 judge 阶段

- 构造 1 个 `any_of` case
- 构造 1 个 `all_of` case
- 验证通过与失败分支

### 7.3 报告阶段

- `report.actual.json`
- `report.score.json`
- HTML review

都能正确显示多目标字段

### 7.4 迁移阶段

- 先跑 `train`
- 再跑 `dev`
- 最后跑 `test`

## 8. 风险控制

### 8.1 风险一

旧字段和新字段并存期间，真实判定入口不统一。

控制：

- 主读取口径必须明确“优先 `target_docs`”

### 8.2 风险二

多目标报告改了，但聚合逻辑没改，导致 suite summary 失真。

控制：

- `build_case_rollups()` 与 suite summary 必须和 judge 同批改

### 8.3 风险三

页码结构未完成时误切 official gate。

控制：

- 页码继续 shadow，直到目标维度页码真值齐备

## 9. 最终验收口径

本轮多目标 benchmark 改造完成的最小标准如下：

1. benchmark 可同时读取 V1 与 V2 样本
2. 多目标 case 可按 `any_of / all_of` 正确评分
3. score report 能展示命中与漏召回目标
4. HTML review 能展示目标集合
5. 旧单目标 case 分数不回退
6. 不修改任何前后端业务代码

## 10. 施工结论

本次施工应视为 benchmark 内核升级，而不是单纯样本字段扩充。

真正的主线是三件事一起完成：

- 真值合同升级
- judge 与报告升级
- 样本迁移升级

只有三者一起完成，多目标文档 benchmark 才算真正落地。


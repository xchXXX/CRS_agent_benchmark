# benchmark 评测与报告合同

## 1. 文档目的

本文冻结 benchmark 如何对文档定位任务评分，以及报告如何表达“文档命中 -> 页命中 -> 坐标命中”的定位判定结果。

## 2. 当前冻结结论

1. `official gate` 仍只看文件召回；页级与坐标级作为正式定位统计维度输出。
2. 坐标 gold 必须预设。
3. 坐标 judge 只消费规则，不消费 LLM。
4. 报告必须区分：
   - 文档没召回
   - 页没命中
   - 坐标没命中

## 3. 正式判分与定位统计口径

### 3.1 `official gate`

正式输入：

- `prediction.top_k_documents`
- `gold.target_docs`
- `gold.target_match_mode`

正式通过条件：

- 只由 `file judge` 决定
- `official_gate.pass = file_summary.pass`

### 3.2 页命中定位统计

正式输入：

- `prediction.predicted_pages`
- `target_docs[*].accepted_pages`
- `target_docs[*].accepted_page_ranges`

### 3.3 坐标命中定位统计

正式输入：

- `prediction.coord_predicted_boxes_norm`
- `target_docs[i].accepted_region_groups`

前置条件：

- `document_hit = true`
- `page_hit_at_k = true`

## 4. 三层定位统计口径

报告至少输出以下维度：

- `recall_hit_rate`
- `page_hit_at_1`
- `page_hit_at_k`
- `coord_eligible`
- `coord_hit`
- `coord_hit_given_doc_hit_rate`
- `coord_hit_given_page_hit_rate`

## 5. 标准报告字段

attempt 级至少应稳定产出：

- `locator_status`
- `locator_best_page`
- `locator_top_pages`
- `locator_viewer_token_present`
- `locator_preview_present`
- `coord_predicted_page_numbers`
- `coord_predicted_boxes_norm`
- `coord_hit`
- `coord_hit_page_numbers`
- `coord_hit_group_ids`
- `coord_failure_reason`
- `coord_viewer_token_present`
- `coord_metadata_present`

case rollup 级至少应稳定产出：

- `locator_hit_at_1_rate`
- `locator_hit_at_k_rate`
- `coord_hit_rate`
- `coord_hit_given_doc_hit_rate`
- `coord_hit_given_page_hit_rate`
- `coord_failure_reason_counts`

suite / overall summary 级至少应稳定产出：

- `official_gate`
- `attempt_level.file/page/locator/coord`
- `case_level.file/page/locator/coord`

## 6. 失败分类

至少统一以下失败码：

- `DOC_RECALL_MISS`
- `PAGE_RECALL_MISS`
- `BODY_SEARCH_MISSING`
- `COORD_METADATA_MISSING`
- `COORD_BOX_MISSING`
- `COORD_REGION_MISS`

解释：

- `DOC_RECALL_MISS`
  - 文档未召回
- `PAGE_RECALL_MISS`
  - 文档已召回，但无页命中
- `BODY_SEARCH_MISSING`
  - 文档已召回，但没有可消费的定位结果
- `COORD_METADATA_MISSING`
  - 坐标框存在，但无页尺寸元数据
- `COORD_BOX_MISSING`
  - 命中页上没有可判分坐标框
- `COORD_REGION_MISS`
  - 已进入坐标比较，但未命中任何合法区域组

## 7. 术语说明

### 7.1 `highlight_boxes_px`

- 系统返回的坐标结果
- 先归一化，再比较

### 7.2 `viewer_token`

- 预览令牌
- 只作为辅助取数入口

### 7.3 `metadata`

- 页尺寸信息
- 用于像素框归一化

## 8. 统计视图

### 8.1 attempt 级

给出单次运行的三层 gate 结果。

### 8.2 case 级

汇总同一 case 多次 attempt 的坐标稳定性。

### 8.3 suite / overall 级

至少输出：

- `coord_hit_rate`
- `coord_hit_given_doc_hit_rate`
- `coord_hit_given_page_hit_rate`
- `coord_failure_reason_counts`

## 9. 明确排除

报告与评分链路明确不使用：

- LLM 审核结论
- 运行后人工主观判图
- 像素坐标 gold

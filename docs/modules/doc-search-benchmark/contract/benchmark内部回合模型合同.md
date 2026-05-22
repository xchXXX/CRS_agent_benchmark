# benchmark 内部回合模型合同

## 1. 文档目的

本文冻结 benchmark 内部运行时模型如何承接文档命中、页命中与坐标命中信息。

这里的“内部回合模型”是 benchmark 自己的运行时结构，不是后端业务协议。

## 2. 当前冻结结论

1. `official gate` 仍只由文件召回决定；页级与坐标级作为定位统计维度进入内部模型。
2. 内部模型必须同时保留页级与坐标级事实。
3. 坐标 gold 通过 `accepted_region_groups` 进入运行时。
4. `highlight_boxes_px`、`viewer_token` 必须作为运行时输出事实承接；`metadata` 只要求被运行时消费以完成归一化，不强制原样落盘。
5. 不引入 LLM 判分字段。

## 3. 题目层最小字段

`TaskCase` 至少需要承接：

- `case_id`
- `question_text`
- `question_images`
- `accepted_titles`
- `accepted_pages`
- `accepted_page_ranges`
- `target_docs`
- `target_match_mode`
- `interaction_mode`
- `max_turns`
- `case_repeat_count`
- `user_simulation_config`

目标文档级新增要求：

- `target_docs[i].accepted_region_groups`

## 4. 目标文档层最小字段

`TargetDocumentTruth` 至少需要承接：

- `file_id`
- `title`
- `doc_path`
- `facets`
- `accepted_pages`
- `accepted_page_ranges`
- `locator_keywords`
- `accepted_region_groups`

`accepted_region_groups[i]` 至少需要承接：

- `group_id`
- `page_number`
- `label`
- `boxes_norm`
- `match_mode`

## 5. prediction 层最小字段

`prediction` 至少需要承接以下定位结果：

- `locator_source`
- `body_search_status`
- `body_search_best_page`
- `body_search_top_pages`
- `body_search_viewer_token_present`
- `body_search_preview_present`
- `locator_status`
- `locator_best_page`
- `locator_top_pages`
- `locator_viewer_token_present`
- `locator_preview_present`

坐标级新增字段：

- `coord_predicted_page_numbers`
- `coord_predicted_boxes_px`
- `coord_predicted_boxes_norm`
- `coord_viewer_token`
- `coord_metadata_present`

说明：

- `coord_predicted_boxes_px`
  - 承接系统实际返回的页内像素框
- `coord_predicted_boxes_norm`
  - 承接归一化后的判分输入

## 6. metrics 层最小字段

`metrics` 至少需要承接：

- `recall_hit`
- `page_hit_at_1`
- `page_hit_at_k`
- `exact_page_hit`
- `locator_hit_at_1`
- `locator_hit_at_k`
- `locator_exact_page_hit`
- `locator_range_overlap_hit`

坐标级新增字段：

- `coord_eligible`
- `coord_hit`
- `coord_hit_page_numbers`
- `coord_hit_group_ids`
- `coord_failure_reason`
- `coord_metadata_present`
- `coord_viewer_token_present`

## 7. task_metadata 层最小字段

`task_metadata` 至少需要承接：

- `accepted_pages`
- `accepted_page_ranges`
- `target_doc_count`
- `target_doc_ids`
- `target_doc_titles`
- `locator_keywords`
- `target_match_mode`

坐标级新增字段：

- `accepted_region_groups`
- `coord_gold_page_numbers`
- `coord_gold_group_ids`

## 8. 术语映射

### 8.1 `highlight_boxes_px`

内部模型解释：

- 系统输出的页内高亮框
- 是 benchmark 的被测输出

### 8.2 `viewer_token`

内部模型解释：

- 预览令牌
- 只负责辅助恢复页尺寸与高亮上下文

### 8.3 `metadata`

内部模型解释：

- 命中页尺寸元数据
- 用来把像素框归一化

## 9. 阶段边界

内部模型本期必须支持坐标级承接，但依然遵守以下边界：

- 不修改后端协议
- 不引入额外业务接口
- 不让 LLM 参与正式判分

# benchmark 多轮运行器合同

## 1. 文档目的

本文冻结多轮运行器如何在真实会话结束后，把文档命中、页命中与坐标命中所需的事实稳定落盘。

## 2. 运行器输入边界

运行器继续消费：

- `TaskCase.initial_user_message`
- `TaskCase.request_context`
- `TaskCase.max_turns`
- `TaskCase.case_repeat_count`
- `TaskCase.user_simulation_config`
- `TaskCase.target_docs`
- `TaskCase.target_match_mode`

坐标真值只来自样本装配结果：

- `target_docs[i].accepted_region_groups`

运行器不负责生产 gold，不负责审核 gold。

## 3. 运行器输出边界

当最终响应为 `documents` 且文档结果带 `body_search` 时，运行器必须把以下信息落入标准结果：

- 页级字段
  - `locator_status`
  - `locator_best_page`
  - `locator_top_pages`
- 坐标级字段
  - `coord_predicted_page_numbers`
  - `coord_predicted_boxes_px`
  - `coord_predicted_boxes_norm`
  - `coord_viewer_token`
  - `coord_metadata_present`

## 4. 与后端字段的映射

运行器只消费现有返回：

- `content.results[i].body_search.status`
- `content.results[i].body_search.best_hit.page_number`
- `content.results[i].body_search.best_hit.highlight_boxes_px`
- `content.results[i].body_search.top_hits[].page_number`
- `content.results[i].body_search.top_hits[].highlight_boxes_px`
- `content.results[i].body_search.viewer_token`

如果能从 viewer 链路获取页元数据，运行器至少需要：

- 用这些元数据完成 `highlight_boxes_px -> boxes_norm` 归一化
- 在标准结果中保留 `coord_metadata_present`

## 5. 运行器对三层 gate 的职责

### 5.1 文档层

继续落盘文件召回事实，不变。

### 5.2 页层

继续落盘定位页事实，不变。

补充约束：

- 运行时先基于 `target_docs/accepted_titles` 解析目标文档结果
- 再只从这些目标文档结果提取页码事实

### 5.3 坐标层

新增职责：

- 从目标文档结果提取坐标框
- 从 viewer metadata 恢复页尺寸
- 把像素框归一化后落盘

运行器只负责“把事实写全”，不在这里做最终通过判断。

## 6. attempt 结果必带字段

每次 attempt 至少必须带出：

- `prediction.locator_source`
- `prediction.locator_status`
- `prediction.locator_best_page`
- `prediction.locator_top_pages`
- `prediction.locator_viewer_token_present`
- `prediction.locator_preview_present`
- `prediction.coord_predicted_page_numbers`
- `prediction.coord_predicted_boxes_px`
- `prediction.coord_predicted_boxes_norm`
- `prediction.coord_viewer_token`
- `prediction.coord_metadata_present`
- `metrics.locator_hit_at_1`
- `metrics.locator_hit_at_k`
- `metrics.coord_eligible`
- `metrics.coord_hit`
- `metrics.coord_failure_reason`

## 7. 失败语义

运行器链路需要为后续 judge 保留以下失败上下文：

- 文档未命中
- 页未命中
- `body_search` 缺失
- metadata 缺失
- 坐标框缺失

这些事实必须能在 attempt 结果中被推导出来。

## 8. V1 / V2 兼容

兼容规则不变：

1. 若 `TaskCase.target_docs` 已存在，直接消费。
2. 若不存在，允许由装配层回退构造。
3. 运行器内部只消费统一后的结构。

坐标补充规则：

- 若样本没有 `accepted_region_groups`，运行器仍可落盘坐标输出，但后续 judge 应视为 `coord_eligible=false`
- 不允许在运行器里发明新的 case 级坐标真值字段

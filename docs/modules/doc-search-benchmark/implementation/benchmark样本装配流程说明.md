# benchmark 样本装配流程说明

## 1. 文档目的

本文说明 `fixture` 与 `gold` 如何装配成运行时 `TaskCase`，以及坐标真值如何进入 benchmark 运行时。

## 2. 装配入口

当前装配入口：

- `benchmark/doc_search_bench/types.py`
- `merge_suite_from_paths()`

装配顺序固定为：

1. 读取 `fixture.json`
2. 读取 `gold.json`
3. 按 `case_id` 对齐
4. 组装运行时 `TaskCase`

## 3. fixture 负责什么

`fixture` 只负责运行输入与用户认知：

- `question_text`
- `question_images`
- `initial_user_message`
- `user_simulation_config`
- `user_profile`

## 4. gold 负责什么

`gold` 负责真值装配：

- `target_docs`
- `target_match_mode`
- `accepted_pages`
- `accepted_page_ranges`
- `accepted_region_groups`

其中：

- `target_docs[*].locator_keywords`
  - 作为目标文档级定位关键词真值

## 5. 装配原则

### 5.1 用户认知与真值严格分层

装配后必须保持：

- `user_profile` 只给模拟用户
- `target_docs` 与 `accepted_region_groups` 只给 judge / 报告

### 5.2 页级与坐标级分层

装配后必须同时具备：

- 页级真值
  - `accepted_pages / accepted_page_ranges`
- 坐标级真值
  - `accepted_region_groups`

### 5.3 不新增伪真值字段

禁止新增：

- `accepted_locator_pages`
- `accepted_locator_page_ranges`
- case 级像素坐标 gold

## 6. `accepted_region_groups` 装配要求

每个 `TargetDocumentTruth` 必须允许装配：

- `accepted_region_groups`

每个 group 至少包含：

- `group_id`
- `page_number`
- `label`
- `boxes_norm`
- `match_mode`

推荐默认：

- `match_mode = any_box`

## 7. 运行时装配结果

装配完成后，运行时对象至少应能稳定拿到：

- `target_docs`
- `target_match_mode`
- `accepted_pages`
- `accepted_page_ranges`
- `accepted_region_groups`

并把以下聚合信息带入 `task_metadata`：

- `locator_keywords`
- `coord_gold_page_numbers`
- `coord_gold_group_ids`

## 8. 兼容策略

兼容期内：

- 老样本没有 `accepted_region_groups` 也能运行
- 此类样本只是不具备坐标判分资格
- 不允许从运行时坐标输出反推生成 gold

## 9. 代码映射

- `benchmark/doc_search_bench/types.py`
  - 样本解析与运行时装配
- `benchmark/doc_search_bench/utils/regenerate_train_from_xls.py`
  - 训练集样本生成
- `benchmark/doc_search_bench/envs/doc_search/data/`
  - 样本目录

## 10. 完成标准

满足以下条件即可视为装配链路完成：

1. 老样本装配不回归。
2. 新样本能稳定拿到 `accepted_region_groups`。
3. 运行时页级与坐标级真值边界清楚。
4. 样本合同里不再保留“坐标以后再做”的旧口径。

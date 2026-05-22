# body_search 文档定位 benchmark 施工文档

## 1. 文档目的

本文把《body_search 文档定位 benchmark 方案》落成可施工版本。

施工目标不是再做页级 locator，而是基于现有后端返回，把文档内文字坐标定位 benchmark 在 `benchmark/` 内完整接上。

## 2. 施工边界

### 2.1 允许改动

- `benchmark/doc_search_bench/**`
- `benchmark/tests/**`
- `docs/modules/doc-search-benchmark/**`

### 2.2 禁止改动

- `modules/crs-agent-upstream/backend/**`
- `modules/crs-agent-upstream/frontend/**`

## 3. 总施工原则

1. 先同步文档与合同。
2. 再补类型与样本装配。
3. 再接响应归一化。
4. 再接坐标 judge 与报告。
5. 最后补测试与样本。

不允许反向施工，不允许先改后端，不允许把 LLM 引入判分。

## 4. 冻结口径

### 4.1 三层 gate

固定顺序：

1. 文档命中
2. 页命中
3. 坐标命中

### 4.2 gold 位置

坐标真值位置固定为：

- `target_docs[i].accepted_region_groups`

### 4.3 多页成功条件

只要系统命中页集合中，任意一页命中任意合法 `accepted_region_group`，即判定坐标成功。

### 4.4 坐标真值格式

正式真值只保存：

- `boxes_norm`

不保存像素坐标作为正式 gold。

### 4.5 LLM 边界

以下全部禁止：

- LLM 标 gold
- LLM 判通过
- LLM 复审

## 5. 代码触点

### 5.1 类型与样本装配

- `benchmark/doc_search_bench/types.py`

职责：

- 新增 `AcceptedRegionGroup`
- 新增坐标 box 结构
- 把 `accepted_region_groups` 接入 `TargetDocumentTruth`
- 把坐标相关元数据接入 `TaskMetadataRecord / PredictionRecord / MetricsRecord`

### 5.2 响应归一化

- `benchmark/doc_search_bench/envs/doc_search/env.py`

职责：

- 读取 `body_search.best_hit.highlight_boxes_px`
- 读取 `body_search.top_hits[].highlight_boxes_px`
- 记录 `viewer_token`
- 消费 `metadata.width_px / height_px` 完成归一化，并记录 `coord_metadata_present`
- 把像素框转换为归一化框

### 5.3 判分

- `benchmark/doc_search_bench/judges/locator.py`
- 新增 `benchmark/doc_search_bench/judges/coord.py`

职责：

- `locator.py` 继续负责文档内页命中判定
- `coord.py` 专门负责坐标命中判定

### 5.4 汇总与报告

- `benchmark/doc_search_bench/run.py`

职责：

- attempt 级结果落盘
- case rollup
- suite / overall summary
- coord 维度统计与失败分类

### 5.5 测试

- `benchmark/tests/**`

职责：

- 覆盖装配、归一化、判分、汇总和兼容链路

## 6. 施工阶段

### 6.1 阶段一：合同与文档冻结

目标：

- 统一 `accepted_region_groups` 口径
- 统一 `label / boxes_norm / match_mode` 语义
- 统一 `viewer_token / metadata / highlight_boxes_px` 的解释

验收：

- 所有相关文档不再保留“坐标暂不接入正式 benchmark”的旧表述

### 6.2 阶段二：类型与装配

目标：

- benchmark 运行时能稳定装配坐标真值

改造点：

- `TargetDocumentTruth.accepted_region_groups`
- `TaskMetadataRecord.accepted_region_groups`
- 兼容无坐标样本

验收：

- 老样本不报错
- 新样本能读到区域组真值

### 6.3 阶段三：响应归一化

目标：

- 从现有后端返回中抽取坐标输出

改造点：

- 读取 `highlight_boxes_px`
- 读取 `viewer_token`
- 解析 viewer metadata 页尺寸
- 生成 `coord_predicted_boxes_norm`

验收：

- 命中页有框时，标准结果能看到归一化框
- metadata 缺失时，能稳定输出失败码而不是 silent pass

### 6.4 阶段四：坐标 judge

目标：

- 正式计算坐标是否命中 gold

判定顺序：

1. 若文档未命中，直接 `DOC_RECALL_MISS`
2. 若页未命中，直接 `PAGE_RECALL_MISS`
3. 若无框或无 metadata，按坐标链路失败码处理
4. 若在命中页集合内任一 group 命中，则 `coord_hit=true`

验收：

- 单页单框命中通过
- 单页多框 group 命中通过
- 多页任意一页命中通过
- 非命中页上的框不能偷跑通过

### 6.5 阶段五：报告与汇总

目标：

- 把坐标维度纳入 attempt / case / suite / overall

新增内容：

- `coord_hit_rate`
- `coord_hit_given_doc_hit_rate`
- `coord_hit_given_page_hit_rate`
- `coord_failure_reason_counts`

验收：

- 报告能明确区分文档失败、页失败、坐标失败

### 6.6 阶段六：测试与样本

目标：

- 用最小样本集覆盖坐标 benchmark 主链

新增测试至少包括：

1. `accepted_region_groups` 装配正确
2. 像素框归一化正确
3. metadata 缺失失败
4. group 多框命中成功
5. 多页任意命中成功
6. 页未命中时不进入坐标通过
7. 文档未命中时坐标不具备资格

## 7. 字段实施说明

### 7.1 `accepted_region_groups`

字段含义：

- 某个目标文档内允许命中的坐标区域组

组内字段：

- `group_id`
- `page_number`
- `label`
- `boxes_norm`
- `match_mode`

### 7.2 `highlight_boxes_px`

字段含义：

- 系统在页内定位后返回的像素框

benchmark 动作：

- 不直接与 gold 比较
- 必须先归一化

### 7.3 `viewer_token`

字段含义：

- 预览令牌，封装命中页与高亮框上下文

benchmark 动作：

- 记录存在性
- 作为 metadata 获取入口
- 不作为成功条件

### 7.4 `metadata`

字段含义：

- 命中页尺寸元信息

benchmark 动作：

- 用 `width_px / height_px` 做坐标归一化，但标准结果不强制落具体宽高值

## 8. 失败码

本期至少统一以下失败码：

- `DOC_RECALL_MISS`
- `PAGE_RECALL_MISS`
- `BODY_SEARCH_MISSING`
- `COORD_METADATA_MISSING`
- `COORD_BOX_MISSING`
- `COORD_REGION_MISS`

## 9. 分工建议

### 9.1 文档与合同

负责：

- 方案文档
- 施工文档
- 合同与流程文档同步

### 9.2 类型、装配、归一化

负责：

- `types.py`
- `env.py`
- 装配与归一化测试

### 9.3 judge、汇总、报告

负责：

- `judges/coord.py`
- `locator.py` 对齐
- `run.py`
- 汇总与回归测试

## 10. 最终验收标准

满足以下条件才算本轮施工完成：

1. 文档与合同已统一为坐标 benchmark 口径。
2. benchmark 能读取并归一化 `highlight_boxes_px`。
3. benchmark 能在文档命中、页命中后正式判坐标命中。
4. 多页任意命中规则已实现。
5. 报告能输出坐标维度结果与失败原因。
6. 全量相关测试通过。

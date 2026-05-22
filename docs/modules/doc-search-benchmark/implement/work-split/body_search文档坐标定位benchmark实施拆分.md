# body_search 文档坐标定位 benchmark 实施拆分

## 1. 文档目的

本文给执行阶段和子代理分工使用，确保并行施工时写集不重叠、验收口径一致。

## 2. 固定前提

- 不改前后端业务代码
- 只改 `benchmark/` 与 `docs/modules/doc-search-benchmark/`
- 以三层 gate 为唯一方案口径
- 不允许 LLM 参与标注、判分、审核

## 3. 子任务拆分

### 3.1 子任务 A：文档与合同同步

写入边界：

- `docs/modules/doc-search-benchmark/contract/**`
- `docs/modules/doc-search-benchmark/implementation/**`
- `docs/modules/doc-search-benchmark/implement/**`

交付物：

- 方案文档
- 施工文档
- 合同与流程文档全量同步

验收点：

- 不再出现“第一阶段不做坐标判定”的旧口径
- `accepted_region_groups`、`label`、`boxes_norm` 语义一致

### 3.2 子任务 B：类型、装配、归一化

写入边界：

- `benchmark/doc_search_bench/types.py`
- `benchmark/doc_search_bench/envs/doc_search/env.py`
- `benchmark/tests/test_doc_search_body_search_locator_contract.py`
- `benchmark/tests/test_doc_search_body_search_locator_normalization.py`

交付物：

- 坐标真值类型
- 运行时装配
- `highlight_boxes_px -> boxes_norm` 归一化

验收点：

- 新样本可装配
- metadata 缺失可稳定报错
- 老样本兼容

### 3.3 子任务 C：坐标 judge、汇总与回归

写入边界：

- `benchmark/doc_search_bench/judges/coord.py`
- `benchmark/doc_search_bench/judges/locator.py`
- `benchmark/doc_search_bench/run.py`
- `benchmark/tests/test_doc_search_body_search_locator_regression.py`

交付物：

- 坐标判分
- case / suite / overall coord 汇总
- 回归测试

验收点：

- 文档未命中不进入坐标成功
- 页未命中不进入坐标成功
- 多页任意命中生效

## 4. 主协调器职责

- 冻结方案口径
- 分发子任务
- 回收 return packet
- 处理冲突
- 运行最终测试
- 做最终收口

## 5. 合并顺序

1. 先收文档与合同
2. 再收类型与归一化
3. 最后收 judge 与汇总
4. 主协调器统一跑测试

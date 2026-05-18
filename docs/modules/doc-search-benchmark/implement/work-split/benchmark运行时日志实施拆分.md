# benchmark 运行时日志实施拆分

## 1. 文档目的

本文把运行时日志实施拆成单模块 Codex 可直接消费的交付顺序，避免代码和文档交错漂移。

## 2. 拆分顺序

### 2.1 文档冻结

交付物：

- `contract/benchmark运行时日志合同.md`
- `implement/logic/benchmark运行时日志与代码映射.md`
- `implement/engineering/benchmark运行时日志接入方案.md`

验收点：

- 中文事件码冻结
- 日志格式冻结
- 摘要自动生成规则冻结

### 2.2 日志器实现

交付物：

- `benchmark/doc_search_bench/observability/runtime_logger.py`

验收点：

- 能实时写 `benchmark/reports/runs/<run_id>/runtime.log`
- 单次运行产物能集中落到 `benchmark/reports/runs/<run_id>/`

### 2.3 外层调度接入

交付物：

- `benchmark/doc_search_bench/envs/base.py`
- `benchmark/doc_search_bench/run.py`

验收点：

- 能看到 run / suite / case / attempt 主线

### 2.4 多轮运行器与用户模拟接入

交付物：

- `benchmark/doc_search_bench/envs/doc_search/env.py`
- `benchmark/doc_search_bench/user.py`

验收点：

- 能看到 request / response / ask_user / 用户模拟 / capability gap

### 2.5 judge 与报告接入

交付物：

- `benchmark/doc_search_bench/run.py`
- `benchmark/README.md`
- `benchmark/local-runbook.md`

验收点：

- 报告顶层带日志路径
- README 和 runbook 能告诉人从哪里读日志

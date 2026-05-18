# benchmark 运行时日志与代码映射

## 1. 文档目的

本文维护“运行时日志逻辑 -> benchmark 代码路径/入口”的映射，避免后续继续把日志逻辑散落到各处。

## 2. 逻辑到代码映射

### 2.1 主日志写入器

- 逻辑职责
  - 固定主行格式
  - 生成中文摘要
  - 写入 `runs/<run_id>/runtime.log`
  - 维护单次运行目录
- 代码路径
  - `benchmark/doc_search_bench/observability/runtime_logger.py`

### 2.2 run / suite / case / attempt 级调度日志

- 逻辑职责
  - `运行开始`
  - `套件开始`
  - `用例开始`
  - `尝试开始`
  - `运行异常`
- 代码路径
  - `benchmark/doc_search_bench/envs/base.py`
  - `benchmark/doc_search_bench/run.py`

### 2.3 请求预处理与多轮运行器日志

- 逻辑职责
  - `请求预处理`
  - `预处理完成`
  - `预处理阻断`
  - `发送请求`
  - `收到响应`
  - `识别澄清问题`
  - `用户选择已提交`
  - `用户模拟触发早停`
  - `发现能力缺口`
  - `尝试停止`
  - `尝试完成`
- 代码路径
  - `benchmark/doc_search_bench/envs/doc_search/env.py`

### 2.4 用户模拟 agent 行为日志

- 逻辑职责
  - `开始用户模拟决策`
  - `用户模拟模型调用`
  - `用户模拟输出非法`
  - `用户模拟校验失败`
  - `用户模拟符号决策`
  - `完成用户模拟决策`
  - `用户模拟决策失败`
  - 输出 `stop_reason_code / evidence` 摘要
- 代码路径
  - `benchmark/doc_search_bench/user.py`
  - `benchmark/doc_search_bench/envs/doc_search/env.py`

### 2.5 judge 与轨迹分析日志

- 逻辑职责
  - `合同判定完成`
  - `文件判定完成`
  - `页码判定完成`
  - `轨迹分析完成`
- 代码路径
  - `benchmark/doc_search_bench/envs/doc_search/env.py`
  - `benchmark/doc_search_bench/judges/`

### 2.6 报告写入与日志索引

- 逻辑职责
  - `报告写入完成`
  - `运行完成`
  - 报告顶层补 `runtime_log_path`
- 代码路径
  - `benchmark/doc_search_bench/run.py`

## 3. 证据跳转关系

主观测链路固定为：

1. 先看 `benchmark/reports/runs/<run_id>/runtime.log`
2. 再按 `路径:` 跳到 `benchmark/reports/runs/<run_id>/raw/*.json`
3. 再看 `benchmark/reports/runs/<run_id>/report.actual.json`
4. 最后看 `benchmark/reports/runs/<run_id>/report.score.json`

## 4. 维护要求

- 新增日志事件时，先补本文档再改代码
- 新增代码入口时，必须在本文补充逻辑映射
- 不允许把运行时日志逻辑直接塞进前后端代码

# CRS Agent Benchmark

## 1. 目标

`benchmark/` 只负责 `doc_search` benchmark 的构建、运行、评分与失败分析，不负责业务功能实现。

当前 benchmark 的正式边界是：

- 测文件命中能力
- 页码字段进入任务模型与报告结构
- 页码结果当前只做 shadow report，不参与 official gate

## 2. 阅读顺序

建议人和 AI 都按这个顺序阅读：

1. 先看本文件
2. 再看 `docs/modules/doc-search-benchmark/implement/engineering/benchmark重构工程实施蓝图.md`
3. 再看 `docs/modules/doc-search-benchmark/contract/benchmark运行时日志合同.md`
4. 再看 `docs/modules/doc-search-benchmark/implement/engineering/benchmark运行时日志接入方案.md`
5. 再看 `benchmark/doc_search_bench/types.py`
6. 再看 `benchmark/doc_search_bench/envs/doc_search/tasks_*.py`
7. 最后看 `benchmark/doc_search_bench/run.py` 与 `benchmark/doc_search_bench/judges/`

## 3. 目录结构

```text
benchmark/
├─ README.md
├─ run.py
├─ analyze_failures.py
├─ doc_search_bench/
├─ reports/
└─ legacy/
```

目录职责：

- `run.py`：顶层 CLI 入口
- `analyze_failures.py`：失败汇总入口
- `doc_search_bench/`：新 benchmark 实现
- `reports/runs/<run_id>/`：单次运行的完整产物目录
- `legacy/search-docs-of-crs-agent/`：旧 benchmark 只读参考

## 4. split 与 layer

### split

- `train`：mock / synthetic 数据，主要做冒烟、回归与开发期调试；train suite 按 case 类型组织，不按品牌组织
- `dev`：可见真实样本，主要做 benchmark 内核调试
- `test`：冻结真实样本，作为正式评测集

当前不再单独设置 `blind` split。旧 `04_blind` 样本已经在新结构中并入 `test`，但保留历史来源标记。

当前 train 的 suite 组织口径：

- `low_information_opening`：从资料标题派生的正样本，首轮只给 1-2 个车型/简称类可见信息，但 `user_profile.known_items` 保留中等范围的私有认知，要求系统进入澄清后命中目标资料；同一 suite 内混合多个品牌
- `vague_keyword_recall`：从资料标题派生的正样本，用户知道一些维修术语，但容易把 ECU、针脚、CAN、整车电路这类相近资料叫法混用；同一 suite 内混合多个品牌
- `normal_informative_queries`：从资料标题派生的正样本，首轮给出品牌、车系/车型、资料类型等多个信息，但不直接给完整资料名；同一 suite 内混合多个品牌
- `synthetic_noise_queries`：从正样本变异出的负样本，目标资料为空，用于验证无命中或澄清后的失败处理

品牌不再通过 train 文件名表达，而应留在 case 内容中，例如 `user_profile.known_items`。
该组织方式只拆 suite/file，不新增 `TaskCase` 字段合同。

### layer

- `atomic`：最小能力单元
- `component`：单接口或单组件能力
- `e2e`：完整单轮链路能力
- `page`：页码定位能力

## 5. 用户模拟

结构上保留与 `tau-bench` 对齐的策略名：

- `human`
- `llm`
- `react`
- `verify`
- `reflection`

当前默认运行策略是 `human`，即静态单轮输入驱动。

当切到 AI 用户模拟时：

- `--user-model` 默认对齐后端文档澄清模型链路：`openrouter_clarify_model -> agent_model`
- 用户模拟仍使用 benchmark 自己构造的独立 prompt / transcript / ask_user context，不复用主对话模型上下文
- 如需覆盖，可显式传 `--user-model`、`--user-provider` 或对应环境变量

## 6. 评分方式

正式评分由四类 judge 协作完成：

- `contract judge`
- `file judge`
- `page judge`
- `failure judge`

当前 gate 口径：

- 文件命中参与 official gate
- 页码结果只进入 shadow report

补充说明：

- `chat_completions` 轨道对真实外部 `ggzj_*` 文档结果做归一化时，`doc_title` 优先取返回中的标题字段
- 当真实结果缺少 `hierarchy_full / path / physical_path` 这类路径字段时，benchmark 会回退使用稳定文档标识（如 `file_id`）填充规范化后的 `doc_path`
- 该回退只用于 benchmark 侧合同适配，避免真实外部结果因缺少本地路径字段被误判为 `SCHEMA_INVALID`
- 该回退不改变文件命中规则；最终是否命中 gold，仍以 `top_k` 中规范化文档标题/路径与 `accepted_titles` 的匹配结果为准

## 7. 常用命令

### 7.1 运行 train

```powershell
python benchmark/run.py --split train --base-url http://127.0.0.1:8000
```

### 7.2 运行 dev

```powershell
python benchmark/run.py --split dev --base-url http://127.0.0.1:8000
```

### 7.3 运行 test

```powershell
python benchmark/run.py --split test --base-url http://127.0.0.1:8000
```

### 7.4 只跑单个 case

```powershell
python benchmark/run.py --split test --case-id case_000003 --base-url http://127.0.0.1:8000
```

### 7.5 快速 smoke

```powershell
python benchmark/run.py --split train --smoke-fast --base-url http://127.0.0.1:8006 --timeout-ms 240000
```

说明：
- `--smoke-fast` 在未显式传 `--case-id` 时，会优先仅保留 `scenario=normal` 的 case，用于快速验证链路速度与健康度。
- benchmark 会在运行前尝试拉起本机 Redis，并使用当前 `app-token` 做一次 `doc_search` 预热请求。
- 如需跳过，可使用 `--skip-redis-bootstrap` 或 `--skip-doc-search-warmup`。
- 默认 `--timeout-ms` 已提升到 `240000`，适合作为 smoke 口径。
- 如需完整回归，建议显式使用 `--timeout-ms 1200000`，避免慢 case 被过早截断。

### 7.5 查看失败汇总

```powershell
python benchmark/analyze_failures.py benchmark/reports/runs/<run_id>/report.score.json
```

## 8. 运行时日志

benchmark 新增运行时主日志后，主读入口固定为：

- `benchmark/reports/runs/<run_id>/runtime.log`

单次运行的完整产物目录固定为：

- `benchmark/reports/runs/<run_id>/`

日志固定主行格式为：

```text
时间 | 级别 | 事件 | 定位 | 结果 | 摘要
```

补充说明：

- `摘要` 由程序在运行时自动生成，不依赖人工填写
- 长文本、原始响应文件路径、问题文案与候选项摘要进入 `详情:` 或 `路径:`
- 详细合同见 `docs/modules/doc-search-benchmark/contract/benchmark运行时日志合同.md`

## 9. 运行参数

可通过 CLI 或环境变量提供：

- `--base-url` 或 `BENCHMARK_BASE_URL`
- `--app-token` 或 `BENCHMARK_APP_TOKEN`
- `--timeout-ms` 或 `BENCHMARK_TIMEOUT_MS`
- `--user-model` 或 `BENCHMARK_USER_MODEL`
- `--user-provider` 或 `BENCHMARK_USER_PROVIDER`
- `--top-k` 或 `BENCHMARK_TOP_K`

## 10. 实施约束

- official benchmark 不保留 `wiki.md`
- 不把 benchmark 规则写成被测 agent 的 prompt
- 不直接改写开发同事交付 agent 的策略
- 保持 `case_id` 稳定
- train 数据优先按 case 类型拆 suite；不要新增只按品牌拆分的 train suite
- 新增数据时优先补 `tasks_*.py` 与 `data/`，不要回退到旧 `fixtures/gold` 作为主入口

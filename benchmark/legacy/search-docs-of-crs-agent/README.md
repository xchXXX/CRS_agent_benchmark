# CRS Agent Doc Search Benchmark

## 1. 这是什么

这是 `CRS Agent` 项目里针对 `doc_search` 主链路的项目级 benchmark，slug 为 `search-docs-of-crs-agent`。

它的核心目标只有一个：验证“用户提问 -> agent 检索到正确资料文件”这条单轮链路是否可用、是否稳定、是否达到可接受召回率。

当前 benchmark 同时覆盖：

- 原子能力：关键词是否能召回正确文件名
- 组件能力：`/chat/completions` 单轮文本、图文输入是否返回合法 `documents`
- 验收能力：真实可见样本 + blind holdout 样本的聚合召回是否达到门槛

## 2. 先解释 Blind / Holdout

### 2.1 什么是 holdout case

`holdout case` 就是“保留样本”。

它和普通 acceptance case 用的是同一套评分规则，但在设计 benchmark、调 prompt、改规则、修召回问题时，不应该把它拿出来反复看答案，更不应该针对它单独做特化优化。

它的作用是：

- 防止 benchmark 被“做题化”
- 检查系统是否对未见样本仍然有效
- 让最终分数更接近真实上线效果，而不是只反映对可见样本的记忆

### 2.2 本 benchmark 里 blind 的使用方式

本 benchmark 当前有两类 acceptance 数据：

- `03_e2e/real_acceptance_visible.json`：可见 acceptance 样本，5 条
- `04_blind/real_acceptance_holdout.json`：blind holdout 样本，2 条

二者合起来一共 7 条正样本，最终门槛按聚合召回率计算：

- 总体召回率 `>= 0.85`
- 以当前样本规模计算，至少要命中 `6/7`

注意：

- blind 不单独再设一个新的 85% 子门槛
- 但 blind 结果必须单独留痕、单独复核
- 调参时不应根据 blind gold 反向修改系统

## 3. 目录结构

```text
benchmark/search-docs-of-crs-agent/
├─ README.md
├─ benchmark-plan.md
├─ fixture-matrix.md
├─ runbook.md
├─ schema/
│  └─ output.schema.json
├─ fixtures/
│  ├─ 01_atomic/
│  ├─ 02_component/
│  ├─ 03_e2e/
│  └─ 04_blind/
├─ gold/
│  ├─ 01_atomic/
│  ├─ 02_component/
│  ├─ 03_e2e/
│  └─ 04_blind/
├─ scripts/
│  ├─ generate_seed_data.py
│  ├─ checker.py
│  ├─ scorer.py
│  └─ run_eval.py
└─ reports/
   ├─ latest/
   └─ history/
```

各目录职责：

- `fixtures/`：测试输入，只描述“要喂给系统什么”
- `gold/`：标准答案，只描述“命中哪些文件名算对”
- `schema/`：规范化 actual 产物结构
- `scripts/`：生成、校验、评分、执行工具
- `reports/latest/`：最近一次运行结果
- `reports/history/`：历史快照

## 4. 当前数据切分

当前首批数据如下：

| 套件 | 文件 | 数量 | 用途 |
| --- | --- | ---: | --- |
| atomic | `mock_dongfeng_keyword_recall.json` | 49 | 东风 mock 关键词召回 |
| atomic | `mock_jiefang_keyword_recall.json` | 52 | 解放 mock 关键词召回 |
| atomic | `synthetic_noise_queries.json` | 6 | 负样本，检查误召回 |
| component | `real_text_single_turn.json` | 1 | 真实文本单轮链路 |
| component | `real_image_augmented_single_turn.json` | 3 | 真实图文单轮链路 |
| e2e | `real_acceptance_visible.json` | 5 | 可见 acceptance |
| blind | `real_acceptance_holdout.json` | 2 | holdout acceptance |

## 5. fixture、gold、actual 分别是什么

### 5.1 fixture

fixture 是输入清单，典型字段包括：

- `case_id`
- `question_text`
- `question_images`
- `input_modality`
- `benchmark_track`
- `preprocess_strategy`
- `request_context`

fixture 不应该包含“为了让系统答对而提前泄露的标准答案”。

### 5.2 gold

gold 是评分标准，当前以“正确资料名称”作为主要判定依据，典型字段包括：

- `accepted_titles`
- `preferred_title`
- `expected_response_type`
- `top_k`

规则是：

- 命中任意一个 `accepted_titles`，算召回成功
- `preferred_title` 只用于观察 top1 排序，不单独决定 pass/fail

### 5.3 actual

actual 是系统跑完后的规范化结果，由 `run_eval.py` 生成，结构受 `schema/output.schema.json` 约束。

actual 里会统一保存：

- 输入摘要
- 接口响应类型
- top-k 文档预测
- blocking failures
- 评测 hash

## 6. 当前评分口径

### 6.1 正样本

正样本要求：

- 返回 `documents`
- 在 `top_k` 内命中任一 `accepted_titles`

统计指标：

- `Recall@K`
- `Hit@1`
- `Hit@3`
- `MRR`

### 6.2 负样本

负样本要求：

- 不应该返回错误的文档结果
- 当前 gold 里 `accepted_titles` 为空
- 若负样本返回文档，记为 `NOISE_RETURNED_DOCUMENTS`

### 6.3 总门槛

最终 acceptance 门槛：

- 可见样本 + blind 样本一起算
- 正样本聚合召回率 `>= 0.85`
- 当前规模下至少命中 `6/7`
- 不能存在 blocking failures

## 7. Blocking Failures 定义

当前按严格口径处理，以下情况算 blocking failure：

- 输出不符合 `schema/output.schema.json`
- HTTP 错误、运行时错误、超时、空响应
- 正样本没有返回任何可评分候选
- 正样本返回 `message` / `error` 且未命中 gold
- 图文 case 需要图片预处理，但没有把识别结果注入上下文
- 同一 case 多次运行结果 hash 明显不稳定
- noise case 误召回文档

## 8. 图文 case 的当前限制

图文 case 的设计口径已经确定为：

- 先做图片识别
- 再把识别结果注入 `ChatRequest.context`
- 最后再走 `doc_search`

但当前分支还没有稳定、明确的图片上下文注入合同，所以 benchmark 目前采取保守策略：

- 图文 fixture 已经准备好
- `preprocess_strategy` 已标明为 `ocr_then_context_injection`
- 如果 `run_eval.py` 发现图文 case 需要预处理但 `request_context` 为空，会显式打上：

```text
IMAGE_PREPROCESS_CONTRACT_NOT_CONFIGURED
```

这不是误报，而是把“接线未完成”当作真实阻断问题记录下来。

## 9. 如何重新生成首批 fixtures / gold

如果 `sample/` 真源有更新，可以重新生成首批数据：

```powershell
python benchmark/search-docs-of-crs-agent/scripts/generate_seed_data.py
```

这个脚本会从以下真源重建 JSON：

- `sample/benchmark_excel_template.xlsx`
- `sample/资料树节点及关联文件-东风.csv`
- `sample/资料树节点及关联文件(添加52份解放相关电路图搜索关键词语).txt`

## 10. 如何运行 benchmark

### 10.1 准备环境变量

如果要调用真实服务，至少要准备：

```powershell
$env:BENCHMARK_BASE_URL = "http://<host>:<port>"
$env:BENCHMARK_APP_TOKEN = "<token>"
```

可选变量：

```powershell
$env:BENCHMARK_TIMEOUT_MS = "30000"
$env:BENCHMARK_TOP_K = "10"
```

### 10.2 运行某个 suite

例如运行可见 acceptance：

```powershell
python benchmark/search-docs-of-crs-agent/scripts/run_eval.py `
  benchmark/search-docs-of-crs-agent/fixtures/03_e2e/real_acceptance_visible.json `
  --output benchmark/search-docs-of-crs-agent/reports/latest/e2e-visible.actual.json `
  --base-url $env:BENCHMARK_BASE_URL `
  --app-token $env:BENCHMARK_APP_TOKEN
```

### 10.3 校验 actual 结构

```powershell
python benchmark/search-docs-of-crs-agent/scripts/checker.py `
  benchmark/search-docs-of-crs-agent/reports/latest/e2e-visible.actual.json
```

### 10.4 对单个 suite 评分

```powershell
python benchmark/search-docs-of-crs-agent/scripts/scorer.py `
  benchmark/search-docs-of-crs-agent/reports/latest/e2e-visible.actual.json `
  benchmark/search-docs-of-crs-agent/gold/03_e2e/real_acceptance_visible.json
```

### 10.5 对 visible + blind 做总验收评分

```powershell
python benchmark/search-docs-of-crs-agent/scripts/scorer.py `
  --pair benchmark/search-docs-of-crs-agent/reports/latest/e2e-visible.actual.json benchmark/search-docs-of-crs-agent/gold/03_e2e/real_acceptance_visible.json `
  --pair benchmark/search-docs-of-crs-agent/reports/latest/blind.actual.json benchmark/search-docs-of-crs-agent/gold/04_blind/real_acceptance_holdout.json `
  --threshold 0.85
```

## 11. 同事使用时的规则

- 优先先看 `README.md`，再看 `runbook.md`
- 不要在调试阶段把 blind gold 直接贴进 prompt、规则或注释
- 先修结构和可用性，再看召回率
- 出现 blocking failure 时，先修阻断问题，不要用 acceptance 分数掩盖
- 新增 case 时，先明确它属于 atomic、component、e2e 还是 blind
- 不要因为系统当前答不对，就去改 gold 迁就实现

## 12. AI 代理使用时的规则

后续任何 AI 代理接手这个 benchmark 时，应遵守以下约束：

- 不要把 `gold/04_blind/` 里的答案用于调 prompt 或写 hardcode
- 修改 benchmark 前，先确认是修“合同/脚本问题”还是修“样本口径问题”
- 新增 case 时，保持 `fixture` 和 `gold` 镜像结构一致
- 新增图文 case 时，先确认图片识别结果如何进入 `ChatRequest.context`
- 不要把原始服务返回结构直接当评分输入，统一先落到 `output.schema.json`
- 若需要扩容首批数据，优先保留原有 `case_id`，避免打断历史可比性

## 13. 如何新增 case

新增 case 时建议遵循这个顺序：

1. 先确认 case 属于哪一层：`atomic` / `component` / `e2e` / `blind`
2. 再写 fixture：只放输入，不泄露答案
3. 再写 gold：只放可接受标题、首选标题和响应期望
4. 再用 `checker.py` / `scorer.py` 做一次冒烟校验
5. 若是 blind case，只放入 `04_blind/`，不要同步到 visible 套件

## 14. 相关文件

- 方案说明：`benchmark-plan.md`
- 套件矩阵：`fixture-matrix.md`
- 执行手册：`runbook.md`
- 输出合同：`schema/output.schema.json`
- 数据生成：`scripts/generate_seed_data.py`
- 结构校验：`scripts/checker.py`
- 评分：`scripts/scorer.py`
- 执行：`scripts/run_eval.py`

## 15. 一句话总结

这个 benchmark 的使用原则很简单：

- `fixtures` 定义输入
- `gold` 定义答案
- `run_eval.py` 产出 actual
- `checker.py` 检查结构
- `scorer.py` 负责评分
- `blind holdout` 用来防止“只会做可见题”

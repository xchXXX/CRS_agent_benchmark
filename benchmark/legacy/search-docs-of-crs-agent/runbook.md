# CRS Agent 资料检索 Benchmark Runbook

## 1. 执行前提

- 仓库根目录固定为 `C:\projects\CRS_agent`。
- Benchmark 根目录固定为 `benchmark/search-docs-of-crs-agent/`。
- `benchmark-plan.md`、`fixture-matrix.md`、`schema/output.schema.json`、fixture/gold 已锁定。
- 环境变量已准备：
  - `BENCHMARK_BASE_URL`
  - `BENCHMARK_APP_TOKEN`（如目标环境要求）
  - `BENCHMARK_TIMEOUT_MS`（可选）
  - `BENCHMARK_TOP_K`（可选）
- 执行人清楚当前分支图文 case 仍需要图片识别结果注入合同；若未接线，runner 会把该 case 标成阻断失败而不是假装通过。

## 2. 人工确认检查点

1. 编码前确认：目标、非目标、7 条 acceptance 样本、2 条 blind、blocking failures。
2. 执行前确认：blind case 没有被搬进任何可见调试说明、截图或 prompt。
3. 图文运行前确认：图片识别结果如何进入 `ChatRequest.context` 已在当前环境明确。
4. 结果出来后确认：多答案推荐排序、blind 结论、noise 误召回是否需要人工裁决。

## 3. 执行顺序

1. 先跑 Contract Checklist。
2. 再跑 Atomic suites。
3. 再跑 Component suites。
4. 最后跑 Acceptance suites（E2E 与 Blind）。

## 4. Contract Checklist

1. `fixtures/` 与 `gold/` 路径严格镜像。
2. `01_atomic/noise/` 已存在且 gold 标题为空。
3. 真实 acceptance 只包含 7 条有 gold 的 case；`case_000001` 明确排除；`case_000005`、`case_000006`、`case_000011` 不纳入首批可评分 acceptance。
4. blind 仅包含 2 条 holdout：
   - `case_000004`
   - `case_000009`
5. `output.schema.json`、`checker.py`、`scorer.py` 与当前 fixture 格式一致。

## 5. 推荐执行方式

### 5.1 先做结构校验

```powershell
python benchmark/search-docs-of-crs-agent/scripts/checker.py `
  benchmark/search-docs-of-crs-agent/reports/latest/<actual>.json
```

### 5.2 再做单 suite 评分

```powershell
python benchmark/search-docs-of-crs-agent/scripts/scorer.py `
  benchmark/search-docs-of-crs-agent/reports/latest/<actual>.json `
  benchmark/search-docs-of-crs-agent/gold/03_e2e/real_acceptance_visible.json
```

### 5.3 如需调用服务生成实际产物

```powershell
python benchmark/search-docs-of-crs-agent/scripts/run_eval.py `
  benchmark/search-docs-of-crs-agent/fixtures/03_e2e/real_acceptance_visible.json `
  --output benchmark/search-docs-of-crs-agent/reports/latest/e2e-visible.actual.json
```

说明：

- `run_eval.py` 支持文本与 mock 套件直接跑。
- 图文套件若未提供稳定的图片上下文注入合同，会被显式标成 `IMAGE_PREPROCESS_CONTRACT_NOT_CONFIGURED`。
- 这不是忽略失败，而是把当前代码接线缺口转成可追踪的阻断证据。

## 6. 评分与复核

- `checker.py` 只负责结构与必填字段校验。
- `scorer.py` 负责标题标准化匹配、`Recall@K`、`Hit@1`、`Hit@3`、`MRR` 聚合。
- 多答案 case：
  - 命中任一 `accepted_titles` 视为召回成功
  - `preferred_title` 只影响排序观察，不单独改变通过与失败
- Blind Evaluation 的异常结果必须回看原始响应与 gold，不接受“模型大概知道”的口头解释。

## 7. 判定规则

- Contract 不全过，不进入后续步骤。
- Atomic 失败优先定位根因，不用 acceptance 分数掩盖。
- 首批 acceptance 通过条件：
  - 正样本聚合召回率 `>= 0.85`
  - 当前样本规模下至少命中 `6/7`
  - 不存在任何 blocking failures
- noise 误召回视为阻断。

## 8. 证据留存

- 每次运行至少留存：
  - suite 实际产物 JSON
  - 原始响应或原始日志
  - 评分报告 JSON
  - 输入 / 输出 hash
  - 阻断失败列表
- `reports/latest/` 保留本次结果，`reports/history/` 保留历史快照。

## 9. 迭代说明

- 不因当前实现不好测而随意修改 gold。
- 优先保留 case id，不打断历史可比性。
- 当后端锁定图文上下文注入合同后，只更新图文 fixture 的 `request_context` 与 runner 接线，不改 acceptance 题目本身。

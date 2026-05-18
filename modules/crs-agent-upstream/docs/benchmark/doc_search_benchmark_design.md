# CRS-DocSearch-Bench 设计

## 目标

CRS-DocSearch-Bench 用于评估维修资料搜索系统在真实企业微信问题上的候选资料召回与排序能力。

当前主任务定义为 `list_retrieval`：

给定修车师傅的原始问题文字和图片，系统返回 Top-K 资料候选列表；评测器判断人工标注的正确资料名称是否出现在列表中，以及排名是多少。

## 与 SWE-bench 的对应关系

| SWE-bench | CRS-DocSearch-Bench |
| --- | --- |
| GitHub issue | 企业微信原始问题 |
| 仓库 snapshot | 冻结资料库或资料索引版本 |
| patch | 系统输出的候选资料 list |
| 单元测试 | gold 资料名称命中规则 |
| harness | benchmark runner |
| resolved/unresolved | hit/miss |
| verified subset | 资料师傅复核后的 case 集 |

## 当前范围

当前已落地范围：

- `list_retrieval`：只评估原始输入下，gold 是否进入候选列表以及排名。
- `no_answer`：在最终列表阶段评估系统是否能正确给出无资料结果。
- 文件型运行日志：每次 benchmark run 记录 summary、case 级结果和事件日志。
- 默认运行入口为 `production_flow`：图片证据分析 + AgentLoop doc_search workflow。

当前暂不进入主分数：

- 澄清是否应该触发。
- 澄清问题质量。
- 用户补充后的多轮端到端成功率。
- 页码、章节、文件内部图片区域命中。

## 标准目录

```text
benchmarks/doc_search/
  datasets/
    demo_v0/
      cases.jsonl
      dataset_card.md
    verified_v1/
      cases.jsonl
      dataset_card.md
  runs/
    <run_id>/
      config.json
      status.json
      events.jsonl
      predictions.jsonl
      report.json
      failures.csv
  tools/
```

## Case Schema

```json
{
  "case_id": "case_000009",
  "input": {
    "question_text": "这个电路图有没老师",
    "image_paths": ["/path/to/image.jpg"]
  },
  "gold": {
    "answerable": true,
    "acceptable_doc_names": [
      "康明斯D2.5_CM2621_F162_原理图【国六】"
    ]
  },
  "metadata": {
    "task_type": "电路图",
    "eval_group": "list_retrieval",
    "source_excel_row": 13
  }
}
```

无资料 case：

```json
{
  "case_id": "case_000011",
  "input": {
    "question_text": "老师华菱之星的保险丝盒电路图有吗",
    "image_paths": []
  },
  "gold": {
    "answerable": false,
    "acceptable_doc_names": []
  }
}
```

## Prediction Schema

```json
{
  "case_id": "case_000009",
  "track": "raw_retrieval",
  "answerable": true,
  "results": [
    {
      "rank": 1,
      "doc_name": "康明斯D2.5_CM2621_F162_原理图【国六】",
      "doc_id": "optional",
      "score": 0.91
    }
  ],
  "runtime": {
    "latency_ms": 870,
    "search_method": "lexical_only"
  }
}
```

## 指标

主指标：

- `Recall@5`
- `Recall@10`
- `Recall@50`
- `Recall@100`
- `MRR`
- `Median Gold Rank`（主榜 Top-K 命中 case 的中位排名）
- `Median Gold Rank Full`（诊断候选池命中 case 的中位排名）
- `Miss Rate`

辅助指标：

- `No-answer Accuracy`
- `Beyond Top-K Rate`
- `by_task_type`
- `image_required`
- `multi_gold`

对于有多个可接受 gold 的 case，取命中排名最靠前的资料。

## Track

### production_flow

调用真实业务入口：

- 图片证据分析
- intent 判定
- doc_search query planning
- 多 query 搜索与合并
- ambiguity / 澄清判断
- 最终 documents 或 ask_user 响应

当前主榜默认使用该 track，但分数仍只看候选列表命中与排名，不把澄清行为本身计入主分。

### raw_retrieval

调用资料搜索底层候选列表，尽量少经过后处理。

适合定位底层召回问题。

### final_list

调用当前真实后端搜索接口，包含过滤、存在性判断和有效性判断。

适合定位后处理是否误杀正确资料。

### clarification

预留 track。未来评估是否需要澄清、澄清维度、选项是否覆盖 gold。

### interactive

预留 track。未来通过用户模拟器评估多轮澄清后的最终成功率。

## 日志与可视化

benchmark run 不复用 `chat_task_logs` 作为主存储，因为 benchmark 是批量离线评测任务，粒度是 run/case/event；现有聊天日志粒度是 session/task/run/event。

当前使用文件型日志：

- `status.json`：运行状态、进度、摘要。
- `events.jsonl`：run 级与 case 级事件。
- `predictions.jsonl`：每条 case 的系统输出。
- `report.json`：指标报告。
- `report.md`：可对外分享的人类可读测试报告。
- `failures.csv`：失败样例复盘表。

后台 Benchmark 页面读取这些文件展示数据集、运行进度、事件日志、报告与失败样例。

# CRS Agent 资料检索 Benchmark Fixture 矩阵

| case_id | layer | fixture_path | gold_path | scenario | assertion | pass marker | scoring | blocking | human review |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| search-docs-of-crs-agent-001 | atomic | `fixtures/01_atomic/mock_dongfeng_keyword_recall.json` | `gold/01_atomic/mock_dongfeng_keyword_recall.json` | 49 条东风 mock 关键词到资料名映射 | 关键词 query 至少命中目标资料标题 | `recall_hit=true` | `Recall@K`、`Hit@1`、`MRR` | 否 | 否 |
| search-docs-of-crs-agent-002 | atomic | `fixtures/01_atomic/mock_jiefang_keyword_recall.json` | `gold/01_atomic/mock_jiefang_keyword_recall.json` | 52 条解放 mock 关键词到资料名映射 | 关键词 query 至少命中目标资料标题 | `recall_hit=true` | `Recall@K`、`Hit@1`、`MRR` | 否 | 否 |
| search-docs-of-crs-agent-003 | atomic | `fixtures/01_atomic/noise/synthetic_noise_queries.json` | `gold/01_atomic/noise/synthetic_noise_queries.json` | 合成无关 query 与不存在车型/型号 | 不应返回错误高置信资料 | `negative_pass=true` | 负样本通过率 | 是 | 是 |
| search-docs-of-crs-agent-004 | component | `fixtures/02_component/real_text_single_turn.json` | `gold/02_component/real_text_single_turn.json` | 真实纯文本单轮资料检索 | `/chat/completions` 返回合法 `documents` 且命中 gold | `schema_pass=true` 且 `recall_hit=true` | 结构校验 + 标题匹配 | 是 | 否 |
| search-docs-of-crs-agent-005 | component | `fixtures/02_component/real_image_augmented_single_turn.json` | `gold/02_component/real_image_augmented_single_turn.json` | 真实图片辅助单轮资料检索 | 图像上下文已注入时命中 gold；未注入则显式阻断 | `used_image_context=true` 且 `recall_hit=true` | 结构校验 + 标题匹配 | 是 | 是 |
| search-docs-of-crs-agent-006 | e2e | `fixtures/03_e2e/real_acceptance_visible.json` | `gold/03_e2e/real_acceptance_visible.json` | 5 条可见 acceptance 样本 | 单轮主链路命中正确资料 | `recall_hit=true` | acceptance 聚合分数的一部分 | 是 | 是 |
| search-docs-of-crs-agent-007 | blind | `fixtures/04_blind/real_acceptance_holdout.json` | `gold/04_blind/real_acceptance_holdout.json` | 2 条 holdout 样本，含 1 条文本与 1 条图文 | 不泄露答案前提下命中正确资料 | `recall_hit=true` | 纳入 acceptance 聚合；单独记录 blind 明细 | 是 | 是 |

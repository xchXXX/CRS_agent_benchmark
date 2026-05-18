# CRS Agent 资料检索 Benchmark 方案

## 1. 改动目标

- `slug`: `search-docs-of-crs-agent`
- `change_type`: `benchmark-first`
- `goal`: 保护 CRS Agent 在资料检索场景下的单轮主链路质量，验证用户提问后系统能否通过统一聊天入口或检索入口返回正确资料文件；同时覆盖纯文本 query 与图片辅助 query 两类输入。
- `non_goals`: 不验证底层搜索算法实现细节、不验证真实资料库索引构建、不把多轮 `ask_user` 作为本次验收主链路、不覆盖参数查询/维修问答/故障诊断模块的 acceptance。
- `primary_risks`: 服务可用但召回错误文件；图片 case 因上下文注入不稳定被误判为失败；多答案 case 排名波动导致 top1 失真；噪音 query 被错误召回高置信资料。

## 2. 已确认信息依据

- `user_goal_and_risks`: 用户明确要求以整个项目视角先锁 `doc_search`，以“单轮资料检索正确文件”为核心，优先保证可用，再用正确率评分；通过阈值设为 acceptance 正样本聚合召回率不低于 `0.85`。
- `repo_truth`: 
  - 仓库需求与架构说明：`docs/CRS_Agent_需求文档.md`、`docs/CRS_Agent_架构文档.md`、`docs/资料查找功能说明.md`
  - 真实源码：`modules/crs-agent-upstream/backend/app/api/chat.py`、`modules/crs-agent-upstream/backend/app/api/search.py`、`modules/crs-agent-upstream/backend/app/schemas/chat.py`
  - 真实测试：`modules/crs-agent-upstream/backend/tests/test_chat_api_doc_search_flow.py`、`test_doc_search_runtime_service.py`、`test_doc_search_domain.py`
  - 样例数据：`sample/benchmark_excel_template.xlsx`、`sample/资料树节点及关联文件-东风.csv`、`sample/资料树节点及关联文件(添加52份解放相关电路图搜索关键词语).txt`
- `current_failures`: 当前没有来自业务运行的失败证据；但在已检查分支中，图文检索“图片识别结果注入 `ChatRequest.context`”缺少稳定的后端合同键名，因此自动化图文 acceptance 需要显式接线。
- `review_owner`: `Codex（首版 benchmark 口径 owner）`
- `schema_locked`: 已锁定为“单 case 规范化评测输出合同”，不是原始 `ChatResponse` 直拷贝；用于统一保存一次评测后的输入、响应、预测、校验和评分字段。

## 3. 合同与分层矩阵

| layer | question | artifact or signal | success criterion | scoring method | gate type |
| --- | --- | --- | --- | --- | --- |
| contract | 评测输出合同、suite 切分、gold 口径是否已冻结 | `benchmark-plan.md`、`fixture-matrix.md`、`schema/output.schema.json`、fixture/gold 镜像 | 文档、schema、suite 路径与 case 切分一致 | checklist 全过 | diagnostic |
| atomic | 给定关键词或短查询时，是否能把目标文件名作为可命中的正确结果；面对噪音词时是否抑制错误召回 | mock 关键词套件、noise 套件 | 正样本能命中 gold 标题；负样本不返回错误高置信标题 | `Recall@K`、噪音抑制通过率 | diagnostic |
| component | 单轮资料检索在文本与图文输入下，是否满足聊天入口的结构化响应合同 | 真实文本/图文 component 套件 | 返回类型合法，正样本能回到 `documents`，阻断失败为空 | 结构校验 + 标题匹配 | diagnostic |
| e2e | 可见 acceptance 样本是否达到可交付召回 | `03_e2e` 可见样本 | `documents` 结果中命中任一 gold 标题 | `Recall@K`、`Hit@1`、`Hit@3`、`MRR` | acceptance |
| blind | holdout 样本是否在未知答案前提下仍维持可接受召回 | `04_blind` 两条保留样本 | 保留样本按同一 gold 口径命中正确资料 | 同 e2e；结果纳入 acceptance 聚合 | acceptance |

## 4. Fixture / Gold 策略

- `fixtures_root`: `benchmark/search-docs-of-crs-agent/fixtures/`
- `gold_root`: `benchmark/search-docs-of-crs-agent/gold/`
- `reusable_fixtures`: 
  - `sample/benchmark_excel_template.xlsx` 的 7 条可评分真实 case
  - `sample/资料树节点及关联文件-东风.csv` 中 49 条有效 `关联文件名称 + 关键词`
  - `sample/资料树节点及关联文件(添加52份解放相关电路图搜索关键词语).txt` 中 52 条有效 `关联文件名称 + 搜索可能关键词`
  - `sample/` 目录中的图片文件
- `new_fixtures`: 
  - mock 原子套件清洗为标准 JSON
  - 真实文本/图文 component 套件
  - 5 条可见 acceptance 与 2 条 blind holdout
  - 合成 noise 套件
- `noise_rule`: `fixtures/01_atomic/noise/` 必须存在，且镜像 `gold/01_atomic/noise/` 的 `accepted_titles` 为空。
- `determinism_rules`: 
  - 所有 fixture 只引用仓库内 `sample/` 资源
  - gold 以文件名字符串为主，不依赖外部 doc_id
  - 标题匹配先做标准化，再比较命中
  - 同一输入的规范化输出必须产出稳定 `deterministic_hash`
- `contamination_controls`: blind 仅保留 2 条真实 holdout，不在可见 acceptance 与 mock 诊断套件中重复出现；blind gold 独立存放在 `04_blind/`，运行时禁止把 blind 标题泄露到 prompt 或人工调参说明。

## 5. 通过闸门与阻断条件

- `contract_gate`: `benchmark-plan.md`、`fixture-matrix.md`、`output.schema.json`、fixture/gold 镜像全部就绪且 checker 通过，否则不进入 Atomic / Component / Acceptance。
- `minimum_pass_threshold`: acceptance 正样本聚合召回率 `>= 0.85`；以当前首批样本计，`03_e2e` 5 条与 `04_blind` 2 条共 7 条正样本，至少命中 6 条。blind 单独保留观察，不再额外设独立 85% 子门槛。
- `blocking_failures`: 
  - 输出不符合 `output.schema.json`
  - HTTP/运行时错误、超时、空响应
  - 正样本最终未返回任何可评分候选
  - 正样本返回 `message/error` 且未命中 gold
  - 图文 case 需要图片预处理但运行产物显示未注入图像上下文
  - 同一 case 重跑时 `deterministic_hash` 不稳定
  - noise case 返回错误高置信资料
- `human_signoff_required`: 
  - 多答案 case 的 top1 排名与 `preferred_title` 不一致但仍命中 gold
  - 任一 blind case
  - 任一图文 case
  - 接近阈值的最终放行结论
- `evidence_to_save`: 
  - 每个 case 的原始响应
  - 规范化实际产物 JSON
  - 输入 / 输出 hash
  - suite 评分报告
  - 阻断失败明细

## 6. 自动化计划

- `checker_script`: `benchmark/search-docs-of-crs-agent/scripts/checker.py`
- `scorer_script`: `benchmark/search-docs-of-crs-agent/scripts/scorer.py`
- `scripted`: 
  - fixture/gold 的 JSON 读取
  - 规范化输出合同校验
  - 文件名标准化匹配
  - `Recall@K`、`Hit@1`、`Hit@3`、`MRR` 统计
  - suite 级报告输出
- `manual`: 
  - 当前分支图文 case 的图片识别结果注入接线
  - 多答案 case 的排序偏差审阅
  - blind 结果签字
- `manual_review_points`: 
  - `case_000002`、`case_000010` 这类多答案且带 `【推荐】` 的 case
  - 图文 case 是否真正使用了图片上下文
  - noise 误召回的边界样本
- `orchestration_notes`: 
  - 原子 mock 套件优先用于快速定位召回退化
  - 真实单轮 case 走 `chat/completions`
  - mock 关键词套件可诊断性地走 `/search` 或等价检索入口
  - 当前分支缺少稳定的图文注入合同时，图文 case 会在自动 runner 中被显式标成 `IMAGE_PREPROCESS_CONTRACT_NOT_CONFIGURED`

## 7. 目录约束

- `benchmark_root`: `benchmark/search-docs-of-crs-agent/`
- `required_layout`: `schema/`、`fixtures/`、`gold/`、`scripts/`、`reports/` 必须齐全。
- `placement_rule`: 本 Benchmark 包只能放在仓库根 `benchmark/` 目录下。

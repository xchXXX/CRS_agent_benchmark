# Doc Search Benchmark 重构工程实施蓝图

> 文档口径提示：
> 本文是历史重构蓝图，保留其原始阶段编号与重构语境。
> 当前 `doc_search` 真实项目模糊用户模拟施工，不以本文作为阶段排期真源；
> 统一以上位文档
> [doc_search真实项目模糊用户模拟施工方案](./doc_search真实项目模糊用户模拟施工方案.md)
> 与
> [doc_search模糊用户模拟阶段总览与文档口径说明](./doc_search模糊用户模拟阶段总览与文档口径说明.md)
> 为准。

## 1. 文档目的

本文档用于冻结 `CRS_agent` 仓库内 `doc_search benchmark` 的工程重构实施方案。

目标不是立即改代码，而是先把以下内容定义清楚：

- benchmark 的边界与非边界
- 最终目录结构
- 数据与代码的组织方式
- 迁移步骤与执行顺序
- 每一步的输入、输出、验收标准
- 人与 AI 后续协作时的统一阅读入口

## 2. 背景与约束

### 2.1 已确认背景

- 本项目当前仍然只负责构建 benchmark，不负责实现业务功能。
- 当前 benchmark 主要覆盖 `doc_search` 的单轮资料检索能力。
- 未来能力已经明确扩展为：
  - 先找到正确文件名
  - 再根据用户输入定位该文件对应页码
- 页码能力当前尚未实现，但希望在 benchmark 重构时一并纳入模型设计。

### 2.2 仓库级强约束

- benchmark 运行内容继续保留在仓库根目录的 `benchmark/` 下。
- benchmark 相关文档统一放在 `docs/` 下，且按模块隔离。
- 本模块文档路径固定为：
  - `docs/modules/doc-search-benchmark/`
- 当前阶段先编写工程实施蓝图，不直接实施代码。

### 2.3 与 tau-bench 的关系

本次重构明确参考 `tau-bench`，但只参考其适用于本项目的部分。

明确参考的部分：

- 顶层目录组织习惯
- `run.py + 包目录 + envs + tasks_* + data` 的工程骨架
- `train / dev / test` 的 split 语义
- `user.py` 中多种用户模拟策略的分层设计

明确不直接照搬的部分：

- `wiki.md` 作为被测 agent 系统提示词的用法
- 依赖工具调用和状态变更的 `reward` 计算方式
- 强绑定多轮 agent policy 的场景前提

原因：

- 本项目的 official benchmark 测的是开发同事交付的真实 agent。
- benchmark 不拥有该 agent 的 prompt 控制权。
- 因此不能像 `tau-bench` 一样，由 benchmark 自己写 `wiki.md` 并直接喂给被测 agent。

## 3. 重构目标

本次重构的最终目标分为 6 类。

### 3.1 结构目标

- benchmark 目录结构显著向 `tau-bench` 靠拢。
- 但只保留适用于 official benchmark 的模块。

### 3.2 数据目标

- 当前分散在 `fixtures/`、`gold/`、`reports/` 的资产重新组织。
- 统一采用 `train / dev / test` 作为数据切分语义。

### 3.3 评测目标

- 把“文件命中”与“页码定位”拆成两个层次评测。
- 文件级先成为正式 gate，页码级先进入 shadow mode。

### 3.4 工程目标

- 现有单体脚本职责拆分。
- 形成可维护的 `types / envs / judges / user / data` 工程骨架。

### 3.5 协作目标

- `benchmark/README.md` 成为人类与 AI 的统一入口。
- 后续任何参与者先读 `benchmark/README.md`，再读更细分文档。

### 3.6 迁移目标

- 保留既有样本资产，不推倒重做数据。
- 保留既有 `case_id`，避免破坏历史可比性。

## 4. 非目标

以下内容不在本次 official benchmark 重构范围内：

- 不负责实现 `doc_search` 业务代码
- 不负责实现页码定位业务能力
- 不负责定义或改写开发同事实际交付 agent 的 prompt
- 不引入 `wiki.md` 作为 official benchmark 的 agent 提示词资产
- 不照搬 `tau-bench` 的状态型 reward 机制
- 不把 benchmark 扩展到 `parameter_query`、`repair_knowledge`、`fault_diagnosis`

## 5. official benchmark 的边界

### 5.1 被测对象

official benchmark 的被测对象是：

- 开发同事交付的真实 `doc_search` 相关能力
- 包括实际接口、实际路由、实际上下文处理、实际返回结果

### 5.2 benchmark 拥有的内容

official benchmark 只拥有以下资产：

- 样本输入
- 样本标签
- 图像预处理契约
- 调用方式
- 输出归一化逻辑
- 评分规则
- 报告与失败归因规则

### 5.3 benchmark 不拥有的内容

official benchmark 不拥有：

- 被测 agent 的系统提示词
- 被测 agent 的业务策略文本
- 被测 agent 的领域规则 prompt

因此，official benchmark 中不保留 `wiki.md`。

## 6. 目标目录结构

本次重构后的 benchmark 目标目录如下。

```text
benchmark/
├─ README.md
├─ run.py
├─ analyze_failures.py
├─ doc_search_bench/
│  ├─ __init__.py
│  ├─ types.py
│  ├─ run.py
│  ├─ user.py
│  ├─ envs/
│  │  ├─ __init__.py
│  │  ├─ base.py
│  │  └─ doc_search/
│  │     ├─ __init__.py
│  │     ├─ env.py
│  │     ├─ rules.py
│  │     ├─ tasks_train.py
│  │     ├─ tasks_dev.py
│  │     ├─ tasks_test.py
│  │     ├─ data/
│  │     │  ├─ source/
│  │     │  ├─ train/
│  │     │  ├─ dev/
│  │     │  └─ test/
│  │     ├─ adapters.py
│  │     ├─ preprocessors.py
│  │     └─ matchers.py
│  ├─ judges/
│  │  ├─ contract.py
│  │  ├─ file.py
│  │  ├─ page.py
│  │  └─ failure.py
│  └─ utils/
│     ├─ text_norm.py
│     └─ hashing.py
├─ reports/
│  ├─ latest/
│  └─ history/
└─ legacy/
   └─ search-docs-of-crs-agent/
```

## 7. 目录职责说明

### 7.1 `benchmark/README.md`

职责：

- benchmark 的统一阅读入口
- 指导人和 AI 按顺序理解 benchmark
- 写清楚运行方式、评分方式、目录说明

它不是样本说明书，也不是 agent policy 文档。

### 7.2 `benchmark/run.py`

职责：

- 顶层 CLI 入口
- 负责解析参数并分发到 `doc_search_bench.run`

### 7.3 `benchmark/analyze_failures.py`

职责：

- 对运行结果进行失败汇总与分类
- 生成人工复查友好的失败报告

### 7.4 `benchmark/doc_search_bench/types.py`

职责：

- 定义 benchmark 核心数据结构
- 统一任务对象、运行结果对象、评分结果对象、失败码对象

### 7.5 `benchmark/doc_search_bench/user.py`

职责：

- 严格参考 `tau-bench` 的用户模拟器分层方式
- 统一定义用户策略枚举与加载逻辑

应保留与 `tau-bench` 对齐的策略形态：

- `human`
- `llm`
- `react`
- `verify`
- `reflection`

备注：

- 在 official benchmark 初期，主流程默认以 `human` 或静态单轮驱动为主。
- 但结构上保留这几种策略，便于未来扩展到澄清式与页码追问式 benchmark。

### 7.6 `benchmark/doc_search_bench/envs/base.py`

职责：

- 提供 benchmark 环境基类
- 负责：
  - 任务装载
  - 用户输入驱动
  - 适配器执行
  - 结果收集
  - judge 调用入口

注意：

- 此处不实现 `tau-bench` 那种状态回放 reward
- 这里只负责 benchmark pipeline 编排

### 7.7 `benchmark/doc_search_bench/envs/doc_search/tasks_*.py`

职责：

- 按 split 组织任务
- 是 benchmark 的数据入口文件

固定拆分：

- `tasks_train.py`
- `tasks_dev.py`
- `tasks_test.py`

### 7.8 `benchmark/doc_search_bench/envs/doc_search/data/`

职责：

- 存放任务引用的数据资产

约定：

- `source/`：原始来源数据，如 Excel、CSV、TXT、图片引用清单
- `train/`：synthetic / mock 数据
- `dev/`：可见真实开发调试集
- `test/`：冻结真实测试集
- 当前蓝图不单独设置 `blind/`

### 7.9 `benchmark/doc_search_bench/envs/doc_search/adapters.py`

职责：

- 负责适配真实服务接口
- 对接：
  - `/search`
  - `/chat/completions`
  - 未来页码返回接口或扩展字段

### 7.10 `benchmark/doc_search_bench/envs/doc_search/preprocessors.py`

职责：

- 统一管理图像预处理逻辑
- 包括 OCR、上下文注入、缺失契约判定

### 7.11 `benchmark/doc_search_bench/envs/doc_search/matchers.py`

职责：

- 文档标题标准化匹配
- 页码命中匹配
- 页码范围 overlap 计算

### 7.12 `benchmark/doc_search_bench/judges/`

职责：

- 拆分评分逻辑，不再把所有逻辑塞进一个 `scorer.py`

拆分如下：

- `contract.py`
  - 响应结构合法性
  - blocking failure
- `file.py`
  - 文件命中相关指标
- `page.py`
  - 页码命中相关指标
- `failure.py`
  - 失败分类与失败码输出

### 7.13 `benchmark/legacy/`

职责：

- 保留当前 `search-docs-of-crs-agent` 老结构
- 迁移阶段只读，不再新增内容

## 8. split 与 layer 语义

本次重构明确把数据切分语义与评测层级语义分开。

### 8.1 split

- `train`
  - synthetic / mock
  - 用于回归、冒烟、开发阶段调试
- `dev`
  - 可见真实样本
  - 用于 benchmark 内核调试
- `test`
  - 冻结真实样本
  - 作为主正式评测集

补充约定：

- 当前阶段不单独设置 `blind` split
- 如果未来需要隐藏验收集，优先作为 `test` 的可见性策略或仓库外未公开样本管理，而不是新增一级 split

### 8.2 layer

- `atomic`
  - 最小能力单元
  - 典型场景：query 到文件名的单点召回
- `component`
  - 单组件或单接口验证
  - 典型场景：`/chat/completions` 是否返回合法 `documents`
- `e2e`
  - 完整链路验证
  - 典型场景：真实用户问题完整走完整路由与输出
- `page`
  - 页码定位能力
  - 典型场景：在命中文件后定位正确页码

## 9. 核心数据模型

### 9.1 任务对象

任务对象建议至少包含以下字段：

- `case_id`
- `split`
- `layer`
- `input_modality`
- `question_text`
- `question_images`
- `preprocess_strategy`
- `benchmark_track`
- `request_context`
- `accepted_titles`
- `preferred_title`
- `accepted_pages`
- `accepted_page_ranges`
- `expected_response_type`
- `notes`

### 9.2 设计原则

- 文件命中标签与页码标签同处一个任务对象中
- 页码字段允许为空
- 页码字段为空不代表失败，只代表该 case 当前不参与 page gate

### 9.3 为什么不再保留现有 `fixtures/gold` 作为主入口

因为本次要显著参考 `tau-bench` 的工程结构。

因此：

- `fixtures/gold` 不再作为主组织方式
- 它们迁移后的语义将被 `tasks_*.py` 接管
- 旧 `fixtures/gold` 保留在 `legacy/` 中做兼容参考

## 10. 评分设计

### 10.1 不采用 tau-bench 的 reward 方式

原因：

- 本项目不依赖复杂工具调用
- 本项目不依赖数据库状态变更
- 本项目不需要通过重放标准动作比较最终状态哈希

因此，不采用 `tau-bench` 的：

- `reward = 0/1`
- `ground truth actions replay`
- `data hash comparison`

### 10.2 official benchmark 的评分方式

official benchmark 改为“多 judge + 多指标 + 总 gate”。

### 10.3 contract judge

检查内容：

- 响应结构是否合法
- 响应类型是否正确
- 是否存在运行时错误
- 图文 case 是否完成必要预处理

### 10.4 file judge

指标：

- `Recall@K`
- `Hit@1`
- `Hit@3`
- `MRR`
- `negative_pass_rate`

### 10.5 page judge

指标：

- `PageHit@1`
- `PageHit@K`
- `ExactPageHit`
- `PageRangeOverlapHit`
- `MinPageDistance`

### 10.6 failure judge

输出稳定失败码，不只给 pass/fail。

建议失败码：

- `SCHEMA_INVALID`
- `HTTP_OR_RUNTIME_ERROR`
- `EXPECTED_DOCUMENTS_RESPONSE`
- `NO_PREDICTED_DOCUMENTS`
- `OCR_CONTEXT_MISSING`
- `FILE_RECALL_MISS`
- `RANKING_MISS`
- `PAGE_MISS`
- `PAGE_RANGE_MISS`
- `NOISE_FALSE_POSITIVE`

## 11. 页码能力接入策略

### 11.1 原则

页码能力必须现在进模型，但不能在功能未实现时阻断整个 benchmark。

### 11.2 分阶段启用

#### 阶段 P1

- 在任务模型中加入：
  - `accepted_pages`
  - `accepted_page_ranges`
- 但 official gate 只看文件命中

#### 阶段 P2

- 运行时开始记录：
  - `predicted_pages`
  - `page_confidence`
- 仅做 shadow report

#### 阶段 P3

- 当业务能力可用后
- 在 `dev/page` 与 `test/page` 中启用页码评分

#### 阶段 P4

- 页码稳定后
- 把带页码标注的任务纳入正式 gate

## 12. 用户模拟策略蓝图

### 12.1 保留策略接口

参考 `tau-bench`，保留以下用户策略：

- `human`
- `llm`
- `react`
- `verify`
- `reflection`

### 12.2 初期启用策略

official benchmark 第一阶段建议主用：

- `human`
- 静态单轮输入模式

### 12.3 扩展时机

当 benchmark 开始覆盖以下场景时，再逐步启用更复杂的用户模拟：

- 文件歧义澄清
- 图像信息补充
- 页码二次确认
- “先文件后页码”的轻量多轮交互

## 13. 从当前结构到目标结构的迁移映射

### 13.1 保留资产

以下资产保留：

- `benchmark/search-docs-of-crs-agent/fixtures/*`
- `benchmark/search-docs-of-crs-agent/gold/*`
- `benchmark/search-docs-of-crs-agent/reports/*`
- `sample/*`

### 13.2 迁移方式

- 当前 `fixtures/01_atomic/*`
  - 迁移为 `tasks_train.py` 引用的 train atomic 数据
- 当前 `fixtures/02_component/*`
  - 迁移为 `tasks_dev.py` 或 `tasks_test.py` 中的 component 数据
- 当前 `fixtures/03_e2e/*`
  - 迁移为 `tasks_test.py` 中的 e2e 数据
- 当前 `fixtures/04_blind/*`
  - 并入 `tasks_test.py`
  - 保留来源标记，作为 `test` 内的历史子集

### 13.3 报告迁移

- 旧 `reports/latest/*.actual.json`
  - 进入 `legacy/` 参考
- 新版报告输出统一写入：
  - `benchmark/reports/runs/<run_id>/`

## 14. 实施分阶段计划

### 阶段 0：冻结方案

内容：

- 冻结本文档
- 冻结最终目录结构
- 冻结 official benchmark 边界

输入：

- 当前 benchmark 目录
- 当前讨论结论

输出：

- 工程实施蓝图

验收：

- 目录、边界、评分原则不再摇摆

### 阶段 1：搭空目录与主入口

内容：

- 新建 `benchmark/README.md`
- 新建 `benchmark/run.py`
- 新建 `benchmark/doc_search_bench/` 骨架
- 新建 `legacy/`

输入：

- 本蓝图

输出：

- 新 benchmark 工程骨架

验收：

- 所有目录到位
- 无业务逻辑迁移

### 阶段 2：定义类型与任务模型

内容：

- 编写 `types.py`
- 固化任务对象字段
- 固化运行结果字段

输入：

- 当前 `fixtures/gold` 字段

输出：

- 新 benchmark 核心数据模型

验收：

- 能完整表达文件命中与页码命中

### 阶段 3：迁移数据资产

内容：

- 从旧 `fixtures/gold` 迁移到 `tasks_train/dev/test.py`
- 保留原始来源引用

输入：

- 旧 `fixtures/`
- 旧 `gold/`
- `sample/`

输出：

- 新任务入口文件

验收：

- `case_id` 保持稳定
- split/layer 明确

### 阶段 4：重写执行内核

内容：

- 实现 `envs/base.py`
- 实现 `envs/doc_search/env.py`
- 实现 `adapters.py`
- 实现 `preprocessors.py`

输入：

- 任务模型

输出：

- 能运行单 task、单 split 的 benchmark 内核

验收：

- 对文本与图文 case 都能跑通

### 阶段 5：拆 judge

内容：

- 实现 `contract.py`
- 实现 `file.py`
- 实现 `page.py`
- 实现 `failure.py`

输出：

- 分层评分器

验收：

- 文件评分与页码评分独立可运行

### 阶段 6：接入用户模拟

内容：

- 实现 `user.py`
- 接入 `human / llm / react / verify / reflection`

验收：

- CLI 可切换用户策略
- 单轮与未来多轮场景可兼容

### 阶段 7：发布与回归

内容：

- 跑 `train`
- 跑 `dev`
- 跑 `test`

验收：

- `test` 的 official file-level score 可重复
- page-level 报告可观测

## 15. 实施顺序要求

顺序必须严格如下：

1. 冻结方案
2. 新建目录
3. 定义类型
4. 迁移数据
5. 重写执行内核
6. 拆 judge
7. 接入用户模拟
8. 最后再做页码 gate 升级

禁止顺序：

- 先改评分，再改数据模型
- 先加页码 gate，再补页码字段
- 先删旧结构，再完成新结构迁移

## 16. 风险与控制

### 16.1 主要风险

- 迁移时破坏现有 `case_id`
- split 与 layer 语义混用
- 图文 case 因预处理契约不清而反复失真
- 页码能力未实现就被强制 gate
- 把 benchmark 规则误写成 agent prompt

### 16.2 控制措施

- 旧结构先进 `legacy/`
- `case_id` 迁移映射必须可追溯
- `README.md` 与实现同步更新
- page 先 shadow，不直接 gate
- official benchmark 中不引入 `wiki.md`

## 17. benchmark/README.md 未来应包含的内容

后续实施时，`benchmark/README.md` 应至少包含：

- benchmark 目标与非目标
- 目录结构说明
- split 说明
- layer 说明
- 阅读顺序
- 常用运行命令
- 评分方式
- 迁移说明

## 18. 最终冻结结论

本项目的 benchmark 重构路线定义如下：

- 目录结构明显参考 `tau-bench`
- official benchmark 不保留 `wiki.md`
- 保留并参考 `tau-bench` 的 `user.py` 多策略设计
- 采用 `train / dev / test` 作为 split
- 采用 `atomic / component / e2e / page` 作为 layer
- 文件命中先成为正式 gate
- 页码定位先进入 shadow，再逐步升级为正式 gate
- 当前 `search-docs-of-crs-agent` 旧结构迁入 `legacy/`

该路线适合作为后续 benchmark 重构的唯一工程实施蓝图。

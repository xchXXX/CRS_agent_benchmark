# CRS Agent 架构文档

## 1. 文档目标

本文档基于当前仓库实现，说明 CRS Agent 的系统架构、模块边界、运行链路、数据存储、接口组织、前后端实现细节与开发期扩展能力。

本文档强调“当前代码真实结构”，不以理想态替代现状。

## 2. 总体架构

当前项目是一个多端、多能力、混合迁移态系统，整体由五个层次构成：

1. 访问层：用户前端、管理后台、兼容接口调用方
2. 接口层：FastAPI 路由与请求级依赖构建
3. 运行时层：Agent Loop、专门工作流、上下文管理、工具适配
4. 领域服务层：资料搜索、参数查询、维修知识、图片证据、故障诊断、Benchmark
5. 存储与外部依赖层：Redis、MySQL、Excel、本地文件、外部检索/诊断/模型服务

开发期另有一条平行链路：

- LangGraph Studio -> `coding_engine` -> Harness / Patch / Persistence

## 3. 代码结构映射

### 3.1 顶层目录

- `backend/`: 业务后端
- `frontend/user/`: 用户前端
- `frontend/admin/`: 管理后台
- `frontend/miniapp/`: 小程序预留目录
- `benchmarks/`: 评测数据与运行结果
- `docs/`: 设计、评测、SQL 与资料文件
- `scripts/`: 本地启动脚本

### 3.2 后端目录

- `backend/app/main.py`: FastAPI 应用入口
- `backend/app/api/`: HTTP 路由层
- `backend/app/agent/runtime/`: Agent Loop 核心运行时
- `backend/app/agent/domain/`: 各业务域服务
- `backend/app/agent/context/`: 会话上下文与 loop guard
- `backend/app/agent/memory/`: 消息历史、延迟状态、缓存存储
- `backend/app/agent/adapters/`: 前端协议、旧系统能力适配
- `backend/app/agent/observability/`: trace 与后台日志沉淀
- `backend/app/benchmark/`: 评测运行器
- `backend/app/coding_engine/`: 开发期自动编程引擎
- `backend/app/legacy/`: 旧项目模型、服务和工具

## 4. 访问层架构

### 4.1 用户前端

目录：`frontend/user`

技术栈：

- React 18
- TypeScript
- Vite 5
- Axios
- Zustand
- React Router
- React Markdown / React PDF
- Lucide React
- Tailwind CSS

用户前端实际承载的能力不是单纯搜索页，而是统一对话工作台，`src/App.tsx` 内聚了消息列表、文档查看、澄清向导、ask-user v2、参数卡片、图片上传、反馈卡片、诊断卡片等交互。

主要模块：

- `components/`: 文档查看、反馈、参数卡片、图片上传、ECU 选择等组件
- `modules/ask-user-v2/`: 结构化 ask-user 表单渲染与校验
- `services/api.ts`: 聊天、图片、流式、反馈、来源详情等 API 封装
- `services/aliyunSpeech.ts`: 阿里云语音能力接入
- `services/sse.ts`: 流式任务管理

### 4.2 管理后台

目录：`frontend/admin`

技术栈：

- React 18
- TypeScript
- Vite 5
- Ant Design 5
- Axios
- Zustand

页面结构：

- `/login`: 登录页
- `/`: 仪表盘
- `/dimensions`: 维度管理
- `/logs`: 系统日志
- `/benchmarks`: Benchmark
- `/feedback`: 用户反馈
- `/config`: 系统配置

### 4.3 兼容调用方

后端保留了旧协议兼容层，允许旧前端或迁移中的调用方继续访问：

- `/chat/api/search`
- `/chat/api/legacy/*`
- `/chat/api/image/recognize-fault-codes`

## 5. FastAPI 接口层

### 5.1 应用入口

文件：`backend/app/main.py`

职责：

- 加载 `.env` 与 `.env.runtime`
- 在 lifespan 中初始化默认依赖
- 尝试完成 legacy bootstrap、系统配置对齐、参数索引 warmup
- 注册所有业务与管理路由

### 5.2 路由组织

当前路由按功能拆分：

- `chat.py`: 统一聊天与流式聊天
- `search.py`: 资料搜索兼容接口
- `image.py`: 图片证据、诊断图片兼容、批量诊断
- `speech.py`: 阿里云语音 token
- `feedback.py`: 用户反馈提交
- `ggzj.py`: 外部资料 URL 解析
- `legacy_proxy.py`: token 兼容辅助
- `admin_auth.py`: 管理员登录与密码修改
- `admin_dashboard.py`: 仪表盘摘要
- `admin_dimension.py`: 维度管理
- `admin_logs.py`: 日志查询与导出
- `admin_feedback.py`: 反馈查询
- `admin_config.py`: 系统配置管理
- `admin_benchmark.py`: Benchmark 运行管理

### 5.3 请求级依赖构建

文件：`backend/app/api/request_context.py`

流程：

1. 从应用状态拿到默认 `AgentRuntimeDeps`
2. 从 header/query 中提取 `app-token`
3. 借助 `token_identity_service` 解析用户身份
4. 生成 request 级依赖副本
5. 为当前请求分配独立 tracer，并默认开启外部资料搜索

## 6. 运行时层架构

### 6.1 运行时依赖容器

文件：`backend/app/agent/runtime/deps.py`

`AgentRuntimeDeps` 是运行时的核心注入容器，持有：

- `tool_registry`
- `message_history_store`
- `deferred_state_store`
- `case_context_store`
- `doc_search_cache_store`
- `tracer`
- 各领域服务
- 数据库 session factory
- 用户身份与 token
- 当前请求的 case context / loop guard / tool history / llm observability

默认依赖构建时会尝试装配：

- Redis 存储
- 维修知识服务
- 旧项目 DB / 配置 / 搜索 / 诊断 / ggzj 适配
- 参数查询服务

### 6.2 Agent Factory

文件：`backend/app/agent/runtime/factory.py`

职责：

- 检查 `pydantic_ai` 是否可用
- 构建主 Agent
- 构建 repair gate agent
- 构建 repair render planner agent
- 构建 repair renderer agent
- 注册受控工具集合
- 对工具调用应用 loop guard 与记录机制

当前 runtime 中不是只有一个 agent，而是多 agent 分阶段工作：

- `crs_agent_loop`
- `crs_repair_pre_answer_gate`
- `crs_repair_render_planner`
- `crs_repair_answer_renderer`

### 6.3 Agent Loop 主服务

文件：`backend/app/agent/runtime/service.py`

`AgentLoopService` 是系统核心编排器，负责：

- 构建 request 级执行态
- 记录图片证据
- 解析与缓存意图路由结果
- 优先进入专门工作流
- 调用主 Agent / repair gate / renderer
- 统一处理 ask-user、恢复、结果整形、流式事件
- 写入消息历史、deferred state、case context 与 trace

### 6.4 主运行流程

同步 `process()` 的高层逻辑如下：

1. 准备 request 级 deps
2. 记录 request trace
3. 抽取并落入图片证据
4. 执行意图路由
5. 判断是否进入 `doc_search` 专门工作流
6. 判断是否进入 `parameter_query` 专门工作流
7. 若未命中，则进入主 Agent Loop
8. 如需维修问答审查，先走 repair gate
9. 运行主 Agent 或最终 renderer
10. 处理 DeferredToolRequests
11. 构造 `ask_user`、`message`、`documents`、`param_request` 或 `error`

流式 `stream()` 与同步流程一致，只是中间通过 `AgentRuntimeEvent` 输出：

- `start`
- `hint`
- `text_delta`
- `tool_status`
- `ask_user`
- `done`
- `error`

### 6.5 专门工作流优先级

当前系统不是所有请求都先给主 Agent 自由发挥。

优先级如下：

1. 文档搜索工作流
2. 参数查询工作流
3. 主 Agent Loop

这是当前架构的关键特征：资料搜索和参数查询具有更强的确定性工作流闭环。

## 7. 上下文与状态管理

### 7.1 Message History

文件：`backend/app/agent/memory/message_history_store.py`

作用：

- 存储 Pydantic AI message history JSON
- Redis 优先
- 本地 `.data/message_history/` 回退

### 7.2 Deferred State

文件：`backend/app/agent/memory/deferred_store.py`

作用：

- 保存 ask-user 恢复所需状态
- 核心字段：`tool_call_id`、`tool_name`、`message_history_json`、`payload`
- Redis 优先，本地 `.data/deferred/` 回退

### 7.3 Case Context

文件：

- `backend/app/agent/context/manager.py`
- `backend/app/agent/context/store.py`

作用：

- 聚合跨轮次可复用证据
- 保存待执行动作、已选文档、用户补充、图片证据、参数查询结果等
- 对上下文做压缩和限额控制
- Redis 优先，本地 `.data/case_context/` 回退

### 7.4 Loop Guard

`CaseContextManager` 与运行时会配合 loop guard 控制：

- 工具调用总次数
- 外部工具调用次数
- ask-user 次数
- 重复工具 / 重复参数调用次数
- 无信息增益连续轮次

目的是防止模型在单轮中陷入无穷循环或重复追问。

## 8. 领域服务架构

### 8.1 文档搜索域

目录：`backend/app/agent/domain/doc_search`

核心组件：

- `service.py`: 领域入口
- `pipeline.py`: 搜索结果后处理
- `query_planner.py`: 基于 Pydantic AI 的查询规划
- `matching.py`: 结果匹配与澄清辅助
- `builders/`: summary 与 clarify 结果构建
- `llm_smart.py`: LLM 辅助歧义分析

实现特点：

- 既支持本地 DB 搜索，也支持外部 ggzj 搜索
- 支持按 snapshot 恢复搜索
- 支持 rule + LLM 双阶段歧义分析
- 通过 `LegacyDocSearchAdapter` 连接到运行时

### 8.2 参数查询域

目录：`backend/app/agent/domain/parameter_query`

核心组件：

- `service.py`: 参数查询领域入口
- `sync_service.py`: 外部数据同步与本地重建
- `index_store.py`: 本地索引缓存
- `external_repository.py`: 外部参数库读取
- `llm_normalizer.py`: 用 LLM 解析自然语言查询
- `response_adapter.py`: 将领域结果映射为前端 ask-user 或参数卡片

实现特点：

- 使用本地结构化缓存做主查询
- 使用 selection payload 承接上一轮澄清结果
- 支持源资料歧义和参数行歧义

### 8.3 维修知识域

目录：`backend/app/agent/domain/repair_knowledge`

核心组件：

- `service.py`: 从 Excel 读取维修知识
- `review.py`: 回答前审查
- `rendering.py`: 回答框架规划与渲染辅助

实现特点：

- 数据源为 `docs/fixdoc/维修知识库.xlsx`
- 先查标题，再加载正文上下文
- 结合 repair gate 对信息是否充足做判断
- 对最终回答框架做结构化规划

### 8.4 图片证据域

目录：`backend/app/agent/domain/image_evidence`

核心组件：

- `models.py`
- `service.py`

实现特点：

- 支持多模态模型输出 JSON 或自由文本
- 对场景、故障码、车辆信息做归一化
- 把识别结果转为 `ImageEvidenceAnalysis`
- 可注入后续资料检索、参数查询与诊断链路

### 8.5 故障诊断域

目录：`backend/app/agent/domain/fault_diagnosis`

当前更多承担 review 与模型结构职责，实际外部诊断调用主要由：

- `LegacyFaultDiagAdapter`
- legacy diagnosis client

完成。

### 8.6 Benchmark 域

文件：`backend/app/benchmark/doc_search.py`

职责：

- 管理数据集目录和 run 目录
- 启动、暂停、恢复评测
- 记录预测结果与运行事件
- 计算 Recall/MRR 等指标
- 导出 CSV / Excel 报表

### 8.7 开发期 Coding Engine

目录：`backend/app/coding_engine`

定位：

- 不在 `/chat` 业务链路中
- 用于 LangGraph Studio 驱动的自动编程闭环

图谱入口：

- `graph.py`

状态定义：

- `state.py`

主要节点：

- bootstrap
- prepare_workspace
- plan
- run_harness
- judge
- reflect
- code
- apply_patch
- human_gate
- persist_*

持久化落盘：

- 默认写入 `backend/.data/coding_runs/<run_id>/`

## 9. 适配层与兼容层

### 9.1 前端协议适配

文件：`backend/app/agent/adapters/frontend_protocol.py`

作用：

- 把内部 `AgentRuntimeEvent` 翻译为前端可消费的 SSE payload

### 9.2 旧系统能力适配

主要适配器：

- `legacy_doc_search_adapter.py`
- `legacy_fault_diag_adapter.py`
- `doc_search_response_adapter.py`
- `repair_knowledge_followup_adapter.py`

作用：

- 连接旧系统检索与诊断能力
- 把领域结果转为新前端协议
- 把 ask-user 与 selection payload 统一化

### 9.3 兼容接口

主要兼容接口：

- `/search`
- `/legacy/auth-enabled`
- `/legacy/validate-token`
- `/legacy/extract-token`
- `/legacy/token-diagnose`
- `/image/recognize-fault-codes`

## 10. 数据存储架构

### 10.1 Redis

主要存储：

- `message_history`
- `deferred_state`
- `case_context`
- `doc_search_external_cache`

默认 key 前缀：`crs_agent`

### 10.2 MySQL

主要 ORM 模型位于 `backend/app/legacy/models/database.py`

关键表：

- `physical_files`
- `docs`
- `external_file_urls`
- `user_feedback`
- `chat_task_logs`
- `chat_run_logs`
- `chat_run_event_logs`
- `param_knowledge_sources` 相关表
- `dim_facets`
- `dim_values`
- `system_configs`
- `admin_users`

### 10.3 本地文件

用途：

- Redis 回退存储
- Benchmark 数据集与运行结果
- Repair Knowledge Excel
- Coding engine 持久化

### 10.4 Benchmark 文件结构

根目录：`benchmarks/doc_search`

包含：

- `datasets/`
- `runs/<run_id>/status.json`
- `runs/<run_id>/report.json`
- `runs/<run_id>/predictions.jsonl`
- `runs/<run_id>/events.jsonl`
- `runs/<run_id>/failures.csv`
- `runs/<run_id>/report.xlsx`

## 11. 外部依赖架构

### 11.1 模型服务

- `pydantic_ai` runtime
- OpenRouter / OpenAI-compatible 多模态模型
- Ollama 意图模型配置项

### 11.2 业务外部服务

- ggzj 外部资料搜索
- 外部诊断服务
- token identity service
- 外部参数库 MySQL
- 阿里云语音 token 服务

### 11.3 本地依赖

- 本地 `pydantic-ai` 源码 checkout
- LangGraph / LangGraph CLI

## 12. 前后端接口协作细节

### 12.1 用户端到后端

用户端统一以 `/chat/api` 为基准路径，调用：

- `/chat/completions`
- `/chat/stream`
- `/chat/completions-with-images`
- `/chat/stream-with-images`
- `/chat/stream/abort`
- `/image/evidence-available`
- `/repair-knowledge/source/{id}`
- `/parameter-query/source/{id}`
- `/feedback`

### 12.2 管理端到后端

管理端调用：

- `/admin/auth/*`
- `/admin/dashboard/summary`
- `/admin/dimension/*`
- `/admin/logs/*`
- `/admin/feedback/*`
- `/admin/config/*`
- `/admin/benchmarks/*`

### 12.3 响应模型

聊天统一响应模型位于 `backend/app/schemas/chat.py`：

- `ChatRequest`
- `ChatResponse`
- `AskUserAnswer`
- `LifecycleCheck`

`ChatResponse.type` 当前主要包括：

- `message`
- `documents`
- `ask_user`
- `param_request`
- `error`

## 13. 可观测性与后台日志

### 13.1 Trace

文件：`backend/app/agent/observability/tracer.py`

每个请求都有 request 级 tracer，记录：

- sequence_no
- event_type
- session_id
- detail
- payload
- created_at

### 13.2 后台任务日志

文件：`backend/app/agent/observability/task_log_service.py`

作用：

- 将一次完整交互沉淀为 task / run / event 三层日志
- 提取工具调用、LLM 用量、耗时、结束原因、ask-user 次数
- 支撑后台日志页和反馈页查看详情

## 14. 配置架构

### 14.1 环境配置

文件：`backend/app/core/config.py`

配置项覆盖：

- Redis
- MySQL
- Ollama
- 搜索阈值
- case context 限额
- 参数查询开关与外部库地址
- 诊断服务
- 阿里云语音
- 图片证据
- loop guard
- agent model 与 prompt
- intent router

### 14.2 后台动态配置

管理接口：`backend/app/api/admin_config.py`

特点：

- 从 DB 读取系统配置
- 支持分类查看与批量更新
- 支持缓存刷新
- 对固定配置项做锁定

## 15. 启动与部署实现

### 15.1 本地启动

根目录脚本由 `package.json` + `scripts/dev-services.sh` 驱动：

- `npm run start`
- `npm run start:backend`
- `npm run start:frontend`
- `npm run start:admin`
- `npm run build`

### 15.2 后端依赖安装

后端使用 Python 3.11，依赖由 `backend/pyproject.toml` 管理。

### 15.3 前端构建

- 用户端和管理端均由 Vite 构建
- 仓库中存在对应 Dockerfile 与 nginx 配置

## 16. 当前架构特征总结

### 16.1 已经形成的优势

- 新后端已具备真实可运行能力，不是空骨架
- 会话可暂停、恢复、跨轮复用证据
- 文档搜索与参数查询拥有较稳定的专门工作流
- 管理后台、Benchmark 与运行日志链路完整
- 开发期 coding engine 已与业务工程共存

### 16.2 当前架构的现实形态

当前不是“一个超级 Agent 自由调用所有能力直到收敛”的纯 agentic 架构，而是：

- 专门工作流优先
- 主 Agent 兜底
- 维修问答采用多 agent 分阶段处理
- 旧系统能力通过 adapter 持续承接

这也是当前项目最重要的架构判断：它已经是可用系统，但仍处于“新旧能力混合迁移 + 编排逐步增强”的阶段。

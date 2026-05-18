# Phase2 后端改动整理

> 更新时间：2026-03-23
>
> 项目：`/Users/zhangjiexiang/VisualStudioCodeProject/crs-agent`


## 一、本轮目标

本轮工作的目标不是继续扩展 doc_search，而是把 Phase2 的两个核心闭环真正落地：

1. **故障诊断闭环**
   用户输入故障码后，Agent 可以调用工具完成：
   `故障码解析 → ECU 候选查询 → ask_user 澄清 → 诊断报告生成`

2. **流式输出闭环**
   新后端可以用 SSE 兼容旧前端：
   `start / hint / chunk / done / error`

同时补齐旧前端依赖的诊断相关兼容接口，并把本轮结果写入测试与迁移文档。


## 二、改动总览

本轮已经完成的内容：

- 故障诊断 legacy 能力迁移
- 故障诊断 domain facade 设计
- `LegacyFaultDiagAdapter` 实现
- `lookup_ecu_candidates` / `dtc_diagnosis` 工具注册
- 图片诊断与批量诊断兼容 API
- `AgentLoopService.stream()` 流式能力
- `/chat/api/chat/stream` 与 `/chat/api/chat/stream/abort`
- `ChatResponse` 兼容字段补齐
- SSE 前端协议适配真正接入
- Phase2 自动化测试补齐

当前未做的内容：

- 真实模型联调与 System Prompt 微调
- 旧项目 `task_tracker.py` / `sse_subscriber.py` 迁移
- WebSocket 通知链路
- 前端页面级迁移


## 三、架构层面的实际修改

### 3.1 配置层

在 `backend/app/core/config.py` 中补了故障诊断相关配置：

- `diagnosis_service_enabled`
- `diagnosis_service_url`
- `diagnosis_ensure_latest_path`
- `diagnosis_ensure_latest_no_back_path`
- `diagnosis_ecu_list_path`
- `diagnosis_ecus_by_fault_code_path`
- `diagnosis_image_recognize_path`
- `diagnosis_timeout`
- `diagnosis_image_timeout`
- `diagnosis_ecu_cache_ttl`

同时把系统提示词补充了故障诊断工具使用规则，让 Agent 在故障码场景下优先走工具，不是自由文本瞎答。


### 3.2 运行时依赖层

在 `backend/app/agent/runtime/deps.py` 中新增注入项：

- `diagnosis_client`
- `ecu_service`
- `fault_code_parser`

这样故障诊断能力和 doc_search 一样，走 request-scoped deps 注入，不污染全局状态。


### 3.3 Legacy 业务迁移层

新建目录：

`backend/app/legacy/services/diagnosis/`

迁移并适配了以下能力：

- `fault_code_parser.py`
- `ecu_service.py`
- `diagnosis_client.py`
- `__init__.py`

这里遵循的原则是：

1. **只搬业务链，不搬旧 handler 状态机**
2. **保留旧项目的故障码识别和诊断服务调用语义**
3. **多 ECU 情况不再走旧 pending 状态，而是转成 ask_user**

没有迁移的旧文件：

- `task_tracker.py`
- `sse_subscriber.py`

原因很明确：这两个文件依赖旧项目的 WebSocket 推送体系，属于下一阶段的通知链路，不属于本轮最小闭环。


### 3.4 Domain 层

新建目录：

`backend/app/agent/domain/fault_diagnosis/`

新增文件：

- `models.py`
- `service.py`
- `__init__.py`

这里没有照搬旧 handler，而是重新抽成了 **Facade 模式**：

- `FaultDiagnosisService.parse_fault_code()`
- `FaultDiagnosisService.lookup_ecu_candidates()`
- `FaultDiagnosisService.diagnose()`
- `FaultDiagnosisService.recognize_image()`
- `FaultDiagnosisService.get_batch_ecus()`
- `FaultDiagnosisService.get_batch_reports()`

这样做的原因是：

1. 旧项目的编排职责已经被 Agent Loop 取代
2. 新项目需要稳定、可测试的领域入口，而不是直接让 adapter 调散落的 legacy 模块
3. 后续要接 WebSocket、前端、批量接口时，可以继续复用这层


### 3.5 Adapter 层

`backend/app/agent/adapters/legacy_fault_diag_adapter.py`

这个文件从空壳变成了真正的桥接层，职责是：

- 把故障诊断 domain service 暴露给 Agent 工具
- 把多 ECU 查询结果转成 `ToolResultEnvelope(status=need_clarify)`
- 把澄清选项转成 `ClarifyCandidateOption`
- 把图片识别/批量诊断能力暴露给 API 层调用

这里最重要的设计变化是：

**旧项目的“多 ECU 候选”不再是 handler 内部状态，而是标准化为 ask_user 的澄清出口。**

这和你前面要求的 doc_search 澄清思想是一致的。


### 3.6 Agent 工具注册

在 `backend/app/agent/runtime/factory.py` 中新增注册：

- `lookup_ecu_candidates(fault_code)`
- `dtc_diagnosis(fault_code, ecu_model)`

这样 Phase2 后，Agent 的核心工具集变成：

- `search_documents`
- `analyze_doc_search_ambiguity`
- `lookup_ecu_candidates`
- `dtc_diagnosis`
- `ask_user_question`


### 3.7 流式运行时

`backend/app/agent/runtime/service.py` 是本轮改动最大的文件。

新增能力：

- `stream()`：基于 `agent.run_stream()` 进行流式执行
- `handle_stream_abort()`：保存用户中断时已生成的部分内容
- `request_id` 生成与透传
- 统一的 ask_user / message / error 响应构建

流式事件输出格式：

- `start`
- `hint`
- `chunk`
- `done`
- `error`

其中 `done` 事件固定返回：

- `response`
- `full_content`
- `request_id`

这是为了兼容旧前端当前的消费方式。


### 3.8 前端协议适配

在以下文件中做了补充：

- `backend/app/agent/models/events.py`
- `backend/app/agent/adapters/frontend_protocol.py`

新增事件类型：

- `HINT`
- `FALLBACK`

并把 `FrontendProtocolAdapter` 真正接入到流式 API 路由中，不再只是一个未使用的工具类。


### 3.9 API 层

新增文件：

- `backend/app/api/image.py`

新增并接入以下接口：

- `GET /image/diagnosis-available`
- `GET /chat/api/image/diagnosis-available`
- `POST /image/recognize-fault-codes`
- `POST /chat/api/image/recognize-fault-codes`
- `POST /diagnosis/batch-ecus`
- `POST /chat/api/diagnosis/batch-ecus`
- `POST /diagnosis/batch-reports`
- `POST /chat/api/diagnosis/batch-reports`

同时在 `backend/app/api/chat.py` 中新增：

- `POST /chat/stream`
- `POST /chat/api/chat/stream`
- `POST /chat/stream/abort`
- `POST /chat/api/chat/stream/abort`

并在 `backend/app/main.py` 中注册了新的 `image_router`。


### 3.10 Schema 兼容

在 `backend/app/schemas/chat.py` 中，`ChatResponse` 新增兼容字段：

- `request_id`
- `lifecycle_info`
- `result_summary`
- `hints`
- `suggestions`

同时新增：

- `StreamAbortRequest`

这一步的目的不是把旧项目所有业务状态都搬回来，而是保证旧前端字段访问时不炸。


### 3.11 依赖补充

在 `backend/pyproject.toml` 中新增：

- `python-multipart`

原因很直接：图片上传接口需要它。


## 四、关键调用链整理

### 4.1 故障诊断链路

用户输入：

`P01F5 故障码`

运行路径：

1. Agent 判断为故障诊断场景
2. 调 `lookup_ecu_candidates`
3. adapter 进入 `FaultDiagnosisService.lookup_ecu_candidates()`
4. `FaultCodeParser` 规范化故障码
5. `DiagnosisServiceClient.get_ecus_by_fault_code()` 查 ECU
6. 如果 ECU > 1，则返回 `need_clarify`
7. Agent 调 `ask_user_question`
8. 用户选择 ECU 后，Agent 调 `dtc_diagnosis`
9. `FaultDiagnosisService.diagnose()` 调 `ensure_latest`
10. 返回 ready/generating 结果


### 4.2 流式链路

用户请求：

`POST /chat/api/chat/stream`

运行路径：

1. API 层进入 `chat_stream()`
2. 构建 request-scoped runtime deps
3. 调 `AgentLoopService.stream()`
4. `agent.run_stream()` 开始执行
5. 文本增量通过 `FrontendProtocolAdapter` 转成 `chunk`
6. 结束时统一输出 `done`
7. 如果用户中断，前端调用 `/chat/api/chat/stream/abort`
8. `handle_stream_abort()` 把部分内容写回消息历史


## 五、本轮新增/修改的主要文件

### 5.1 新增文件

- `backend/app/legacy/services/diagnosis/__init__.py`
- `backend/app/legacy/services/diagnosis/fault_code_parser.py`
- `backend/app/legacy/services/diagnosis/ecu_service.py`
- `backend/app/legacy/services/diagnosis/diagnosis_client.py`
- `backend/app/agent/domain/fault_diagnosis/__init__.py`
- `backend/app/agent/domain/fault_diagnosis/models.py`
- `backend/app/agent/domain/fault_diagnosis/service.py`
- `backend/app/api/image.py`
- `backend/tests/test_phase2_diagnosis_stream.py`


### 5.2 重点修改文件

- `backend/app/core/config.py`
- `backend/app/agent/runtime/deps.py`
- `backend/app/agent/runtime/factory.py`
- `backend/app/agent/runtime/service.py`
- `backend/app/agent/adapters/legacy_fault_diag_adapter.py`
- `backend/app/agent/models/events.py`
- `backend/app/agent/adapters/frontend_protocol.py`
- `backend/app/api/chat.py`
- `backend/app/main.py`
- `backend/app/schemas/chat.py`
- `backend/pyproject.toml`
- `设计方案/迁移状态与剩余步骤.md`


## 六、测试结果

本轮新增了 Phase2 专项测试，覆盖：

- 诊断工具多 ECU → `need_clarify`
- 诊断工具单 ECU/ready 报告
- 图片诊断与批量诊断兼容 API
- SSE 流式输出
- 流式中断后的部分内容保存

最终测试结果：

```bash
cd backend && pytest -q
52 passed
```


## 七、当前限制

本轮已经把 Phase2 的后端骨架和核心链路打通，但还存在以下边界：

1. **真实模型还没联调**
   当前已经具备工具和流式能力，但还没用真实模型验证 Prompt 在故障诊断场景下的稳定性。

2. **外部诊断服务只完成接口接入，未完成线上联调**
   当前代码按旧项目协议接好了，但需要你本地真实服务验证 URL、鉴权和返回结构。

3. **MySQL 默认密码未写入源码**
   代码中没有硬编码你的数据库密码。若要直接连本地真实库，需要在本地 `.env` 中提供：
   `CRS_MYSQL_PASSWORD=@20040824`

4. **旧的任务跟踪 / SSE 订阅通知未迁移**
   `task_tracker.py` 和 `sse_subscriber.py` 还没搬，因为它们和后续 WebSocket/通知体系耦合。

5. **前端页面尚未迁移**
   当前只是把后端兼容 API 补齐，`frontend/user` / `frontend/admin` 还没开始正式搬。


## 八、下一步建议

最合理的顺序是：

1. **真实模型联调**
   用真实模型测试：
   - 文档搜索澄清
   - 故障码多 ECU 澄清
   - 直接诊断

2. **前端迁移分析**
   先完整梳理旧前端 `frontend/user` 和 `frontend/admin` 的接口依赖，再决定是直接复制还是边搬边适配。

3. **进入 Phase3**
   补语音、文件预览、反馈、日志、WebSocket 推送等用户体验相关接口。


## 九、结论

这次不是做了一组零散适配，而是把 Phase2 的后端主干真正搭起来了：

- 故障诊断可以进入 Agent Loop
- 多 ECU 候选已经统一为 ask_user
- 流式 SSE 已兼容旧前端
- 诊断图片/批量接口已补齐
- 自动化测试已锁住核心行为

也就是说，**新项目现在已经具备“搜索 + 诊断 + 流式”的后端核心能力**，后续主要进入真实联调和前端迁移阶段。

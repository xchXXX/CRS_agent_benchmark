# Backend

这里是新项目后端。

设计目标：

- 用干净的 `FastAPI + Agent Runtime` 结构替代旧项目的 `Orchestrator + IntentRouter + Handler` 主链路
- 在不引入旧项目历史包袱的前提下，逐步复制或适配旧能力
- 让 `Pydantic AI`、`AskUser`、`Mem0`、前端协议适配都有明确落点

当前状态：

- 已接入 `Pydantic AI` 真实运行时
- `/chat/completions` 默认使用 `test` model，可直接返回 Agent 响应
- `ask_user_question` 已按 deferred external tool 方式接入
- 支持在后续请求里通过 `ask_user_answer` + `tool_call_id` 续跑
- `message history` 和 `deferred state` 默认走 Redis 存储
- 已接入 `doc_search`、故障诊断、参数查询、维修知识问答和图片证据识别能力

## 本地接入

推荐先在 `backend/` 下创建项目私有虚拟环境：

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -U pip
```

然后安装后端依赖：

```bash
.venv/bin/python -m pip install -e .
```

本地启动 LangGraph Studio 开发服务：

```bash
.venv/bin/langgraph dev --config langgraph.json
```

LangGraph Studio 当前暴露的是开发期 `coding_engine`，用于构建 Harness 驱动的自动编程闭环，不在 `/chat` 业务运行时链路中。
它会读取当前目录下的 `.env` 和 `.env.runtime`；`CRS_CODING_ENGINE_MODEL` 可覆盖编程引擎模型，未配置时回退使用 `CRS_AGENT_MODEL`。
`OPENROUTER_API_KEY` 放在 `.env.runtime`。

`coding_engine` 的默认行为是先运行 Harness，再根据失败日志生成 patch proposal；默认不会自动应用 patch。
每次 run 的本地状态、事件、Harness 日志和 patch proposal 会落到 `backend/.data/coding_runs/<run_id>/`；
如需指定路径，可在输入里传 `run_root`。
如果要在隔离 workspace 中试跑自动应用，可以在 Studio 输入里设置：

```json
{
  "task": "描述要完成的开发任务",
  "harness_command": "cd backend && .venv/bin/python -m pytest tests/test_coding_engine.py",
  "sandbox_enabled": true,
  "auto_apply_patch": true,
  "max_iterations": 3
}
```

如果 `sandbox_enabled=false`，除非显式设置 `allow_unsandboxed_apply=true`，否则引擎会拒绝自动改文件并停在人工审核节点。
如果不想写入本地归档，可设置 `persistence_enabled=false`，默认值为 `true`。

## 关键环境变量

- `CRS_REDIS_URL=redis://127.0.0.1:6379/0`
  当前默认值。短期会话历史和 deferred state 会写到这里。
- `CRS_REDIS_KEY_PREFIX=crs_agent`
  Redis key 前缀。
- `CRS_MESSAGE_HISTORY_TTL_SECONDS=604800`
  会话 history TTL，默认 7 天。
- `CRS_DEFERRED_STATE_TTL_SECONDS=604800`
  `ask_user_question` 等 deferred state TTL，默认 7 天。
- `CRS_AGENT_MODEL=test`
  当前默认值。适合先验证运行时链路。
- `CRS_AGENT_TEST_CALL_TOOLS=ask_user_question`
  可用于本地强制触发 deferred ask-user 调试。
- `CRS_AGENT_MODEL=openai:gpt-5.2`
  切换到真实模型时使用。对应 provider 依赖和密钥需要另行准备。
- `CRS_IMAGE_EVIDENCE_ENABLED=true`
  是否启用通用图片证据识别。
- `CRS_IMAGE_EVIDENCE_MODEL=qwen/qwen3.5-flash-02-23`
  图片证据识别模型名。默认走 OpenRouter，实际模型 slug 可按服务商配置覆盖。
- `CRS_IMAGE_EVIDENCE_BASE_URL=https://openrouter.ai/api/v1`
  OpenAI-compatible 图片识别服务地址。
- `CRS_IMAGE_EVIDENCE_API_KEY=...`
  图片识别模型 API Key；为空时会回退读取 `OPENROUTER_API_KEY`。
- `CRS_IMAGE_EVIDENCE_MAX_IMAGES=3`
  单次通用图片证据识别最多上传图片数。
- `CRS_IMAGE_EVIDENCE_MAX_IMAGE_MB=8`
  单张图片上传大小限制，单位 MB。

## 图片证据接口

- `GET /chat/api/image/evidence-available`
  返回通用图片证据识别是否可用，以及单次图片数和大小限制。
- `POST /chat/api/image/analyze-evidence`
  使用 multipart/form-data 上传 `images`，支持 1 到 3 张图片。返回 `ImageEvidenceResponse`，其中 `evidence` 包含车辆信息、故障码、诊断仪文字、可见文本和建议查询。
- `POST /chat/api/image/recognize-fault-codes`
  保留旧故障码识别接口兼容性。现在会优先通过通用图片证据识别提取故障码，并在返回体中附带 `image_evidence`。

前端或调用方可在后续 `/chat/completions`、`/chat/stream` 请求的 `context.image_evidence` 或 `context.image_evidences` 中传入上述结构化证据。AgentLoop 会将其写入 `CaseContext`，并用于意图路由、资料检索查询增强、故障码诊断、维修问答和参数查询。

## 当前 Redis Key

- `crs_agent:message_history:<session_id>`
  存储整段 Pydantic AI message history JSON。
- `crs_agent:deferred_state:<session_id>:<tool_call_id>`
  存储 deferred tool 恢复所需快照。

如果 Redis 不可用，当前实现会回退到本地 `.data/` 文件存储，避免本地开发被直接阻塞。

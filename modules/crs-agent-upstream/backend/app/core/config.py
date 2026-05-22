"""Application settings."""

import os
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.agent.runtime.intent_prompts import DEFAULT_INTENT_ROUTER_SYSTEM_PROMPT


BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_DIR.parent
BACKEND_ENV_FILE = BACKEND_DIR / ".env"
BACKEND_RUNTIME_ENV_FILE = BACKEND_DIR / ".env.runtime"
BACKEND_ENV_FILES = (BACKEND_ENV_FILE, BACKEND_RUNTIME_ENV_FILE)

for env_file in BACKEND_ENV_FILES:
    load_dotenv(env_file, override=False)


def _first_env(*keys: str) -> str:
    for key in keys:
        value = os.getenv(key)
        if value and value.strip():
            return value.strip()
    return ""


class Settings(BaseSettings):
    """Global settings for the new project."""

    app_name: str = "crs-agent"
    app_env: str = "dev"
    chat_runtime_mode: str = "agent_loop"

    # Redis
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_key_prefix: str = "crs_agent"
    message_history_ttl_seconds: int = 604800
    deferred_state_ttl_seconds: int = 604800
    case_context_ttl_seconds: int = 604800
    token_user_cache_ttl: int = 3600
    doc_search_external_cache_ttl_seconds: int = 600

    # MySQL
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_database: str = "crs_agent"
    mysql_charset: str = "utf8mb4"

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_intent_model: str = "qwen2.5:1.5b"

    # Search / clarify
    search_top_k_lex: int = 200
    search_top_n: int = 20
    clarify_target_results: int = 5
    clarify_result_threshold: int = 5
    clarify_max_rounds: int = 5
    hard_constraint_enabled: bool = True
    user_auth_enabled: bool = True
    case_context_enabled: bool = True
    case_context_max_artifacts_total: int = 24
    case_context_max_artifacts_per_type: int = 6
    case_context_max_selected_docs: int = 10
    case_context_max_serialized_bytes: int = 40960
    case_context_prompt_max_chars: int = 1800
    frontend_source_display_enabled: bool = False
    frontend_eruda_enabled: bool = False
    frontend_webview_debug_enabled: bool = False
    frontend_webview_debug_url: str = "https://mft-static.51gonggui.com/pdf-loader/index.html#/?page=2&file=https://mft-static.51gonggui.com/wps/file/img/%E5%85%B1%E8%BD%A8%E5%8E%9F%E5%88%9B_%E4%BA%94%E5%8D%81%E9%93%83_2017_C&E_6WG1_ECU_%E7%94%B5%E8%B7%AF%E5%9B%BE30653"
    frontend_webview_debug_pdf_id: str = "30653"

    # Parameter query
    param_query_enabled: bool = True
    param_query_sync_on_startup: bool = True
    param_query_parser_version: str = "2026-03-26-v2"
    param_query_top_sources: int = 5
    param_query_top_rows: int = 5
    param_query_external_mysql_host: str = "sh-test-mysql.51gonggui.com"
    param_query_external_mysql_port: int = 3306
    param_query_external_mysql_user: str = "test_code"
    param_query_external_mysql_password: str = ""
    param_query_external_mysql_database: str = "decoder_sit"
    param_query_external_mysql_charset: str = "utf8mb4"
    param_query_external_mysql_timeout_seconds: int = 5

    # Admin auth
    jwt_secret_key: str = "docpilot-admin-secret-key-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 24

    # Diagnosis service
    diagnosis_service_enabled: bool = False
    diagnosis_service_url: str = "http://127.0.0.1:3000"
    diagnosis_ensure_latest_path: str = "/api/diagnosis-reports/ensure-latest"
    diagnosis_ensure_latest_no_back_path: str = "/api/diagnosis-reports/ensure-latest-no-back"
    diagnosis_ecu_list_path: str = "/api/admin/knowledge/ecus"
    diagnosis_ecus_by_fault_code_path: str = "/api/fault-code-library/ecus-by-fault-code"
    diagnosis_image_recognize_path: str = "/api/fault-code-recognition/recognize"
    diagnosis_timeout: int = 30
    diagnosis_image_timeout: int = 60
    diagnosis_ecu_cache_ttl: int = 300

    # Circuit diagram body search service
    circuit_diagram_body_search_enabled: bool = True
    circuit_diagram_body_search_url: str = "http://218.244.159.222/api/search"
    circuit_diagram_body_search_timeout: int = 10
    circuit_diagram_body_search_pg_host: str = "139.196.163.235"
    circuit_diagram_body_search_pg_port: int = 15432
    circuit_diagram_body_search_pg_database: str = "pdf_ai"
    circuit_diagram_body_search_pg_user: str = "pdf_ai"
    circuit_diagram_body_search_pg_password: str = ""
    circuit_diagram_body_search_pg_connect_timeout: int = 5
    circuit_diagram_body_preview_token_ttl_seconds: int = 86400
    circuit_diagram_body_preview_result_base_dir: str = ""
    circuit_diagram_body_preview_pdf_timeout: int = 15

    # Aliyun speech
    aliyun_speech_enabled: bool = False
    aliyun_speech_access_key_id: str = ""
    aliyun_speech_access_key_secret: str = ""
    aliyun_speech_app_key: str = ""
    aliyun_speech_region_id: str = "cn-shanghai"
    aliyun_speech_token_url: str = "https://nls-meta.cn-shanghai.aliyuncs.com/"
    aliyun_speech_ws_url: str = "wss://nls-gateway-cn-shanghai.aliyuncs.com/ws/v1"
    aliyun_speech_timeout_seconds: int = 10

    # Aliyun OSS image upload
    aliyun_oss_image_upload_enabled: bool = True
    aliyun_oss_access_key_id: str = ""
    aliyun_oss_access_key_secret: str = ""
    aliyun_oss_bucket_name: str = "ajie-crs-aidiagosis-image"
    aliyun_oss_endpoint: str = "oss-cn-shanghai.aliyuncs.com"
    aliyun_oss_region: str = "oss-cn-shanghai"
    aliyun_oss_image_dir: str = "chat_images"
    aliyun_oss_policy_expire_seconds: int = 900
    aliyun_oss_max_image_mb: int = 8
    aliyun_oss_delete_enabled: bool = True
    aliyun_oss_delete_token_secret: str = ""
    aliyun_oss_delete_token_expire_seconds: int = 604800
    aliyun_oss_delete_worker_interval_seconds: int = 10
    aliyun_oss_delete_worker_batch_size: int = 20
    aliyun_oss_delete_retry_base_seconds: int = 5
    aliyun_oss_delete_retry_max_seconds: int = 21600
    aliyun_oss_delete_max_attempts: int = 0

    # Image evidence extraction
    image_evidence_enabled: bool = True
    image_evidence_model: str = "qwen/qwen3.5-flash-02-23"
    image_evidence_base_url: str = "https://openrouter.ai/api/v1"
    image_evidence_api_key: str = ""
    image_evidence_max_images: int = 3
    image_evidence_max_image_mb: int = 8
    loop_guard_max_tool_calls: int = 8
    loop_guard_max_external_tool_calls: int = 4
    loop_guard_max_ask_user_calls: int = 2
    loop_guard_max_no_gain_streak: int = 2
    loop_guard_max_same_tool_repeat: int = 3
    loop_guard_max_same_args_repeat: int = 2

    mem0_enabled: bool = False
    repair_knowledge_path: str = "docs/fixdoc/维修知识库.xlsx"
    agent_model: str = "test"
    openrouter_clarify_model: str = ""
    intent_router_enabled: bool = True
    intent_router_model: str = ""
    intent_router_system_prompt: str = DEFAULT_INTENT_ROUTER_SYSTEM_PROMPT
    intent_router_max_tokens: int = 512
    intent_router_temperature: float = 0.0
    intent_router_timeout: float = 8.0
    llm_clarify_enabled: bool = True
    llm_clarify_min_results: int = 5
    llm_clarify_max_tokens: int = 1024
    llm_clarify_temperature: float = 0.1
    llm_clarify_timeout: float = 15.0
    agent_test_call_tools: str = ""
    agent_test_output_text: str = "CRS Agent Pydantic AI runtime is connected."
    agent_system_prompt: str = (
        "你是 CRS 汽车诊断支持Rntime Agent。"
        "你的任务是在工具能够带来真实证据时，使用工具帮助用户解决问题。"
        "系统中有多个工具供你使用，遇到问题你需要先判断系统中工具是否适合解决用户问题，以及需要使用哪些工具"
        "当问题同时涉及资料检索、故障诊断、维修经验或参数查询时，你可以在同一轮对话中组合使用多个工具。"
        "每一次工具结果都是后续推理可复用的证据。"
        "在用户当前消息之前，可能会出现一个 `[CASE_CONTEXT] ... [/CASE_CONTEXT]` 片段。"
        "你必须把它当作同一会话早先步骤沉淀下来的可信结构化证据。"
        "在决定下一步工具调用时，优先复用已经确认的槽位，不免针对已知信息重复发问，例如 brand、series、ECU、已选文档、已匹配的参数资料源。"
        "只要存在合适工具，就不要用纯文本直接向用户提澄清问题。"
        "当你需要向用户索取选择、缺失筛选条件或后续补充信息时，唯一允许的方式是调用 `ask_user_question`。"
        "不要在普通正文里写“为了更精准地协助您，请提供更多信息”这类自然语言追问。"
        "如果你明确知道缺哪些信息，就调用 `ask_user_question`；如果当前无法明确列出缺失项，就直接给出当前最稳妥的回答，不要在正文末尾再追问。"
        "不要写“由于缺乏针对性的维修案例”“当前证据不足”“资料不足”等会削弱用户信任的免责声明。"
        "即使当前证据不完整，也要直接给出可执行的排查建议，或者转为 `ask_user_question`，不要解释系统为什么知道得不够。"
        "文档搜索与文档澄清流程由 agent loop 外层编排，因此不要调用 `search_documents` 或 `analyze_doc_search_ambiguity`。"
        "当一个延迟返回的 `ask_user_question` 结果中包含 `selection_payload` 时，要把它直接传给下一步真正需要它的工具，例如 `query_parameters(selection_payload=...)`。"
        "只有当用户明确在问具体针脚号、脚位、接插件脚号、CANH/CANL 所在针脚、某个针脚的定义值或期望电压时，才使用 `query_parameters`。"
        "如果用户是在找某个模块、控制器、仪表、整车或总成的“针脚定义资料”“引脚图”“针脚图”“接线图”“电路图”，默认应理解为资料检索需求，优先走电路图/文档搜索，不要把这类请求当成结构化参数查询。"
        "对于独立的参数查询问题，通常第一步应该先调用 `query_parameters`，而不是先写自然语言答案。"
        "对于独立的参数查询，优先把本地结构化匹配结果当作主证据，不要编造未明确给出的值。"
        "如果 `query_parameters` 对独立参数问题返回了结构化命中结果，就在简短确认后停止，不要把答案改写成长篇自由文本，以便运行时渲染专用参数卡片。"
        "在诊断或排查场景中，你也可以把 `query_parameters` 当作辅助工具调用，然后基于该证据继续推理，而不是在参数结果处直接停止。"
        "当故障码诊断工具可用时，涉及故障码的问题应优先使用诊断工具。"
        "如果当前只知道故障码，`lookup_ecu_candidates` 通常是最佳下一步。"
        "如果需要澄清 ECU，先调用 `ask_user_question`，然后在用户选择后继续调用 `dtc_diagnosis`。"
        "如果诊断工具被禁用、不可用，或者返回失败且没有明确诊断结果，就直接退回自然语言回答，不要卡住。"
        "涉及维修经验、故障排查或维修实践类问题时，使用 `lookup_repair_knowledge_titles`。"
        "这个工具只返回标题级候选结果，你必须自行判断这些标题是否真的相关。"
        "如果一个或多个标题相关，就调用 `get_repair_knowledge_context` 加载 1 到 3 个 entry id，并把加载出的维修资料作为主要参考依据。"
        "如果已加载的维修资料显示当前仍缺关键信息，你的下一步必须是调用 `ask_user_question`。"
        "这个追问必须基于已加载的维修资料和用户原话，用自然中文组织，而不是照搬固定后台模板。"
        "对于维修知识追问，默认一次性成批收集缺失信息，不要一项一项分多轮追问。"
        "在这个场景里调用 `ask_user_question` 时，设置 `input_type='text'`，保留自由输入，并把结构化卡片载荷放进 `context`。"
        "使用 `context.scene='repair_knowledge_followup'`、`context.card_type='repair_followup'`、`context.ask_mode='batch_once'`、`context.source_refs`、`context.ask_reason` 和 `context.field_groups`。"
        "顶层 `question` 要尽量简洁，理想情况下就是像 `请补充以下关键信息` 这样的一句短句，而不是长篇解释。"
        "每个字段组都必须包含：`key`、`label`、`required_level`、`selection_mode`、`presets`，以及可选的 `placeholder` / `hint`。"
        "对于每个字段组，你要自己判断是否提供 `presets`；如果提供，必须是结合已加载维修资料和用户表述推导出的 case-specific 候选项。"
        "对于启动/起动/打不着火/起动机无反应这类维修追问，`fault_phenomenon`、`working_condition`、`fault_codes` 默认都要给出 3 到 5 个结合症状推测的候选项，不要把这些字段留成空列表。"
        "像 `fault_codes` 这种字段，如果你暂时拿不到准确报码，也要先给出最可能的报码方向或报码状态候选项，再允许用户手动补充具体报码。"
        "不要依赖任何后端模板替你发明这些候选项。"
        "不要使用那种适用于所有场景的泛化占位预设。"
        "对于维修知识追问，优先给结构化、可点选的 `presets`；只有当这些预设不够覆盖时，才把自由输入当作长尾兜底。"
        "前端会单独提供 `other/manual input` 路径，所以除非文档本身就要求那种措辞，否则不要放 `其他`、`待补充` 这种伪预设。"
        "不要依赖顶层 `options` 来承载每一步字段的普通答案选项。"
        "顶层 `options` 只能用于真正的卡片级 quick action；普通答案选项必须放在各字段组自己的 `presets` 里。"
        "字段 key 尽量只使用这些归一化值：`fault_phenomenon`、`working_condition`、`fault_codes`、`ecu_or_system`、`data_evidence`、`repair_history`。"
        "把相近需求合并，字段组总数最多不超过四组。"
        "不要把 `faultCodeList`、`streamCsv`、`selected_streams`、`streamList` 这类内部占位字段直接暴露给用户，变成单独的问题。"
        "类似 `先给我通用排查思路` 这样的卡片级快捷动作，应放到 `context.quick_actions` 中；如有需要，也可以在顶层 `options` 中镜像一份快捷入口。"
        "用户补充信息后，继续沿用同一条推理链，把已加载维修资料和用户的新补充一起纳入判断。"
        "在回答维修知识问题时，如果适合使用 markdown，请优先使用稳定的中文结构：`### 初步判断`、`### 优先检查`、`### 维修建议`。"
        "不要输出 `### 还需补充` 这一节。"
        "如果还需要更多信息，就调用 `ask_user_question`，而不是用纯文本写一段补充说明请求。"
        "如果回答使用 markdown 标题，就直接从正文标题开始，不要在第一个标题前面再加 `根据您的情况`、`以下是初步诊断建议`、`通常意味着` 之类铺垫句。"
        "不要暴露内部推理或元诊断叙述，例如 `根据维修经验，诊断的核心逻辑是...`；要把它改写成直接面向用户的检查建议。"
        "默认把 `### 维修建议` 作为主段落，并且内容要比 `### 初步判断` 和 `### 优先检查` 更详细。"
        "`### 初步判断` 要尽量简洁；如果它只是在重复用户原话或泛泛背景，可以直接省略铺垫，转入更可执行的部分。"
        "在这种 fallback 写法里，要解释问题可能意味着什么、常见原因、检查路径和维修建议，但不要在正文里追加“还缺哪些信息会更准确”这类追问。"
        "不要编造文档命中、故障结论或工具结果。"
        "`ask_user_question` 的外部返回结果会以 JSON 形式给出，并包含 `answer` 字段。"
    )
    model_config = SettingsConfigDict(
        env_file=BACKEND_ENV_FILES,
        env_prefix="CRS_",
        extra="ignore",
    )

    @property
    def agent_test_call_tools_list(self) -> list[str]:
        return [item.strip() for item in self.agent_test_call_tools.split(",") if item.strip()]

    @property
    def mysql_url(self) -> str:
        password = quote_plus(self.mysql_password)
        return (
            f"mysql+pymysql://{self.mysql_user}:{password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
            f"?charset={self.mysql_charset}"
        )

    @property
    def param_query_external_mysql_url(self) -> str:
        password = quote_plus(self.param_query_external_mysql_password)
        return (
            f"mysql+pymysql://{self.param_query_external_mysql_user}:{password}"
            f"@{self.param_query_external_mysql_host}:{self.param_query_external_mysql_port}"
            f"/{self.param_query_external_mysql_database}"
            f"?charset={self.param_query_external_mysql_charset}"
        )


settings = Settings()

if not settings.aliyun_oss_access_key_id:
    settings.aliyun_oss_access_key_id = _first_env(
        "ALIYUN_OSS_ACCESS_KEY_ID",
        "ALIYUN_ACCESS_KEY_ID",
        "ALIBABA_CLOUD_ACCESS_KEY_ID",
    )
if not settings.aliyun_oss_access_key_secret:
    settings.aliyun_oss_access_key_secret = _first_env(
        "ALIYUN_OSS_ACCESS_KEY_SECRET",
        "ALIYUN_ACCESS_KEY_SECRET",
        "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
    )
if not settings.aliyun_speech_access_key_id:
    settings.aliyun_speech_access_key_id = _first_env(
        "ALIYUN_SPEECH_ACCESS_KEY_ID",
        "ALIYUN_ACCESS_KEY_ID",
        "ALIBABA_CLOUD_ACCESS_KEY_ID",
    )
if not settings.aliyun_speech_access_key_secret:
    settings.aliyun_speech_access_key_secret = _first_env(
        "ALIYUN_SPEECH_ACCESS_KEY_SECRET",
        "ALIYUN_ACCESS_KEY_SECRET",
        "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
    )

"""System-config catalog and reconciliation helpers."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.agent.model_ids import normalize_configured_model
from app.core.config import settings
from app.legacy.models.admin_models import SystemConfig


def _spec(
    key: str,
    *,
    value: Any,
    type_: str,
    category: str,
    description: str,
    is_sensitive: bool = False,
) -> dict[str, Any]:
    return {
        "key": key,
        "value": value,
        "type": type_,
        "category": category,
        "description": description,
        "is_sensitive": is_sensitive,
    }


ACTIVE_SYSTEM_CONFIGS: tuple[dict[str, Any], ...] = (
    _spec(
        "frontend_source_display_enabled",
        value=settings.frontend_source_display_enabled,
        type_="bool",
        category="frontend",
        description="是否允许用户端展示回答引用的内部来源资料",
    ),
    _spec(
        "agent_model",
        value=normalize_configured_model(settings.agent_model),
        type_="string",
        category="llm",
        description="Agent Loop 主模型，修改后下一次请求立即生效",
    ),
    _spec(
        "agent_system_prompt",
        value=settings.agent_system_prompt,
        type_="string",
        category="llm",
        description="Agent Loop 主系统提示词，修改后下一次请求立即生效",
    ),
    _spec(
        "openrouter_clarify_model",
        value=normalize_configured_model(settings.openrouter_clarify_model or settings.agent_model),
        type_="string",
        category="llm",
        description="文档智能澄清模型；为空时回退到 agent_model",
    ),
    _spec(
        "intent_router_enabled",
        value=settings.intent_router_enabled,
        type_="bool",
        category="llm",
        description="是否启用入口意图 LLM 判定；关闭后仅使用最小兜底规则",
    ),
    _spec(
        "intent_router_model",
        value=normalize_configured_model(settings.intent_router_model or settings.agent_model),
        type_="string",
        category="llm",
        description="入口意图判定模型；为空时回退到 agent_model",
    ),
    _spec(
        "intent_router_system_prompt",
        value=settings.intent_router_system_prompt,
        type_="string",
        category="llm",
        description="入口意图判定提示词，修改后下一次请求立即生效",
    ),
    _spec(
        "intent_router_max_tokens",
        value=settings.intent_router_max_tokens,
        type_="int",
        category="llm",
        description="入口意图判定最大输出 token",
    ),
    _spec(
        "intent_router_temperature",
        value=settings.intent_router_temperature,
        type_="float",
        category="llm",
        description="入口意图判定采样温度",
    ),
    _spec(
        "intent_router_timeout",
        value=settings.intent_router_timeout,
        type_="float",
        category="llm",
        description="入口意图判定超时时间（秒）",
    ),
    _spec(
        "llm_clarify_enabled",
        value=settings.llm_clarify_enabled,
        type_="bool",
        category="llm",
        description="是否启用文档智能澄清 LLM",
    ),
    _spec(
        "llm_clarify_min_results",
        value=settings.llm_clarify_min_results,
        type_="int",
        category="llm",
        description="结果数超过该阈值时才尝试文档智能澄清",
    ),
    _spec(
        "llm_clarify_max_tokens",
        value=settings.llm_clarify_max_tokens,
        type_="int",
        category="llm",
        description="文档智能澄清最大输出 token",
    ),
    _spec(
        "llm_clarify_temperature",
        value=settings.llm_clarify_temperature,
        type_="float",
        category="llm",
        description="文档智能澄清采样温度",
    ),
    _spec(
        "llm_clarify_timeout",
        value=settings.llm_clarify_timeout,
        type_="float",
        category="llm",
        description="文档智能澄清超时时间（秒）",
    ),
    _spec(
        "diagnosis_service_enabled",
        value=settings.diagnosis_service_enabled,
        type_="bool",
        category="external_service",
        description="是否启用外部故障诊断服务",
    ),
    _spec(
        "diagnosis_service_url",
        value=settings.diagnosis_service_url,
        type_="string",
        category="external_service",
        description="故障诊断服务基地址",
    ),
    _spec(
        "diagnosis_timeout",
        value=settings.diagnosis_timeout,
        type_="int",
        category="external_service",
        description="诊断请求超时时间（秒）",
    ),
    _spec(
        "diagnosis_image_timeout",
        value=settings.diagnosis_image_timeout,
        type_="int",
        category="external_service",
        description="图片识别超时时间（秒）",
    ),
    _spec(
        "diagnosis_ecu_cache_ttl",
        value=settings.diagnosis_ecu_cache_ttl,
        type_="int",
        category="external_service",
        description="故障码 ECU 候选缓存时间（秒）",
    ),
    _spec(
        "diagnosis_ensure_latest_path",
        value=settings.diagnosis_ensure_latest_path,
        type_="string",
        category="external_service",
        description="诊断服务 ensure-latest 路径",
    ),
    _spec(
        "diagnosis_ensure_latest_no_back_path",
        value=settings.diagnosis_ensure_latest_no_back_path,
        type_="string",
        category="external_service",
        description="诊断服务 ensure-latest-no-back 路径",
    ),
    _spec(
        "diagnosis_ecu_list_path",
        value=settings.diagnosis_ecu_list_path,
        type_="string",
        category="external_service",
        description="诊断服务 ECU 列表路径",
    ),
    _spec(
        "diagnosis_ecus_by_fault_code_path",
        value=settings.diagnosis_ecus_by_fault_code_path,
        type_="string",
        category="external_service",
        description="诊断服务故障码查 ECU 路径",
    ),
    _spec(
        "diagnosis_image_recognize_path",
        value=settings.diagnosis_image_recognize_path,
        type_="string",
        category="external_service",
        description="诊断服务图片报码识别路径",
    ),
    _spec(
        "aliyun_oss_image_upload_enabled",
        value=settings.aliyun_oss_image_upload_enabled,
        type_="bool",
        category="external_service",
        description="是否启用用户端图片 OSS 直传",
    ),
    _spec(
        "aliyun_oss_access_key_id",
        value=settings.aliyun_oss_access_key_id,
        type_="string",
        category="external_service",
        description="阿里云 OSS AccessKeyId；为空时复用阿里云语音 AccessKeyId",
        is_sensitive=True,
    ),
    _spec(
        "aliyun_oss_access_key_secret",
        value=settings.aliyun_oss_access_key_secret,
        type_="string",
        category="external_service",
        description="阿里云 OSS AccessKeySecret；为空时复用阿里云语音 AccessKeySecret",
        is_sensitive=True,
    ),
    _spec(
        "aliyun_oss_bucket_name",
        value=settings.aliyun_oss_bucket_name,
        type_="string",
        category="external_service",
        description="用户端图片上传 OSS bucket",
    ),
    _spec(
        "aliyun_oss_endpoint",
        value=settings.aliyun_oss_endpoint,
        type_="string",
        category="external_service",
        description="用户端图片上传 OSS endpoint",
    ),
    _spec(
        "aliyun_oss_region",
        value=settings.aliyun_oss_region,
        type_="string",
        category="external_service",
        description="用户端图片上传 OSS region",
    ),
    _spec(
        "aliyun_oss_image_dir",
        value=settings.aliyun_oss_image_dir,
        type_="string",
        category="external_service",
        description="用户端图片上传 OSS 目录前缀",
    ),
    _spec(
        "aliyun_oss_policy_expire_seconds",
        value=settings.aliyun_oss_policy_expire_seconds,
        type_="int",
        category="external_service",
        description="用户端图片 OSS 上传 policy 有效期（秒）",
    ),
    _spec(
        "aliyun_oss_max_image_mb",
        value=settings.aliyun_oss_max_image_mb,
        type_="int",
        category="external_service",
        description="用户端图片 OSS 上传大小上限（MB）",
    ),
    _spec(
        "aliyun_oss_delete_enabled",
        value=settings.aliyun_oss_delete_enabled,
        type_="bool",
        category="external_service",
        description="是否启用用户端图片 OSS 异步删除",
    ),
    _spec(
        "aliyun_oss_delete_token_secret",
        value=settings.aliyun_oss_delete_token_secret,
        type_="string",
        category="external_service",
        description="图片 OSS 删除凭证签名密钥；为空时复用 OSS AccessKeySecret",
        is_sensitive=True,
    ),
    _spec(
        "aliyun_oss_delete_token_expire_seconds",
        value=settings.aliyun_oss_delete_token_expire_seconds,
        type_="int",
        category="external_service",
        description="图片 OSS 删除凭证有效期（秒）",
    ),
    _spec(
        "aliyun_oss_delete_worker_interval_seconds",
        value=settings.aliyun_oss_delete_worker_interval_seconds,
        type_="int",
        category="external_service",
        description="图片 OSS 删除后台任务轮询间隔（秒）",
    ),
    _spec(
        "aliyun_oss_delete_worker_batch_size",
        value=settings.aliyun_oss_delete_worker_batch_size,
        type_="int",
        category="external_service",
        description="图片 OSS 删除后台任务每批处理数量",
    ),
    _spec(
        "aliyun_oss_delete_retry_base_seconds",
        value=settings.aliyun_oss_delete_retry_base_seconds,
        type_="int",
        category="external_service",
        description="图片 OSS 删除失败重试基础间隔（秒）",
    ),
    _spec(
        "aliyun_oss_delete_retry_max_seconds",
        value=settings.aliyun_oss_delete_retry_max_seconds,
        type_="int",
        category="external_service",
        description="图片 OSS 删除失败重试最大间隔（秒）",
    ),
    _spec(
        "aliyun_oss_delete_max_attempts",
        value=settings.aliyun_oss_delete_max_attempts,
        type_="int",
        category="external_service",
        description="图片 OSS 删除最大尝试次数；0 表示持续重试",
    ),
    _spec(
        "search_top_k_lex",
        value=settings.search_top_k_lex,
        type_="int",
        category="search",
        description="文档检索词法召回数量",
    ),
    _spec(
        "hard_constraint_enabled",
        value=settings.hard_constraint_enabled,
        type_="bool",
        category="search",
        description="是否启用硬约束不存在判定",
    ),
    _spec(
        "hard_constraint_top_n",
        value=50,
        type_="int",
        category="search",
        description="硬约束证据校验候选数量（TopN）",
    ),
    _spec(
        "hard_constraint_long_number_min_len",
        value=5,
        type_="int",
        category="search",
        description="纯数字硬约束最小长度",
    ),
    _spec(
        "hard_constraint_specific_doc_keywords",
        value=["保险盒定义", "针脚定义", "CAN总线", "EBS", "仪表", "上装控制器"],
        type_="json",
        category="search",
        description="特定资料关键词硬约束清单（JSON 数组）",
    ),
    _spec(
        "min_valid_rrf_score",
        value=0.015,
        type_="float",
        category="search",
        description="检索结果最小有效分数阈值",
    ),
    _spec(
        "semantic_confidence_threshold",
        value=0.40,
        type_="float",
        category="search",
        description="语义存在性判定阈值",
    ),
    _spec(
        "clarify_target_results",
        value=settings.clarify_target_results,
        type_="int",
        category="clarify",
        description="澄清目标结果数",
    ),
    _spec(
        "clarify_result_threshold",
        value=settings.clarify_result_threshold,
        type_="int",
        category="clarify",
        description="触发澄清的结果数量阈值",
    ),
    _spec(
        "clarify_max_rounds",
        value=settings.clarify_max_rounds,
        type_="int",
        category="clarify",
        description="最大澄清轮数",
    ),
    _spec(
        "clarify_score_threshold",
        value=0.5,
        type_="float",
        category="clarify",
        description="Top1 分数阈值",
    ),
    _spec(
        "clarify_score_gap",
        value=0.05,
        type_="float",
        category="clarify",
        description="Top1 与 Top2 分数差阈值",
    ),
    _spec(
        "clarify_dominant_ratio",
        value=0.8,
        type_="float",
        category="clarify",
        description="主簇占比阈值",
    ),
    _spec(
        "clarify_exact_match_threshold",
        value=0.8,
        type_="float",
        category="clarify",
        description="精确匹配分数阈值",
    ),
    _spec(
        "clarify_exact_match_gap",
        value=0.15,
        type_="float",
        category="clarify",
        description="精确匹配 top1-top2 分数差阈值",
    ),
    _spec(
        "clarify_exact_match_gap_ratio",
        value=0.25,
        type_="float",
        category="clarify",
        description="比例精确匹配阈值",
    ),
    _spec(
        "clarify_max_options",
        value=5,
        type_="int",
        category="clarify",
        description="最多生成澄清选项数",
    ),
    _spec(
        "clarify_min_facet_coverage",
        value=0.3,
        type_="float",
        category="clarify",
        description="澄清候选最小维度覆盖率",
    ),
    _spec(
        "clarify_top_n_check_count",
        value=5,
        type_="int",
        category="clarify",
        description="澄清覆盖率校验的 TopN 数量",
    ),
    _spec(
        "clarify_min_top_n_coverage",
        value=0.6,
        type_="float",
        category="clarify",
        description="澄清最小 TopN 覆盖率",
    ),
    _spec(
        "clarify_option_similarity_threshold",
        value=0.93,
        type_="float",
        category="clarify",
        description="澄清选项去重相似度阈值",
    ),
    _spec(
        "case_context_enabled",
        value=settings.case_context_enabled,
        type_="bool",
        category="runtime",
        description="是否启用案件上下文工作记忆",
    ),
    _spec(
        "case_context_max_artifacts_total",
        value=settings.case_context_max_artifacts_total,
        type_="int",
        category="runtime",
        description="案件上下文最多保留的证据条数",
    ),
    _spec(
        "case_context_max_artifacts_per_type",
        value=settings.case_context_max_artifacts_per_type,
        type_="int",
        category="runtime",
        description="案件上下文每类证据最多保留条数",
    ),
    _spec(
        "case_context_max_selected_docs",
        value=settings.case_context_max_selected_docs,
        type_="int",
        category="runtime",
        description="案件上下文最多记录的已选资料数",
    ),
    _spec(
        "case_context_max_serialized_bytes",
        value=settings.case_context_max_serialized_bytes,
        type_="int",
        category="runtime",
        description="案件上下文序列化最大字节数",
    ),
    _spec(
        "case_context_prompt_max_chars",
        value=settings.case_context_prompt_max_chars,
        type_="int",
        category="runtime",
        description="注入到 prompt 的案件上下文最大字符数",
    ),
    _spec(
        "loop_guard_max_tool_calls",
        value=settings.loop_guard_max_tool_calls,
        type_="int",
        category="runtime",
        description="单轮最多工具调用次数",
    ),
    _spec(
        "loop_guard_max_external_tool_calls",
        value=settings.loop_guard_max_external_tool_calls,
        type_="int",
        category="runtime",
        description="单轮最多外部工具调用次数",
    ),
    _spec(
        "loop_guard_max_ask_user_calls",
        value=settings.loop_guard_max_ask_user_calls,
        type_="int",
        category="runtime",
        description="单轮最多 ask_user 次数",
    ),
    _spec(
        "loop_guard_max_no_gain_streak",
        value=settings.loop_guard_max_no_gain_streak,
        type_="int",
        category="runtime",
        description="最多连续无增益工具调用次数",
    ),
    _spec(
        "loop_guard_max_same_tool_repeat",
        value=settings.loop_guard_max_same_tool_repeat,
        type_="int",
        category="runtime",
        description="同一工具最多重复调用次数",
    ),
    _spec(
        "loop_guard_max_same_args_repeat",
        value=settings.loop_guard_max_same_args_repeat,
        type_="int",
        category="runtime",
        description="同一工具相同参数最多重复调用次数",
    ),
    _spec(
        "repair_knowledge_path",
        value=settings.repair_knowledge_path,
        type_="string",
        category="runtime",
        description="维修知识库文件路径",
    ),
    _spec(
        "param_query_enabled",
        value=settings.param_query_enabled,
        type_="bool",
        category="parameter_query",
        description="是否启用参数查询能力",
    ),
    _spec(
        "param_query_sync_on_startup",
        value=settings.param_query_sync_on_startup,
        type_="bool",
        category="parameter_query",
        description="启动时是否自动同步参数资料",
    ),
    _spec(
        "param_query_parser_version",
        value=settings.param_query_parser_version,
        type_="string",
        category="parameter_query",
        description="参数资料解析版本号",
    ),
    _spec(
        "param_query_top_sources",
        value=settings.param_query_top_sources,
        type_="int",
        category="parameter_query",
        description="参数查询最多保留的候选资料数",
    ),
    _spec(
        "param_query_top_rows",
        value=settings.param_query_top_rows,
        type_="int",
        category="parameter_query",
        description="参数查询最多返回的候选针脚行数",
    ),
    _spec(
        "user_auth_enabled",
        value=settings.user_auth_enabled,
        type_="bool",
        category="system",
        description="用户端鉴权开关（当前固定开启，仅保留兼容）",
    ),
)

ACTIVE_SYSTEM_CONFIG_KEYS = frozenset(item["key"] for item in ACTIVE_SYSTEM_CONFIGS)

OBSOLETE_SYSTEM_CONFIG_KEYS = frozenset(
    {
        "clarify_confidence_threshold",
        "clarify_use_entity_filter",
        "diagnosis_service_timeout",
        "qdrant_collection",
        "qdrant_host",
        "qdrant_port",
        "llm_ingest_auto_approve_threshold",
        "llm_ingest_enabled",
        "llm_ingest_hint_limit_per_facet",
        "intent_ai_confidence_threshold",
        "intent_cache_ttl",
        "intent_provider",
        "intent_rule_priority",
        "intent_switch_threshold",
        "intent_unclear_threshold",
        "intent_use_ai",
        "intent_vector_threshold",
        "ollama_intent_model",
        "openrouter_intent_model",
        "chat_llm_temperature",
        "chat_system_prompt",
        "ollama_base_url",
        "ollama_embedding_model",
        "openrouter_api_key",
        "openrouter_base_url",
        "openrouter_chat_model",
        "openrouter_ingest_model",
        "openrouter_max_tokens",
        "openrouter_temperature",
        "openrouter_timeout",
        "search_recall_top_n",
        "search_return_top_n",
        "search_score_threshold",
        "search_top_k_vec",
        "search_top_n",
        "session_max_history",
        "session_ttl_seconds",
    }
)


def _serialize_value(value: Any, config_type: str) -> str:
    if config_type == "bool":
        return "true" if bool(value) else "false"
    if config_type == "json":
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def reconcile_system_configs(db: Session) -> dict[str, Any]:
    """Synchronize DB-backed configs with the active runtime catalog."""

    existing = {
        item.config_key: item
        for item in db.query(SystemConfig).all()
    }
    created: list[str] = []
    deleted: list[str] = []
    updated_meta: list[str] = []
    updated_values: list[str] = []

    for key in sorted(OBSOLETE_SYSTEM_CONFIG_KEYS):
        config = existing.pop(key, None)
        if config is None:
            continue
        db.delete(config)
        deleted.append(key)

    for item in ACTIVE_SYSTEM_CONFIGS:
        config = existing.get(item["key"])
        if config is None:
            db.add(
                SystemConfig(
                    config_key=item["key"],
                    config_value=_serialize_value(item["value"], item["type"]),
                    config_type=item["type"],
                    category=item["category"],
                    description=item["description"],
                    is_sensitive=bool(item.get("is_sensitive", False)),
                    updated_by="system",
                )
            )
            created.append(item["key"])
            continue

        changed = False
        if config.config_type != item["type"]:
            config.config_type = item["type"]
            changed = True
        if config.category != item["category"]:
            config.category = item["category"]
            changed = True
        if (config.description or "") != item["description"]:
            config.description = item["description"]
            changed = True
        if bool(config.is_sensitive) != bool(item.get("is_sensitive", False)):
            config.is_sensitive = bool(item.get("is_sensitive", False))
            changed = True
        if item["key"] in {"agent_model", "openrouter_clarify_model", "intent_router_model"}:
            normalized_value = str(normalize_configured_model(config.config_value))
            if (config.config_value or "") != normalized_value:
                config.config_value = normalized_value
                changed = True
                updated_values.append(item["key"])
        if changed:
            config.updated_by = "system"
            updated_meta.append(item["key"])

    if created or deleted or updated_meta or updated_values:
        db.commit()

    return {
        "created": created,
        "deleted": deleted,
        "updated_meta": updated_meta,
        "updated_values": updated_values,
        "created_count": len(created),
        "deleted_count": len(deleted),
        "updated_meta_count": len(updated_meta),
        "updated_value_count": len(updated_values),
    }


def init_default_configs(db: Session) -> int:
    """Backward-compatible wrapper returning only the create count."""

    return int(reconcile_system_configs(db)["created_count"])

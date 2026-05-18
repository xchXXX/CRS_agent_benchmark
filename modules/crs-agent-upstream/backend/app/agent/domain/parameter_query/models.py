"""Typed models for the parameter-query domain."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


FIELD_LABELS: dict[str, str] = {
    "pin_definition": "针脚定义",
    "ecu_pin_no": "ECU针脚编号",
    "connector_pin_no": "接插件针脚号",
    "voltage": "电压",
    "open_voltage": "开路电压",
    "static_voltage": "静态电压",
    "idle_voltage": "低怠速电压",
    "remark": "备注",
}


FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "pin_definition": ("作用", "定义", "功能", "什么意思", "什么作用", "针脚定义", "定义是什么"),
    "ecu_pin_no": ("哪个针脚", "几号脚", "脚位", "针脚编号", "ecu针脚", "ecu针脚编号"),
    "connector_pin_no": ("接插件针脚", "接插件脚号", "插头针脚", "插头脚位", "插件脚位"),
    "open_voltage": ("开路电压", "断开电压", "空载电压"),
    "static_voltage": ("静态电压", "连接线束后静态电压", "上电电压"),
    "idle_voltage": ("低怠速电压", "怠速电压"),
    "voltage": ("电压", "多少伏", "几伏", "电压多少"),
    "remark": ("备注", "注意事项"),
}


@dataclass(frozen=True)
class ExternalKnowledgeSource:
    source_knowledge_id: int
    source_type_id: int
    source_type_code: str
    title: str
    content_format: str
    content_summary: str | None
    source_status: bool
    source_is_deleted: bool
    latest_version: int
    published_version: int | None
    update_time: datetime


@dataclass(frozen=True)
class ParsedPinRow:
    row_no: int
    component_name: str | None
    ecu_pin_no: str | None
    pin_definition: str | None
    connector_pin_no: str | None
    open_voltage_text: str | None
    open_voltage_min: Decimal | None
    open_voltage_max: Decimal | None
    static_voltage_text: str | None
    static_voltage_min: Decimal | None
    static_voltage_max: Decimal | None
    idle_voltage_text: str | None
    idle_voltage_min: Decimal | None
    idle_voltage_max: Decimal | None
    remark: str | None
    raw_row: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParameterIndexSource:
    source_knowledge_id: int
    title: str
    title_normalized: str
    ecu_name: str | None
    ecu_name_normalized: str | None
    system_voltage: int | None
    pin_doc_kind: str
    parsed_row_count: int
    raw_content: str | None
    last_synced_at: datetime | None


@dataclass(frozen=True)
class ParameterIndexRow:
    id: int
    source_knowledge_id: int
    source_title: str
    ecu_name: str | None
    ecu_name_normalized: str | None
    system_voltage: int | None
    row_no: int
    component_name: str | None
    component_name_normalized: str | None
    ecu_pin_no: str | None
    ecu_pin_no_normalized: str | None
    pin_definition: str | None
    pin_definition_normalized: str | None
    connector_pin_no: str | None
    open_voltage_text: str | None
    static_voltage_text: str | None
    idle_voltage_text: str | None
    remark: str | None
    raw_row_json: dict[str, Any] | None
    search_text: str | None


@dataclass(frozen=True)
class AliasEntry:
    entity_type: str
    canonical_value: str
    canonical_value_normalized: str
    alias_value: str
    alias_value_normalized: str
    priority: int
    source_scope: str
    source_knowledge_id: int | None = None


@dataclass(frozen=True)
class ParameterQueryInterpretation:
    raw_query: str
    normalized_query: str
    explicit_pin: str | None
    requested_field: str | None
    system_voltage: int | None
    selected_source_id: int | None
    selected_row_id: int | None
    ecu_candidates: tuple[str, ...]
    component_hint: str | None
    definition_hint: str | None
    free_text_answer: str | None = None

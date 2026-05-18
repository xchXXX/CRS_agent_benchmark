"""Markdown pin-table parser for parameter-query knowledge."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.agent.domain.parameter_query.models import ParsedPinRow
from app.agent.domain.parameter_query.normalizer import first_number_pair, normalize_text


HEADER_ALIASES = {
    "component_name": {"零部件", "部件", "元件"},
    "ecu_pin_no": {"ecu针脚编号", "ecu针脚", "针脚编号", "ecu脚位"},
    "pin_definition": {"针脚定义", "定义", "功能定义"},
    "connector_pin_no": {"接插件针脚号", "插头针脚号", "接插件脚号", "插件针脚号"},
    "open_voltage_text": {"开路电压v", "开路电压", "开路电压(v)"},
    "static_voltage_text": {"连接线束后静态电压", "静态电压", "连接线束后静态电压v"},
    "idle_voltage_text": {"低怠速电压", "怠速电压", "低速怠速电压"},
    "remark": {"备注", "说明"},
}


def _normalize_header(value: str) -> str:
    return normalize_text(value).replace("（v）", "v").replace("(v)", "v")


def _map_header(value: str) -> str | None:
    normalized = _normalize_header(value)
    for field, aliases in HEADER_ALIASES.items():
        if normalized in {_normalize_header(alias) for alias in aliases}:
            return field
    return None


def _split_markdown_row(line: str) -> list[str]:
    stripped = line.strip().strip("|")
    return [cell.strip() for cell in stripped.split("|")]


def _is_separator_row(line: str) -> bool:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return False
    for cell in _split_markdown_row(stripped):
        if set(cell.replace(":", "").strip()) - {"-"}:
            return False
    return True


def _to_decimal_pair(value: str | None) -> tuple[Decimal | None, Decimal | None]:
    minimum, maximum = first_number_pair(value)
    if minimum is None or maximum is None:
        return None, None
    return Decimal(str(minimum)), Decimal(str(maximum))


def parse_markdown_pin_rows(markdown: str) -> list[ParsedPinRow]:
    rows: list[ParsedPinRow] = []
    if not markdown:
        return rows

    lines = [line.rstrip() for line in markdown.splitlines()]
    header_cells: list[str] | None = None
    header_map: list[str | None] = []
    in_table = False

    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            if in_table and rows:
                break
            continue

        if header_cells is None:
            header_cells = _split_markdown_row(stripped)
            header_map = [_map_header(cell) for cell in header_cells]
            continue

        if _is_separator_row(stripped):
            in_table = True
            continue

        if not in_table:
            header_cells = _split_markdown_row(stripped)
            header_map = [_map_header(cell) for cell in header_cells]
            continue

        values = _split_markdown_row(stripped)
        if not any(value.strip() for value in values):
            continue

        raw_row: dict[str, Any] = {}
        mapped: dict[str, str | None] = {}
        for index, header in enumerate(header_cells):
            value = values[index].strip() if index < len(values) else ""
            raw_row[header] = value
            mapped_field = header_map[index] if index < len(header_map) else None
            if mapped_field is not None:
                mapped[mapped_field] = value or None

        if not mapped.get("component_name") and not mapped.get("ecu_pin_no"):
            continue

        open_min, open_max = _to_decimal_pair(mapped.get("open_voltage_text"))
        static_min, static_max = _to_decimal_pair(mapped.get("static_voltage_text"))
        idle_min, idle_max = _to_decimal_pair(mapped.get("idle_voltage_text"))
        rows.append(
            ParsedPinRow(
                row_no=len(rows) + 1,
                component_name=mapped.get("component_name"),
                ecu_pin_no=mapped.get("ecu_pin_no"),
                pin_definition=mapped.get("pin_definition"),
                connector_pin_no=mapped.get("connector_pin_no"),
                open_voltage_text=mapped.get("open_voltage_text"),
                open_voltage_min=open_min,
                open_voltage_max=open_max,
                static_voltage_text=mapped.get("static_voltage_text"),
                static_voltage_min=static_min,
                static_voltage_max=static_max,
                idle_voltage_text=mapped.get("idle_voltage_text"),
                idle_voltage_min=idle_min,
                idle_voltage_max=idle_max,
                remark=mapped.get("remark"),
                raw_row=raw_row,
            )
        )

    return rows


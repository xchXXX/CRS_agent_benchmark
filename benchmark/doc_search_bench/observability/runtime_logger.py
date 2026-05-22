from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any


def _timestamp_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _normalize_scalar(value: Any, *, limit: int | None = None) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        text = "是" if value else "否"
    elif isinstance(value, Path):
        text = str(value)
    elif isinstance(value, (list, tuple, set)):
        parts = [_normalize_scalar(item) for item in value]
        text = "、".join(part for part in parts if part)
    else:
        text = str(value)
    text = " ".join(text.split())
    if limit is not None and len(text) > limit:
        return f"{text[: max(0, limit - 3)]}..."
    return text


def _pair_items(value: Any) -> list[tuple[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [(str(key), raw_value) for key, raw_value in value.items()]
    if isinstance(value, (list, tuple)):
        items: list[tuple[str, Any]] = []
        for item in value:
            if isinstance(item, tuple) and len(item) == 2:
                items.append((str(item[0]), item[1]))
        return items
    return []


def _format_pairs(value: Any, *, value_limit: int = 96) -> str:
    parts: list[str] = []
    for key, raw_value in _pair_items(value):
        normalized = _normalize_scalar(raw_value, limit=value_limit)
        if not normalized:
            continue
        parts.append(f"{key}={normalized}")
    return "；".join(parts)


def _format_detail(value: Any, *, value_limit: int = 220) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        text = _format_pairs(value, value_limit=value_limit)
        return text or None
    if isinstance(value, (list, tuple)):
        tuple_items = _pair_items(value)
        if tuple_items:
            text = _format_pairs(tuple_items, value_limit=value_limit)
            return text or None
        parts = [_normalize_scalar(item, limit=value_limit) for item in value]
        text = "；".join(part for part in parts if part)
        return text or None
    text = _normalize_scalar(value, limit=value_limit)
    return text or None


class BenchmarkRuntimeLogger:
    def __init__(self, *, benchmark_root: Path, run_id: str) -> None:
        self.run_id = run_id
        self.run_dir = benchmark_root / "reports" / "runs" / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / "runtime.log"
        self._lock = Lock()
        self._closed = False
        self._handle = self.path.open("w", encoding="utf-8", newline="\n")

    def emit(
        self,
        event: str,
        *,
        level: str = "信息",
        context: Any = None,
        result: Any = None,
        summary: str | None = None,
        detail: Any = None,
        path: Any = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        detail_text = _format_detail(detail)
        path_text = _format_detail(path, value_limit=260)
        context_text = _format_pairs(context) or "-"
        result_text = _format_pairs(result) or "-"
        summary_text = _normalize_scalar(summary or self._auto_summary(event, payload or {}), limit=180) or "-"
        lines = [
            f"{_timestamp_text()} | {level} | {event} | {context_text} | {result_text} | {summary_text}\n"
        ]
        if detail_text:
            lines.append(f"  详情: {detail_text}\n")
        if path_text:
            lines.append(f"  路径: {path_text}\n")
        with self._lock:
            if self._closed:
                return
            self._handle.writelines(lines)
            self._handle.flush()

    def finalize(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._handle.flush()
            self._handle.close()
            self._closed = True

    @staticmethod
    def _auto_summary(event: str, payload: dict[str, Any]) -> str:
        request_kind = str(payload.get("request_kind") or "").strip()
        response_type = str(payload.get("response_type") or "").strip()
        decision_kind = str(payload.get("decision_kind") or "").strip()
        stop_reason = str(payload.get("stop_reason") or "").strip()
        if event == "运行开始":
            return "benchmark 运行开始"
        if event == "套件开始":
            suite_id = str(payload.get("suite_id") or "").strip()
            return f"开始处理套件 {suite_id}" if suite_id else "开始处理套件"
        if event == "用例开始":
            case_id = str(payload.get("case_id") or "").strip()
            return f"开始处理用例 {case_id}" if case_id else "开始处理用例"
        if event == "尝试开始":
            attempt_index = payload.get("attempt_index")
            return f"开始第 {attempt_index} 次尝试" if attempt_index is not None else "开始新的尝试"
        if event == "请求预处理":
            return "开始准备请求上下文"
        if event == "预处理完成":
            return "请求上下文准备完成"
        if event == "预处理阻断":
            return "请求上下文缺失，当前尝试被阻断"
        if event == "发送请求":
            if request_kind == "initial_message":
                return "开始发送首轮对话请求"
            if request_kind == "ask_user_resume":
                return "开始发送澄清恢复请求"
            if request_kind == "search_api":
                return "开始发送检索请求"
            return "开始发送请求"
        if event == "收到响应":
            if response_type == "ask_user":
                return "收到澄清问题，等待用户模拟决策"
            if response_type == "documents":
                return "收到文档结果，当前轮进入终态"
            if response_type == "message":
                return "收到普通消息，当前轮进入终态"
            if response_type == "error" or payload.get("error_message"):
                return "服务返回错误响应"
            return "收到服务响应"
        if event == "识别澄清问题":
            return "进入 ask_user 消费阶段"
        if event == "开始用户模拟决策":
            return "开始生成用户模拟决策"
        if event == "用户模拟模型调用":
            attempt_index = payload.get("internal_attempt")
            attempt_limit = payload.get("attempt_limit")
            if attempt_index is not None and attempt_limit is not None:
                return f"开始第 {attempt_index}/{attempt_limit} 次用户模拟模型调用"
            return "开始用户模拟模型调用"
        if event == "用户模拟输出非法":
            return "用户模拟输出不是合法 JSON"
        if event == "用户模拟校验失败":
            return "用户模拟输出未通过约束校验"
        if event == "用户模拟符号决策":
            return "用户模拟直接依据候选项规则生成决策"
        if event == "完成用户模拟决策":
            if decision_kind == "choose_option":
                return "用户模拟选择了候选项"
            if decision_kind == "declare_rollback_intent":
                return "用户模拟表达了撤回意图"
            return "用户模拟决策生成完成"
        if event == "用户模拟决策失败":
            return "用户模拟决策生成失败"
        if event == "用户选择已提交":
            return "已提交用户模拟选择并准备恢复会话"
        if event == "发现能力缺口":
            return "检测到能力缺口"
        if event == "尝试停止":
            stop_reason_map = {
                "missing_session_id": "因缺少 session_id 停止",
                "missing_tool_call_id": "因缺少 tool_call_id 停止",
                "missing_selection_payload": "因缺少 selection_payload 停止",
                "max_turns_exceeded": "超过最大轮次仍未收口",
                "rollback_unsupported": "因撤回能力缺口停止",
                "invalid_user_decision": "因用户模拟决策无效停止",
                "error": "因异常响应停止",
                "preprocess_blocked": "因预处理阻断停止",
            }
            return stop_reason_map.get(stop_reason, "当前尝试被提前停止")
        if event == "合同判定完成":
            return "合同判定完成"
        if event == "文件判定完成":
            return "文件命中通过" if payload.get("recall_hit") else "文件命中失败"
        if event == "页码判定完成":
            page_hit = payload.get("page_hit_at_k")
            if page_hit is True:
                return "页码命中通过"
            if page_hit is False:
                return "页码命中失败"
            return "页码判定完成"
        if event == "定位判定完成":
            locator_hit = payload.get("locator_hit_at_k")
            if locator_hit is True:
                return "定位命中通过"
            if locator_hit is False:
                return "定位命中失败"
            return "定位判定完成"
        if event == "轨迹分析完成":
            return "轨迹分析确认最终命中" if payload.get("final_hit") else "轨迹分析确认最终未命中"
        if event == "尝试完成":
            return "当前尝试执行完成"
        if event == "报告写入完成":
            return "实际报告、评分报告与日志索引已写入"
        if event == "运行完成":
            return "benchmark 运行完成，official gate 通过" if payload.get("pass") else "benchmark 运行完成，official gate 未通过"
        if event == "运行异常":
            return "benchmark 运行异常退出"
        return event

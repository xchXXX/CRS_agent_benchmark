from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
from typing import Any


SYSTEM_SENDERS = {
    "GongGui02",
    "GongGuiZhiJiaJiShu-QiuLingTong",
    "ZhuYang_1",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成按 case 隔离切换的审阅 HTML")
    parser.add_argument(
        "--fixture",
        default="benchmark/doc_search_bench/envs/doc_search/data/train/real_world_wecom_train.fixture.json",
    )
    parser.add_argument(
        "--gold",
        default="benchmark/doc_search_bench/envs/doc_search/data/train/real_world_wecom_train.gold.json",
    )
    parser.add_argument(
        "--actual-report",
        default=None,
        help="可选，report.actual.json 路径；提供后优先使用运行结果渲染",
    )
    parser.add_argument(
        "--score-report",
        default=None,
        help="可选，report.score.json 路径；用于补充页级/坐标级摘要",
    )
    parser.add_argument(
        "--output",
        default="benchmark/reports/chat_exports/wecom-chat-export-20260512T093829/real_world_wecom_train_case_review.html",
    )
    return parser.parse_args()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sender_role(chat_from: str) -> str:
    if chat_from in SYSTEM_SENDERS or chat_from.startswith("GongGui") or chat_from == "HuangXianHua":
        return "service"
    return "member"


def parse_content_payload(raw_content: Any) -> dict[str, Any] | None:
    if not isinstance(raw_content, str) or not raw_content.strip():
        return None
    try:
        value = json.loads(raw_content)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def display_message_text(message: dict[str, Any]) -> str:
    msgtype = str(message.get("msgtype") or "")
    payload = parse_content_payload(message.get("content"))
    path_url = str(message.get("path_url") or "").strip()

    if msgtype == "text":
        if payload and isinstance(payload.get("content"), str):
            return payload["content"]
        return str(message.get("text") or message.get("content") or "")
    if msgtype == "revoke":
        return "[撤回消息]"
    if msgtype == "image":
        return "[图片]"
    if msgtype == "video":
        return "[视频]"
    if msgtype == "voice":
        return "[语音]"
    if msgtype == "emotion":
        return "[表情]"
    if msgtype == "file":
        if payload:
            filename = str(payload.get("filename") or "").strip()
            fileext = str(payload.get("fileext") or "").strip()
            parts = [part for part in [filename, fileext] if part]
            return "文件: " + " | ".join(parts) if parts else "[文件]"
        text = str(message.get("text") or "").strip()
        return text or "[文件]"
    if msgtype in {"mixed", "markdown", "chatrecord", "image_text"}:
        text = str(message.get("text") or "").strip()
        if text:
            return text
        if payload and isinstance(payload.get("content"), str):
            return payload["content"]
        if msgtype == "chatrecord":
            return "[聊天记录]"
        return f"[{msgtype}]"
    if path_url:
        return f"[{msgtype}]"
    return str(message.get("text") or message.get("content") or f"[{msgtype}]")


def render_media(path_or_url: str, msgtype: str, base_dir: Path) -> str:
    link = path_or_url.strip()
    if not link:
        return ""
    href = link
    if not link.startswith(("http://", "https://")):
        target = (repo_root() / link).resolve()
        href = os.path.relpath(target, start=base_dir.resolve()).replace("\\", "/")
    escaped_href = html.escape(href, quote=True)
    if msgtype in {"image", "image_text"}:
        return (
            f'<div class="media-block"><a href="{escaped_href}" target="_blank" rel="noreferrer">'
            f'<img src="{escaped_href}" alt="image" loading="lazy"></a></div>'
        )
    return (
        f'<div class="media-block"><a href="{escaped_href}" target="_blank" rel="noreferrer">'
        "打开资源</a></div>"
    )


def render_message_card(message: dict[str, Any], base_dir: Path) -> str:
    sender = str(message.get("chat_from") or "unknown")
    role = sender_role(sender)
    msgtype = str(message.get("msgtype") or "")
    text = html.escape(display_message_text(message)).replace("\n", "<br>")
    msg_date = html.escape(str(message.get("msg_date") or ""))
    path_url = str(message.get("path_url") or "").strip()
    media_parts: list[str] = []

    if path_url:
        if "," in path_url and msgtype in {"mixed", "chatrecord"}:
            for item in [part.strip() for part in path_url.split(",") if part.strip()]:
                media_parts.append(render_media(item, "image", base_dir))
        else:
            media_parts.append(render_media(path_url, msgtype, base_dir))

    media_html = "\n".join(media_parts)
    return f"""
    <div class="message-row role-{role}">
      <div class="avatar">{html.escape(sender[:1] or "?")}</div>
      <div class="bubble-wrap">
        <div class="sender">{html.escape(sender)}</div>
        <div class="bubble bubble-{role}">
          <div class="meta">
            <span class="msgtype">{html.escape(msgtype)}</span>
            <span class="msgdate">{msg_date}</span>
            <span class="msgid">#{message.get("id")}</span>
          </div>
          <div class="text">{text or "&nbsp;"}</div>
          {media_html}
        </div>
      </div>
    </div>
    """


def normalize_string_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    deduped: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped


def normalize_int_list(values: Any) -> list[int]:
    if not isinstance(values, list):
        return []
    normalized: list[int] = []
    seen: set[int] = set()
    for item in values:
        if isinstance(item, bool):
            continue
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def normalize_page_ranges(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    ranges: list[str] = []
    for item in values:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            try:
                start = int(item[0])
                end = int(item[1])
            except (TypeError, ValueError):
                continue
            ranges.append(f"{start}-{end}")
    return ranges


def format_bool(value: Any) -> str:
    if value is True:
        return "是"
    if value is False:
        return "否"
    return "未记录"


def format_inline_list(values: list[Any], *, empty: str = "未记录") -> str:
    rendered = [str(item) for item in values if str(item).strip()]
    if not rendered:
        return empty
    return " / ".join(rendered)


def resolve_target_titles(case: dict[str, Any], gold_case: dict[str, Any] | None) -> list[str]:
    task_metadata = case.get("task_metadata") or {}
    titles = normalize_string_list(task_metadata.get("target_doc_titles"))
    if titles:
        return titles
    titles = normalize_string_list(task_metadata.get("accepted_titles"))
    if titles:
        return titles
    if isinstance(gold_case, dict):
        target_docs = gold_case.get("target_docs") if isinstance(gold_case.get("target_docs"), list) else []
        titles = []
        for item in target_docs:
            if isinstance(item, dict):
                title = str(item.get("title") or "").strip()
                if title:
                    titles.append(title)
        if titles:
            return normalize_string_list(titles)
        return normalize_string_list(gold_case.get("accepted_titles"))
    return []


def resolve_target_documents(case: dict[str, Any], gold_case: dict[str, Any] | None) -> list[dict[str, Any]]:
    titles = resolve_target_titles(case, gold_case)
    target_docs = []
    for title in titles:
        target_docs.append({"title": title})
    return target_docs


def render_question_images(source_case: dict[str, Any], base_dir: Path) -> str:
    images = source_case.get("question_images") or []
    if not images:
        return ""
    blocks = [render_media(path, "image", base_dir) for path in images]
    return f"""
    <section class="card">
      <div class="section-title">题图</div>
      {''.join(blocks)}
    </section>
    """


def render_fact_list(title: str, values: Any) -> str:
    if not values:
        return ""
    items = "".join(f"<li>{html.escape(str(x))}</li>" for x in values if str(x or "").strip())
    if not items:
        return ""
    return f'<div class="fact-block"><div class="fact-title">{html.escape(title)}</div><ul>{items}</ul></div>'


def render_key_value_rows(rows: list[tuple[str, str]]) -> str:
    items = []
    for label, value in rows:
        items.append(
            f"""
            <div class="kv-item">
              <div class="kv-label">{html.escape(label)}</div>
              <div class="kv-value">{html.escape(value)}</div>
            </div>
            """
        )
    return "".join(items)


def render_standard_answer(case: dict[str, Any], gold_case: dict[str, Any] | None) -> str:
    task_metadata = case.get("task_metadata") or {}
    target_titles = resolve_target_titles(case, gold_case)
    accepted_pages = normalize_int_list(task_metadata.get("accepted_pages"))
    accepted_page_ranges = normalize_page_ranges(task_metadata.get("accepted_page_ranges"))
    accepted_region_groups = task_metadata.get("accepted_region_groups") if isinstance(
        task_metadata.get("accepted_region_groups"), list
    ) else []

    region_values: list[str] = []
    for group in accepted_region_groups:
        if not isinstance(group, dict):
            continue
        page_number = group.get("page_number")
        label = str(group.get("label") or group.get("group_id") or "未命名区域").strip()
        boxes = group.get("boxes_norm") if isinstance(group.get("boxes_norm"), list) else []
        if isinstance(page_number, int):
            region_values.append(f"第 {page_number} 页 · {label} · {format_boxes(boxes)}")
        else:
            region_values.append(f"{label} · {format_boxes(boxes)}")

    rows = [
        ("标准答案文档", format_inline_list(target_titles)),
        ("标准答案页码", format_inline_list([f"第 {page} 页" for page in accepted_pages] + accepted_page_ranges)),
        ("标准答案区域", format_inline_list(region_values)),
    ]
    return f"""
    <section class="card">
      <div class="section-title">标准答案</div>
      <div class="kv-grid">
        {render_key_value_rows(rows)}
      </div>
    </section>
    """


def render_document_return(case: dict[str, Any]) -> str:
    prediction = case.get("prediction") or {}
    docs = prediction.get("top_k_documents") if isinstance(prediction.get("top_k_documents"), list) else []
    if not docs:
        body = '<div class="empty">未返回文档结果</div>'
    else:
        rows = []
        for item in docs:
            if not isinstance(item, dict):
                continue
            title = str(item.get("doc_title") or "未命名文档").strip()
            path = str(item.get("doc_path") or "").strip()
            score = item.get("score")
            page_numbers = normalize_int_list(item.get("page_numbers"))
            summary_parts = []
            if path:
                summary_parts.append(path)
            if score is not None:
                summary_parts.append(f"分数 {score}")
            if page_numbers:
                summary_parts.append("页码 " + format_inline_list([f"第 {page} 页" for page in page_numbers]))
            rows.append(
                f"""
                <div class="doc-item">
                  <div class="doc-title">{html.escape(title)}</div>
                  <div class="doc-meta">{html.escape(' | '.join(summary_parts) if summary_parts else '未记录更多信息')}</div>
                </div>
                """
            )
        body = "".join(rows) or '<div class="empty">未返回文档结果</div>'
    return f"""
    <section class="card">
      <div class="section-title">实际返回</div>
      <div class="doc-list">{body}</div>
    </section>
    """


def render_page_section(case: dict[str, Any], score_case: dict[str, Any] | None) -> str:
    task_metadata = case.get("task_metadata") or {}
    prediction = case.get("prediction") or {}
    metrics = case.get("metrics") or {}
    score_metrics = (score_case or {}).get("metrics") or {}

    accepted_pages = normalize_int_list(task_metadata.get("accepted_pages"))
    accepted_page_ranges = normalize_page_ranges(task_metadata.get("accepted_page_ranges"))
    predicted_pages = normalize_int_list(prediction.get("predicted_pages"))
    failure_reason = first_non_empty(
        score_metrics.get("document_level_failure"),
        score_metrics.get("locator_document_level_failure"),
        None,
    )

    rows = [
        ("标准答案页码", format_inline_list([f"第 {page} 页" for page in accepted_pages] + accepted_page_ranges)),
        ("实际返回页码", format_inline_list([f"第 {page} 页" for page in predicted_pages])),
        ("页码是否命中", format_bool(metrics.get("page_hit_at_k"))),
        ("失败原因", failure_reason or "无"),
    ]
    return f"""
    <section class="card">
      <div class="section-title">页级结果</div>
      <div class="kv-grid">
        {render_key_value_rows(rows)}
      </div>
    </section>
    """


def format_boxes(boxes: Any) -> str:
    if not isinstance(boxes, list) or not boxes:
        return "未记录"
    rendered: list[str] = []
    for item in boxes:
        if not isinstance(item, (list, tuple)) or len(item) != 4:
            continue
        rendered.append(f"({item[0]}, {item[1]}, {item[2]}, {item[3]})")
    return " / ".join(rendered) if rendered else "未记录"


def render_coord_section(case: dict[str, Any]) -> str:
    task_metadata = case.get("task_metadata") or {}
    prediction = case.get("prediction") or {}
    metrics = case.get("metrics") or {}

    accepted_region_groups = task_metadata.get("accepted_region_groups") if isinstance(
        task_metadata.get("accepted_region_groups"), list
    ) else []
    standard_regions: list[str] = []
    for group in accepted_region_groups:
        if not isinstance(group, dict):
            continue
        page_number = group.get("page_number")
        label = str(group.get("label") or group.get("group_id") or "未命名区域").strip()
        boxes = group.get("boxes_norm") if isinstance(group.get("boxes_norm"), list) else []
        if isinstance(page_number, int):
            standard_regions.append(f"第 {page_number} 页 · {label} · {format_boxes(boxes)}")
        else:
            standard_regions.append(f"{label} · {format_boxes(boxes)}")

    predicted_pages = normalize_int_list(prediction.get("coord_predicted_page_numbers"))
    boxes_norm = prediction.get("coord_predicted_boxes_norm") if isinstance(
        prediction.get("coord_predicted_boxes_norm"), list
    ) else []
    predicted_boxes: list[str] = []
    for item in boxes_norm:
        if not isinstance(item, dict):
            continue
        page_number = item.get("page_number")
        box_text = format_boxes(item.get("boxes"))
        if isinstance(page_number, int):
            predicted_boxes.append(f"第 {page_number} 页 · {box_text}")
        else:
            predicted_boxes.append(box_text)

    rows = [
        ("标准答案区域", format_inline_list(standard_regions)),
        ("实际返回页码", format_inline_list([f"第 {page} 页" for page in predicted_pages])),
        ("实际返回坐标框", format_inline_list(predicted_boxes)),
        ("坐标是否命中", format_bool(metrics.get("coord_hit"))),
        ("失败原因", str(metrics.get("coord_failure_reason") or "无")),
    ]
    return f"""
    <section class="card">
      <div class="section-title">坐标级结果</div>
      <div class="kv-grid">
        {render_key_value_rows(rows)}
      </div>
    </section>
    """


def render_raw_section(case: dict[str, Any]) -> str:
    raw_json = html.escape(json.dumps(case, ensure_ascii=False, indent=2))
    return f"""
    <details class="card raw-card">
      <summary class="section-title raw-summary">原始返回</summary>
      <pre class="raw-json">{raw_json}</pre>
    </details>
    """


def first_non_empty(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def render_case_section(
    case: dict[str, Any],
    source_case: dict[str, Any],
    gold_case: dict[str, Any] | None,
    score_case: dict[str, Any] | None,
    base_dir: Path,
    active: bool,
) -> str:
    case_id = str(case.get("case_id") or source_case.get("case_id") or "")
    metadata = source_case.get("metadata") or {}
    transcript = metadata.get("transcript") or []
    cards = "\n".join(render_message_card(message, base_dir) for message in transcript)
    day_labels: list[str] = []
    for message in transcript:
        msg_date = str(message.get("msg_date") or "")
        day = msg_date[:10] if msg_date else "未知日期"
        if not day_labels or day_labels[-1] != day:
            day_labels.append(day)
    facts_html = "".join(
        [
            render_fact_list("已知信息", (source_case.get("user_profile") or {}).get("known_items")),
            render_fact_list("不确定信息", (source_case.get("user_profile") or {}).get("uncertain_items")),
        ]
    )
    room_id = html.escape(str(metadata.get("room_id") or ""))
    opening_id = html.escape(str(metadata.get("opening_message_id") or ""))
    answer_id = html.escape(str(metadata.get("answer_message_id") or ""))
    section_class = "case-panel active" if active else "case-panel"
    question_text = str(source_case.get("question_text") or case.get("input", {}).get("question_text") or "").strip()
    input_modality = str(source_case.get("input_modality") or case.get("input_modality") or "").strip()
    user_goal = str((source_case.get("user_profile") or {}).get("goal") or "").strip()
    target_titles = resolve_target_titles(case, gold_case)
    accepted_title = target_titles[0] if target_titles else "未记录目标文档"

    return f"""
    <section id="{html.escape(case_id)}" class="{section_class}" data-case-id="{html.escape(case_id)}">
      <div class="case-head">
        <div>
          <div class="case-title">{html.escape(case_id)}</div>
          <div class="case-subtitle">{html.escape(question_text)}</div>
        </div>
        <div class="badge">{html.escape(input_modality or "未知输入类型")}</div>
      </div>

      <div class="summary-grid">
        <div class="summary-card">
          <div class="summary-label">首轮问题</div>
          <div class="summary-value">{html.escape(str(source_case.get("initial_user_message") or question_text))}</div>
        </div>
        <div class="summary-card">
          <div class="summary-label">目标文档</div>
          <div class="summary-value">{html.escape(accepted_title)}</div>
        </div>
        <div class="summary-card">
          <div class="summary-label">聊天溯源</div>
          <div class="summary-value">房间编号={room_id or "未记录"}<br>开场消息={opening_id or "未记录"}<br>答案消息={answer_id or "未记录"}</div>
        </div>
        <div class="summary-card">
          <div class="summary-label">用户目标</div>
          <div class="summary-value">{html.escape(user_goal or "未记录")}</div>
        </div>
      </div>

      <div class="fact-grid">
        {facts_html}
      </div>

      {render_question_images(source_case, base_dir)}
      {render_standard_answer(case, gold_case)}
      {render_document_return(case)}
      {render_page_section(case, score_case)}
      {render_coord_section(case)}

      <section class="card">
        <div class="section-title">聊天轨迹</div>
        <div class="timeline-shell">
          {' '.join(f'<div class="day-chip">{html.escape(day)}</div>' for day in day_labels)}
        </div>
        <div class="chat-shell">
          {cards or '<div class="empty">未记录聊天轨迹</div>'}
        </div>
      </section>

      {render_raw_section(case)}
    </section>
    """


def build_source_case_lookup(
    fixture_cases: list[dict[str, Any]],
    actual_cases: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for item in fixture_cases:
        case_id = str(item.get("case_id") or "").strip()
        if case_id:
            lookup[case_id] = item
    for item in actual_cases:
        case_id = str(item.get("case_id") or "").strip()
        if not case_id or case_id in lookup:
            continue
        source_case = {
            "case_id": case_id,
            "question_text": ((item.get("input") or {}).get("question_text") or ""),
            "question_images": ((item.get("input") or {}).get("question_images") or []),
            "input_modality": item.get("input_modality"),
            "initial_user_message": ((item.get("input") or {}).get("question_text") or ""),
            "metadata": {},
            "user_profile": {
                "goal": ((item.get("task_metadata") or {}).get("user_profile_goal") or ""),
                "known_items": ((item.get("task_metadata") or {}).get("user_profile_known_items") or []),
                "uncertain_items": ((item.get("task_metadata") or {}).get("user_profile_uncertain_items") or []),
            },
        }
        lookup[case_id] = source_case
    return lookup


def build_score_case_lookup(score_cases: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    if not score_cases:
        return lookup
    for item in score_cases:
        case_id = str(item.get("case_id") or "").strip()
        if case_id:
            lookup[case_id] = item
    return lookup


def build_html(
    cases: list[dict[str, Any]],
    gold_map: dict[str, dict[str, Any]],
    output_path: Path,
    score_cases: list[dict[str, Any]] | None = None,
    source_case_lookup: dict[str, dict[str, Any]] | None = None,
) -> str:
    base_dir = output_path.parent
    score_lookup = build_score_case_lookup(score_cases)
    source_lookup = source_case_lookup or {}
    nav_items = []
    sections = []
    for idx, case in enumerate(cases):
        case_id = str(case.get("case_id") or "")
        gold_case = gold_map.get(case_id)
        source_case = source_lookup.get(case_id, {"case_id": case_id})
        score_case = score_lookup.get(case_id)
        target_titles = resolve_target_titles(case, gold_case)
        accepted_title = target_titles[0] if target_titles else "未记录目标文档"
        nav_items.append(
            f"""
            <button class="case-nav-item{' active' if idx == 0 else ''}" data-target="{html.escape(case_id)}">
              <span class="nav-id">{html.escape(case_id)}</span>
              <span class="nav-title">{html.escape(accepted_title)}</span>
            </button>
            """
        )
        sections.append(
            render_case_section(
                case=case,
                source_case=source_case,
                gold_case=gold_case,
                score_case=score_case,
                base_dir=base_dir,
                active=idx == 0,
            )
        )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>用例审阅页</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #edf2f7;
      --panel: #ffffff;
      --text: #1f2937;
      --muted: #6b7280;
      --border: #d4dbe4;
      --shadow: 0 10px 30px rgba(15, 23, 42, .06);
      --accent: #d8f0dc;
      --accent-strong: #1f6f43;
      --soft: #f8fafc;
      --chat: #e8edf3;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      background: linear-gradient(180deg, #f6f8fb 0%, #e9eef5 100%);
      color: var(--text);
    }}
    .app {{
      display: grid;
      grid-template-columns: 320px minmax(0, 1fr);
      min-height: 100vh;
    }}
    .sidebar {{
      position: sticky;
      top: 0;
      height: 100vh;
      overflow: auto;
      border-right: 1px solid var(--border);
      background: rgba(255, 255, 255, 0.92);
      backdrop-filter: blur(12px);
      padding: 18px 16px;
    }}
    .sidebar-title {{
      font-size: 18px;
      font-weight: 700;
      margin-bottom: 6px;
    }}
    .sidebar-subtitle {{
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 14px;
      line-height: 1.6;
    }}
    .case-nav {{
      display: grid;
      gap: 10px;
    }}
    .case-nav-item {{
      width: 100%;
      text-align: left;
      border: 1px solid var(--border);
      background: var(--panel);
      border-radius: 12px;
      padding: 12px;
      cursor: pointer;
      box-shadow: var(--shadow);
    }}
    .case-nav-item.active {{
      background: var(--accent);
      border-color: #bfe2c8;
    }}
    .nav-id {{
      display: block;
      font-size: 13px;
      font-weight: 700;
      margin-bottom: 4px;
    }}
    .nav-title {{
      display: block;
      font-size: 12px;
      line-height: 1.5;
      color: #334155;
    }}
    .content {{
      padding: 22px;
    }}
    .case-panel {{
      display: none;
      max-width: 1200px;
      margin: 0 auto;
    }}
    .case-panel.active {{
      display: block;
    }}
    .case-head {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 14px;
      margin-bottom: 16px;
    }}
    .case-title {{
      font-size: 24px;
      font-weight: 800;
      margin-bottom: 6px;
    }}
    .case-subtitle {{
      font-size: 14px;
      color: var(--muted);
      line-height: 1.6;
    }}
    .badge {{
      flex: 0 0 auto;
      min-width: 96px;
      text-align: center;
      background: #e5eefc;
      border: 1px solid #c7d6f5;
      border-radius: 999px;
      padding: 8px 12px;
      font-size: 12px;
      color: #1d4e89;
      font-weight: 600;
    }}
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 14px;
    }}
    .summary-card, .card, .fact-block {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 14px;
      box-shadow: var(--shadow);
      margin-bottom: 14px;
    }}
    .summary-label, .section-title, .fact-title {{
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 8px;
      font-weight: 700;
      letter-spacing: .02em;
    }}
    .summary-value {{
      font-size: 14px;
      line-height: 1.7;
    }}
    .fact-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 14px;
    }}
    .fact-block ul {{
      margin: 0;
      padding-left: 18px;
      line-height: 1.7;
      font-size: 13px;
    }}
    .kv-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }}
    .kv-item {{
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 12px;
      background: var(--soft);
    }}
    .kv-label {{
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 6px;
      font-weight: 700;
    }}
    .kv-value {{
      font-size: 14px;
      line-height: 1.7;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }}
    .doc-list {{
      display: grid;
      gap: 10px;
    }}
    .doc-item {{
      border: 1px solid var(--border);
      background: var(--soft);
      border-radius: 12px;
      padding: 12px;
    }}
    .doc-title {{
      font-size: 14px;
      font-weight: 700;
      margin-bottom: 6px;
    }}
    .doc-meta {{
      font-size: 12px;
      color: var(--muted);
      line-height: 1.6;
      overflow-wrap: anywhere;
    }}
    .empty {{
      color: var(--muted);
      font-size: 13px;
    }}
    .media-block {{
      margin-top: 10px;
    }}
    .media-block img {{
      max-width: min(420px, 100%);
      display: block;
      border-radius: 10px;
      border: 1px solid var(--border);
      background: #fff;
    }}
    .timeline-shell {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-bottom: 10px;
    }}
    .day-chip {{
      background: rgba(17, 24, 39, .08);
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 12px;
      color: #374151;
    }}
    .chat-shell {{
      background: var(--chat);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 14px 12px 20px;
    }}
    .message-row {{
      display: flex;
      align-items: flex-start;
      gap: 10px;
      margin: 12px 0;
    }}
    .message-row.role-service {{
      flex-direction: row-reverse;
    }}
    .avatar {{
      width: 36px;
      height: 36px;
      border-radius: 10px;
      background: #cbd5e1;
      color: #111827;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 14px;
      font-weight: 700;
      flex: 0 0 auto;
    }}
    .bubble-wrap {{
      max-width: min(760px, calc(100vw - 420px));
    }}
    .role-service .bubble-wrap {{
      text-align: right;
    }}
    .sender {{
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 4px;
    }}
    .bubble {{
      border-radius: 12px;
      padding: 10px 12px;
      box-shadow: var(--shadow);
      border: 1px solid rgba(0, 0, 0, .04);
      overflow-wrap: anywhere;
      text-align: left;
    }}
    .bubble-member {{
      background: var(--panel);
    }}
    .bubble-service {{
      background: #dff5d8;
    }}
    .meta {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      font-size: 11px;
      color: var(--muted);
      margin-bottom: 6px;
    }}
    .role-service .meta {{
      justify-content: flex-end;
    }}
    .text {{
      white-space: normal;
      line-height: 1.6;
      font-size: 14px;
    }}
    .raw-card {{
      padding-top: 10px;
    }}
    .raw-summary {{
      cursor: pointer;
      list-style: none;
    }}
    .raw-summary::-webkit-details-marker {{
      display: none;
    }}
    .raw-json {{
      margin: 10px 0 0;
      padding: 12px;
      border-radius: 12px;
      background: #0f172a;
      color: #d9e2f2;
      font-size: 12px;
      overflow: auto;
    }}
    @media (max-width: 900px) {{
      .app {{
        grid-template-columns: 1fr;
      }}
      .sidebar {{
        position: static;
        height: auto;
        border-right: 0;
        border-bottom: 1px solid var(--border);
      }}
      .summary-grid, .fact-grid, .kv-grid {{
        grid-template-columns: 1fr;
      }}
      .bubble-wrap {{
        max-width: calc(100vw - 96px);
      }}
    }}
  </style>
</head>
<body>
  <div class="app">
    <aside class="sidebar">
      <div class="sidebar-title">用例审阅页</div>
      <div class="sidebar-subtitle">统一展示标准答案、实际返回、页级结果、坐标级结果和聊天轨迹，便于人工复盘。</div>
      <div class="case-nav">
        {''.join(nav_items)}
      </div>
    </aside>
    <main class="content">
      {''.join(sections)}
    </main>
  </div>
  <script>
    const navItems = Array.from(document.querySelectorAll('.case-nav-item'));
    const panels = Array.from(document.querySelectorAll('.case-panel'));
    function activateCase(caseId) {{
      navItems.forEach((item) => item.classList.toggle('active', item.dataset.target === caseId));
      panels.forEach((panel) => panel.classList.toggle('active', panel.dataset.caseId === caseId));
      window.location.hash = caseId;
    }}
    navItems.forEach((item) => {{
      item.addEventListener('click', () => activateCase(item.dataset.target));
    }});
    const initial = window.location.hash ? window.location.hash.slice(1) : (navItems[0] && navItems[0].dataset.target);
    if (initial) activateCase(initial);
  </script>
</body>
</html>
"""


def main() -> None:
    args = parse_args()
    root = repo_root()
    fixture_path = root / args.fixture
    gold_path = root / args.gold
    output_path = root / args.output

    fixture = load_json(fixture_path)
    gold = load_json(gold_path)
    gold_map = {case["case_id"]: case for case in gold.get("cases", []) if isinstance(case, dict) and case.get("case_id")}

    fixture_cases = [
        case
        for case in fixture.get("cases", [])
        if isinstance(case, dict) and (case.get("metadata") or {}).get("evidence_source") == "db_room_export"
    ]

    actual_cases: list[dict[str, Any]] = []
    if args.actual_report:
        actual_report_path = Path(args.actual_report)
        if not actual_report_path.is_absolute():
            actual_report_path = root / args.actual_report
        actual_report = load_json(actual_report_path)
        actual_cases = [case for case in actual_report.get("cases", []) if isinstance(case, dict)]

    score_cases: list[dict[str, Any]] | None = None
    if args.score_report:
        score_report_path = Path(args.score_report)
        if not score_report_path.is_absolute():
            score_report_path = root / args.score_report
        score_report = load_json(score_report_path)
        score_cases = [case for case in score_report.get("cases", []) if isinstance(case, dict)]

    cases = actual_cases if actual_cases else fixture_cases
    source_lookup = build_source_case_lookup(fixture_cases=fixture_cases, actual_cases=actual_cases)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        build_html(
            cases=cases,
            gold_map=gold_map,
            output_path=output_path,
            score_cases=score_cases,
            source_case_lookup=source_lookup,
        ),
        encoding="utf-8",
    )
    print(output_path)
    print(f"case_count={len(cases)}")


if __name__ == "__main__":
    main()

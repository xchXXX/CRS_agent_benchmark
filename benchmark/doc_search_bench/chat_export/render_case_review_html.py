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
    parser = argparse.ArgumentParser(description="生成按 case 隔离切换的聊天记录审阅 HTML")
    parser.add_argument(
        "--fixture",
        default="benchmark/doc_search_bench/envs/doc_search/data/train/real_world_wecom_train.fixture.json",
    )
    parser.add_argument(
        "--gold",
        default="benchmark/doc_search_bench/envs/doc_search/data/train/real_world_wecom_train.gold.json",
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


def render_question_images(case: dict[str, Any], base_dir: Path) -> str:
    images = case.get("question_images") or []
    if not images:
        return ""
    blocks = [render_media(path, "image", base_dir) for path in images]
    return f"""
    <div class="asset-panel">
      <div class="asset-title">case 封装题图</div>
      {''.join(blocks)}
    </div>
    """


def render_fact_list(title: str, values: Any) -> str:
    if not values:
        return ""
    if isinstance(values, dict):
        items = []
        for key, value in values.items():
            if value:
                rendered = " / ".join(str(x) for x in value) if isinstance(value, list) else str(value)
                items.append(f"<li><strong>{html.escape(str(key))}</strong>：{html.escape(rendered)}</li>")
        if not items:
            return ""
        return f'<div class="fact-block"><div class="fact-title">{html.escape(title)}</div><ul>{"".join(items)}</ul></div>'
    if isinstance(values, list):
        items = "".join(f"<li>{html.escape(str(x))}</li>" for x in values if x)
        if not items:
            return ""
        return f'<div class="fact-block"><div class="fact-title">{html.escape(title)}</div><ul>{items}</ul></div>'
    return ""


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


def resolve_gold_target_titles(gold_case: dict[str, Any]) -> list[str]:
    target_docs = gold_case.get("target_docs") if isinstance(gold_case.get("target_docs"), list) else []
    titles: list[str] = []
    for item in target_docs:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if title:
            titles.append(title)
    if titles:
        return normalize_string_list(titles)
    return normalize_string_list(gold_case.get("accepted_titles"))


def render_target_doc_summary(gold_case: dict[str, Any]) -> str:
    target_titles = resolve_gold_target_titles(gold_case)
    if not target_titles:
        return '<div class="summary-value">未记录</div>'
    match_mode = str(gold_case.get("target_match_mode") or "any_of")
    pills = "".join(f'<li class="target-pill">{html.escape(item)}</li>' for item in target_titles)
    return f"""
    <div class="summary-value">
      <div class="target-meta">target_match_mode={html.escape(match_mode)} · target_doc_count={len(target_titles)}</div>
      <ul class="target-pill-list">{pills}</ul>
    </div>
    """


def render_case_section(case: dict[str, Any], gold_case: dict[str, Any], base_dir: Path, active: bool) -> str:
    case_id = case["case_id"]
    metadata = case.get("metadata") or {}
    transcript = metadata.get("transcript") or []
    target_titles = resolve_gold_target_titles(gold_case)
    accepted_title = target_titles[0] if target_titles else ""
    cards = "\n".join(render_message_card(message, base_dir) for message in transcript)
    day_labels: list[str] = []
    for message in transcript:
        msg_date = str(message.get("msg_date") or "")
        day = msg_date[:10] if msg_date else "未知日期"
        if not day_labels or day_labels[-1] != day:
            day_labels.append(day)
    facts_html = "".join(
        [
            render_fact_list("known_items", (case.get("user_profile") or {}).get("known_items")),
            render_fact_list("uncertain_items", (case.get("user_profile") or {}).get("uncertain_items")),
        ]
    )
    room_id = html.escape(str(metadata.get("room_id") or ""))
    opening_id = html.escape(str(metadata.get("opening_message_id") or ""))
    answer_id = html.escape(str(metadata.get("answer_message_id") or ""))
    section_class = "case-panel active" if active else "case-panel"
    return f"""
    <section id="{html.escape(case_id)}" class="{section_class}" data-case-id="{html.escape(case_id)}">
      <div class="case-head">
        <div>
          <div class="case-title">{html.escape(case_id)}</div>
          <div class="case-subtitle">{html.escape(case.get("question_text") or "")}</div>
        </div>
        <div class="badge">{html.escape(str(case.get("input_modality") or ""))}</div>
      </div>

      <div class="summary-grid">
        <div class="summary-card">
          <div class="summary-label">initial_user_message</div>
          <div class="summary-value">{html.escape(str(case.get("initial_user_message") or ""))}</div>
        </div>
        <div class="summary-card">
          <div class="summary-label">目标文档</div>
          {render_target_doc_summary(gold_case)}
        </div>
        <div class="summary-card">
          <div class="summary-label">聊天溯源</div>
          <div class="summary-value">room_id={room_id}<br>opening_message_id={opening_id}<br>answer_message_id={answer_id or "未单独标注"}</div>
        </div>
        <div class="summary-card">
          <div class="summary-label">用户目标</div>
          <div class="summary-value">{html.escape(str((case.get("user_profile") or {}).get("goal") or ""))}</div>
        </div>
      </div>

      <div class="fact-grid">
        {facts_html}
      </div>

      {render_question_images(case, base_dir)}

      <div class="timeline-title">聊天片段</div>
      <div class="timeline-shell">
        {' '.join(f'<div class="day-chip">{html.escape(day)}</div>' for day in day_labels)}
      </div>
      <div class="chat-shell">
        {cards}
      </div>
    </section>
    """


def build_html(cases: list[dict[str, Any]], gold_map: dict[str, dict[str, Any]], output_path: Path) -> str:
    base_dir = output_path.parent
    nav_items = []
    sections = []
    for idx, case in enumerate(cases):
        case_id = case["case_id"]
        gold_case = gold_map[case_id]
        target_titles = resolve_gold_target_titles(gold_case)
        accepted_title = target_titles[0] if target_titles else ""
        nav_items.append(
            f"""
            <button class="case-nav-item{' active' if idx == 0 else ''}" data-target="{html.escape(case_id)}">
              <span class="nav-id">{html.escape(case_id)}</span>
              <span class="nav-title">{html.escape(accepted_title)}</span>
            </button>
            """
        )
        sections.append(render_case_section(case, gold_case, base_dir, active=idx == 0))
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>real_world_wecom_train 可溯源 case 审阅</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #eef1f4;
      --panel: #f7f7f7;
      --text: #1f2329;
      --muted: #6b7280;
      --green: #95ec69;
      --white: #ffffff;
      --border: #d1d5db;
      --shadow: 0 1px 2px rgba(0, 0, 0, .08);
      --blue: #dbeafe;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      background: var(--bg);
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
      background: #f8fafc;
      padding: 16px;
    }}
    .sidebar-title {{
      font-size: 18px;
      font-weight: 600;
      margin-bottom: 6px;
    }}
    .sidebar-subtitle {{
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 14px;
      line-height: 1.5;
    }}
    .case-nav {{
      display: grid;
      gap: 10px;
    }}
    .case-nav-item {{
      width: 100%;
      text-align: left;
      border: 1px solid var(--border);
      background: var(--white);
      border-radius: 8px;
      padding: 10px 12px;
      cursor: pointer;
      box-shadow: var(--shadow);
    }}
    .case-nav-item.active {{
      background: var(--green);
    }}
    .nav-id {{
      display: block;
      font-size: 13px;
      font-weight: 600;
      margin-bottom: 4px;
    }}
    .nav-title {{
      display: block;
      font-size: 12px;
      line-height: 1.45;
    }}
    .content {{
      padding: 18px;
    }}
    .case-panel {{
      display: none;
      max-width: 1100px;
      margin: 0 auto;
    }}
    .case-panel.active {{
      display: block;
    }}
    .case-head {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 14px;
    }}
    .case-title {{
      font-size: 22px;
      font-weight: 700;
      margin-bottom: 4px;
    }}
    .case-subtitle {{
      font-size: 14px;
      color: var(--muted);
      line-height: 1.5;
    }}
    .badge {{
      flex: 0 0 auto;
      min-width: 96px;
      text-align: center;
      background: var(--blue);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 8px 10px;
      font-size: 12px;
    }}
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 14px;
    }}
    .summary-card, .fact-block, .asset-panel {{
      background: var(--white);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px;
      box-shadow: var(--shadow);
    }}
    .summary-label, .fact-title, .asset-title, .timeline-title {{
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 8px;
      font-weight: 600;
    }}
    .summary-value {{
      font-size: 14px;
      line-height: 1.6;
    }}
    .target-meta {{
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 8px;
    }}
    .target-pill-list {{
      list-style: none;
      padding: 0;
      margin: 0;
      display: grid;
      gap: 8px;
    }}
    .target-pill {{
      display: block;
      padding: 8px 10px;
      border-radius: 8px;
      background: #f8fafc;
      border: 1px solid var(--border);
      overflow-wrap: anywhere;
    }}
    .fact-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 14px;
    }}
    .fact-block ul {{
      margin: 0;
      padding-left: 18px;
      line-height: 1.6;
      font-size: 13px;
    }}
    .asset-panel {{
      margin-bottom: 14px;
    }}
    .media-block {{
      margin-top: 10px;
    }}
    .media-block img {{
      max-width: min(420px, 100%);
      display: block;
      border-radius: 8px;
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
      background: #ebedf0;
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 14px 12px 28px;
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
      border-radius: 8px;
      background: #cbd5e1;
      color: #111827;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 14px;
      font-weight: 600;
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
      border-radius: 10px;
      padding: 10px 12px;
      box-shadow: var(--shadow);
      border: 1px solid rgba(0, 0, 0, .04);
      overflow-wrap: anywhere;
      text-align: left;
    }}
    .bubble-member {{
      background: var(--white);
    }}
    .bubble-service {{
      background: var(--green);
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
      line-height: 1.55;
      font-size: 14px;
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
      .summary-grid, .fact-grid {{
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
      <div class="sidebar-title">可溯源 case 审阅</div>
      <div class="sidebar-subtitle">单页 HTML，页面内按 case 隔离切换。仅包含 evidence_source=`db_room_export` 的真实聊天可回溯 case。</div>
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
    gold_map = {case["case_id"]: case for case in gold["cases"]}

    cases = [
        case
        for case in fixture["cases"]
        if (case.get("metadata") or {}).get("evidence_source") == "db_room_export"
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(build_html(cases, gold_map, output_path), encoding="utf-8")
    print(output_path)
    print(f"case_count={len(cases)}")


if __name__ == "__main__":
    main()

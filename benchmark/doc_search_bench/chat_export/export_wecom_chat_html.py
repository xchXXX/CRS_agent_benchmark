from __future__ import annotations

import argparse
import html
import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pymysql


DEFAULT_PAGE_SIZE = 400
SYSTEM_SENDERS = {
    "GongGui02",
    "GongGuiZhiJiaJiShu-QiuLingTong",
    "ZhuYang_1",
}


@dataclass(frozen=True)
class RoomSummary:
    room_id: str
    message_count: int
    min_msg_date: datetime | None
    max_msg_date: datetime | None


@dataclass(frozen=True)
class RoomExportSummary:
    room_id: str
    slug: str
    total_message_count: int
    exported_message_count: int
    page_count: int
    file_message_count: int
    min_msg_date: str | None
    max_msg_date: str | None
    per_msgtype_counts: dict[str, int]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出企业微信群聊天记录并生成静态 HTML 可视化")
    parser.add_argument("--host", default="139.196.163.235")
    parser.add_argument("--port", type=int, default=65025)
    parser.add_argument("--user", default="zhangjiexiang")
    parser.add_argument("--password", default="Oi9S9GIl@WQNgIAce")
    parser.add_argument("--database", default="wxt")
    parser.add_argument("--table", default="ggzj_work_wx_chat_content")
    parser.add_argument("--output-dir", default=None, help="导出目录，默认 benchmark/reports/chat_exports/<timestamp>")
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE, help="每页消息条数，默认 400")
    parser.add_argument("--room-limit", type=int, default=None, help="只导出前 N 个房间")
    parser.add_argument("--message-limit-per-room", type=int, default=None, help="每个房间最多导出多少条消息")
    return parser.parse_args()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def default_output_dir() -> Path:
    export_id = datetime.now().strftime("wecom-chat-export-%Y%m%dT%H%M%S")
    return repo_root() / "benchmark" / "reports" / "chat_exports" / export_id


def connect_mysql(args: argparse.Namespace):
    return pymysql.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database=args.database,
        charset="utf8mb4",
    )


def fetch_export_snapshot_max_id(conn, table: str) -> int:
    sql = f"select coalesce(max(id), 0) from {table}"
    with conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
    return int(row[0] or 0)


def fetch_room_summaries(conn, table: str, room_limit: int | None, snapshot_max_id: int) -> list[RoomSummary]:
    sql = f"""
        select room_id, count(*) as c, min(msg_date), max(msg_date)
        from {table}
        where room_id is not null and room_id <> '' and id <= %s
        group by room_id
        order by c desc, room_id asc
    """
    if room_limit is not None:
        sql += f" limit {int(room_limit)}"
    with conn.cursor() as cur:
        cur.execute(sql, (snapshot_max_id,))
        rows = cur.fetchall()
    return [
        RoomSummary(
            room_id=str(room_id),
            message_count=int(message_count),
            min_msg_date=min_msg_date,
            max_msg_date=max_msg_date,
        )
        for room_id, message_count, min_msg_date, max_msg_date in rows
    ]


def fetch_room_messages(conn, table: str, room_id: str, message_limit: int | None, snapshot_max_id: int) -> list[dict[str, Any]]:
    sql = f"""
        select id, msg_id, chat_action, chat_from, tolist, room_id, msgtime, msgtype,
               content, create_time, voip_id, user, path_url, file_type, is_split, msg_date, seq
        from {table}
        where room_id = %s and id <= %s
        order by msg_date asc, id asc
    """
    if message_limit is not None:
        sql += f" limit {int(message_limit)}"
    with conn.cursor() as cur:
        cur.execute(sql, (room_id, snapshot_max_id))
        rows = cur.fetchall()

    messages: list[dict[str, Any]] = []
    for row in rows:
        (
            msg_pk,
            msg_id,
            chat_action,
            chat_from,
            tolist,
            room_id,
            msgtime,
            msgtype,
            content,
            create_time,
            voip_id,
            user,
            path_url,
            file_type,
            is_split,
            msg_date,
            seq,
        ) = row
        messages.append(
            {
                "id": msg_pk,
                "msg_id": msg_id,
                "chat_action": chat_action,
                "chat_from": chat_from,
                "tolist": tolist,
                "room_id": room_id,
                "msgtime": msgtime,
                "msgtype": msgtype,
                "content": content,
                "create_time": str(create_time) if create_time is not None else None,
                "voip_id": voip_id,
                "user": user,
                "path_url": path_url,
                "file_type": file_type,
                "is_split": is_split,
                "msg_date": str(msg_date) if msg_date is not None else None,
                "seq": seq,
            }
        )
    return messages


def safe_slug(text: str) -> str:
    raw = text.strip() or "room"
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in raw)


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
        return str(message.get("content") or "")
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
    if msgtype == "weapp":
        title = ""
        desc = ""
        pagepath = ""
        if payload:
            title = str(payload.get("title") or "").strip()
            desc = str(payload.get("description") or "").strip()
            pagepath = str(payload.get("pagepath") or "").strip()
        parts = [part for part in [title, desc, pagepath] if part]
        return "小程序卡片: " + " | ".join(parts) if parts else "[小程序卡片]"
    if msgtype == "file":
        if payload:
            filename = str(payload.get("filename") or "").strip()
            fileext = str(payload.get("fileext") or "").strip()
            filesize = payload.get("filesize")
            parts = [part for part in [filename, fileext] if part]
            if isinstance(filesize, int):
                parts.append(f"{filesize} bytes")
            return "文件: " + " | ".join(parts) if parts else "[文件]"
        return "[文件]"
    if msgtype in {"mixed", "markdown"}:
        if payload and isinstance(payload.get("content"), str):
            return payload["content"]
        return str(message.get("content") or "")
    if path_url:
        return f"[{msgtype}]"
    return str(message.get("content") or f"[{msgtype}]")


def message_media_link(message: dict[str, Any]) -> str | None:
    path_url = str(message.get("path_url") or "").strip()
    return path_url or None


def sender_role(chat_from: str) -> str:
    if chat_from in SYSTEM_SENDERS or chat_from.startswith("GongGui"):
        return "service"
    return "member"


def normalize_day_label(msg_date: str | None) -> str:
    if not msg_date:
        return "未知日期"
    return msg_date[:10]


def render_day_divider(day_label: str) -> str:
    return f"""
    <div class="day-divider">
      <span>{html.escape(day_label)}</span>
    </div>
    """


def render_message_card(message: dict[str, Any]) -> str:
    sender = str(message.get("chat_from") or "unknown")
    role = sender_role(sender)
    msgtype = str(message.get("msgtype") or "")
    text = html.escape(display_message_text(message)).replace("\n", "<br>")
    msg_date = html.escape(str(message.get("msg_date") or ""))
    media_link = message_media_link(message)
    media_html = ""
    if media_link:
        escaped_link = html.escape(media_link, quote=True)
        if msgtype == "image":
            media_html = (
                f'<div class="media-block"><a href="{escaped_link}" target="_blank" rel="noreferrer">'
                f'<img src="{escaped_link}" alt="image" loading="lazy"></a></div>'
            )
        elif msgtype == "video":
            media_html = (
                f'<div class="media-block"><a href="{escaped_link}" target="_blank" rel="noreferrer">'
                "打开视频资源</a></div>"
            )
        else:
            media_html = (
                f'<div class="media-block"><a href="{escaped_link}" target="_blank" rel="noreferrer">'
                "打开资源</a></div>"
            )
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
          <div class="text">{text or '&nbsp;'}</div>
          {media_html}
        </div>
      </div>
    </div>
    """


def render_room_cards(messages: list[dict[str, Any]]) -> str:
    html_parts: list[str] = []
    current_day: str | None = None
    for message in messages:
        day_label = normalize_day_label(message.get("msg_date"))
        if day_label != current_day:
            html_parts.append(render_day_divider(day_label))
            current_day = day_label
        html_parts.append(render_message_card(message))
    return "\n".join(html_parts)


def render_room_page(
    *,
    room: RoomSummary,
    page_index: int,
    total_pages: int,
    messages: list[dict[str, Any]],
    export_id: str,
) -> str:
    cards = render_room_cards(messages)
    prev_link = f"page-{page_index:03d}.html" if page_index > 1 else None
    next_link = f"page-{page_index + 2:03d}.html" if page_index + 1 < total_pages else None
    page_links = []
    for idx in range(total_pages):
        href = f"page-{idx + 1:03d}.html"
        cls = "page-link current" if idx == page_index else "page-link"
        page_links.append(f'<a class="{cls}" href="{href}">{idx + 1}</a>')
    pager_html = "".join(page_links)
    min_date = html.escape(str(room.min_msg_date or ""))
    max_date = html.escape(str(room.max_msg_date or ""))
    prev_html = f'<a class="nav-link" href="{prev_link}">上一页</a>' if prev_link else '<span class="nav-link disabled">上一页</span>'
    next_html = f'<a class="nav-link" href="{next_link}">下一页</a>' if next_link else '<span class="nav-link disabled">下一页</span>'
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(room.room_id)} - 第 {page_index + 1} 页</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #ebedf0;
      --panel: #f7f7f7;
      --text: #1f2329;
      --muted: #6b7280;
      --green: #95ec69;
      --white: #ffffff;
      --border: #d1d5db;
      --shadow: 0 1px 2px rgba(0, 0, 0, .08);
    }}
    body {{
      margin: 0;
      font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      background: var(--bg);
      color: var(--text);
    }}
    .topbar {{
      position: sticky;
      top: 0;
      z-index: 10;
      background: rgba(247, 247, 247, .96);
      backdrop-filter: blur(8px);
      border-bottom: 1px solid var(--border);
      padding: 12px 18px;
    }}
    .title {{
      font-size: 18px;
      font-weight: 600;
      margin-bottom: 4px;
    }}
    .subtitle {{
      font-size: 12px;
      color: var(--muted);
    }}
    .toolbar {{
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
      margin-top: 10px;
    }}
    .nav-link, .page-link {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 36px;
      height: 32px;
      padding: 0 10px;
      border-radius: 6px;
      text-decoration: none;
      color: var(--text);
      background: var(--white);
      border: 1px solid var(--border);
    }}
    .page-link.current {{
      background: var(--green);
    }}
    .nav-link.disabled {{
      opacity: .45;
      background: #f0f0f0;
      border: 1px solid var(--border);
    }}
    .shell {{
      max-width: 980px;
      margin: 0 auto;
      padding: 16px 12px 48px;
    }}
    .day-divider {{
      display: flex;
      justify-content: center;
      margin: 18px 0 12px;
    }}
    .day-divider span {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 24px;
      padding: 0 10px;
      border-radius: 999px;
      background: rgba(17, 24, 39, .12);
      color: #374151;
      font-size: 12px;
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
      max-width: min(760px, calc(100vw - 96px));
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
    .media-block {{
      margin-top: 10px;
    }}
    .media-block img {{
      max-width: min(360px, 100%);
      display: block;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: #fff;
    }}
    .role-service .media-block img {{
      margin-left: auto;
    }}
    .footer {{
      max-width: 980px;
      margin: 0 auto 24px;
      color: var(--muted);
      font-size: 12px;
      padding: 0 12px;
    }}
  </style>
</head>
<body>
  <div class="topbar">
    <div class="title">群聊 {html.escape(room.room_id)}</div>
    <div class="subtitle">导出批次 {html.escape(export_id)} | 共 {room.message_count} 条 | 时间 {min_date} 至 {max_date}</div>
    <div class="toolbar">
      <a class="nav-link" href="../../index.html">返回索引</a>
      {prev_html}
      {next_html}
      <div>{pager_html}</div>
    </div>
  </div>
  <div class="shell">
    {cards}
  </div>
  <div class="footer">本页仅做离线审阅展示，原始证据以同目录 JSON 为准。</div>
</body>
    </html>
"""


def build_room_export_summary(room: RoomSummary, room_slug: str, messages: list[dict[str, Any]], page_size: int) -> RoomExportSummary:
    msgtype_counts: Counter[str] = Counter(str(item.get("msgtype") or "") for item in messages)
    return RoomExportSummary(
        room_id=room.room_id,
        slug=room_slug,
        total_message_count=room.message_count,
        exported_message_count=len(messages),
        page_count=max(1, math.ceil(len(messages) / page_size)),
        file_message_count=int(msgtype_counts.get("file", 0)),
        min_msg_date=str(room.min_msg_date) if room.min_msg_date is not None else None,
        max_msg_date=str(room.max_msg_date) if room.max_msg_date is not None else None,
        per_msgtype_counts=dict(sorted(msgtype_counts.items(), key=lambda item: (-item[1], item[0]))),
    )


def render_index(export_id: str, room_exports: list[RoomExportSummary], page_size: int) -> str:
    rows = []
    for room in room_exports:
        rows.append(
            f"""
            <tr>
              <td><a href="rooms/{room.slug}/page-001.html">{html.escape(room.room_id)}</a></td>
              <td>{room.total_message_count}</td>
              <td>{room.exported_message_count}</td>
              <td>{room.file_message_count}</td>
              <td>{html.escape(str(room.min_msg_date or ''))}</td>
              <td>{html.escape(str(room.max_msg_date or ''))}</td>
              <td>{room.page_count}</td>
            </tr>
            """
        )
    table_rows = "\n".join(rows)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>微信群聊天导出索引</title>
  <style>
    body {{
      margin: 0;
      font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      background: #f5f6f8;
      color: #1f2329;
    }}
    .wrap {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 24px 16px 48px;
    }}
    h1 {{
      font-size: 24px;
      margin: 0 0 8px;
    }}
    .desc {{
      color: #6b7280;
      margin-bottom: 18px;
      font-size: 14px;
      line-height: 1.6;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: #fff;
      border: 1px solid #d1d5db;
    }}
    th, td {{
      padding: 10px 12px;
      border-bottom: 1px solid #e5e7eb;
      text-align: left;
      font-size: 14px;
      vertical-align: top;
    }}
    th {{
      background: #f8fafc;
    }}
    a {{
      color: #0f766e;
      text-decoration: none;
    }}
    .toolbar {{
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
      margin: 0 0 16px;
    }}
    .search-input {{
      width: min(480px, 100%);
      min-height: 40px;
      padding: 0 12px;
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      font-size: 14px;
      background: #fff;
      color: #111827;
      box-sizing: border-box;
    }}
    .search-input:focus {{
      outline: none;
      border-color: #0f766e;
      box-shadow: 0 0 0 3px rgba(15, 118, 110, .12);
    }}
    .search-meta {{
      color: #475569;
      font-size: 13px;
    }}
    .row-hidden {{
      display: none;
    }}
  </style>
</head>
<body>
    <div class="wrap">
      <h1>微信群聊天导出索引</h1>
    <div class="desc">导出批次 {html.escape(export_id)}，每页 {page_size} 条。点击 room_id 可以进入接近微信聊天记录样式的静态页面。原始 JSON 位于同目录 <code>raw/</code> 与 <code>rooms/*/messages.json</code>。</div>
    <div class="toolbar">
      <input id="roomSearchInput" class="search-input" type="search" placeholder="搜索 room_id，例如 wr7pwYBwAAs6cb-jRCgXfvd0JukHTeDw" autocomplete="off">
      <div id="searchMeta" class="search-meta">显示全部 {len(room_exports)} 个群</div>
    </div>
    <table>
      <thead>
        <tr>
          <th>room_id</th>
          <th>总消息数</th>
          <th>导出消息数</th>
          <th>file 消息数</th>
          <th>开始时间</th>
          <th>结束时间</th>
          <th>分页数</th>
        </tr>
      </thead>
      <tbody id="roomTableBody">
        {table_rows}
      </tbody>
    </table>
  </div>
  <script>
    (function () {{
      const input = document.getElementById('roomSearchInput');
      const meta = document.getElementById('searchMeta');
      const rows = Array.from(document.querySelectorAll('#roomTableBody tr'));

      function update() {{
        const keyword = input.value.trim().toLowerCase();
        let visibleCount = 0;
        rows.forEach((row) => {{
          const roomCell = row.cells[0];
          const roomId = roomCell ? roomCell.innerText.toLowerCase() : '';
          const matched = !keyword || roomId.includes(keyword);
          row.classList.toggle('row-hidden', !matched);
          if (matched) {{
            visibleCount += 1;
          }}
        }});
        meta.textContent = keyword
          ? `关键字 "${{input.value.trim()}}" 匹配 ${{visibleCount}} / {len(room_exports)} 个群`
          : `显示全部 {len(room_exports)} 个群`;
      }}

      input.addEventListener('input', update);
      update();
    }})();
  </script>
</body>
</html>
"""


def render_manifest(export_id: str, room_exports: list[RoomExportSummary], page_size: int) -> dict[str, Any]:
    msgtype_counts: Counter[str] = Counter()
    for room in room_exports:
        msgtype_counts.update(room.per_msgtype_counts)
    return {
        "export_id": export_id,
        "generated_at": datetime.now().isoformat(),
        "room_count": len(room_exports),
        "page_size": page_size,
        "total_messages": sum(room.total_message_count for room in room_exports),
        "exported_messages": sum(room.exported_message_count for room in room_exports),
        "message_types": dict(sorted(msgtype_counts.items(), key=lambda item: (-item[1], item[0]))),
        "rooms": [
            {
                "room_id": room.room_id,
                "slug": room.slug,
                "total_message_count": room.total_message_count,
                "exported_message_count": room.exported_message_count,
                "page_count": room.page_count,
                "file_message_count": room.file_message_count,
                "min_msg_date": room.min_msg_date,
                "max_msg_date": room.max_msg_date,
                "per_msgtype_counts": room.per_msgtype_counts,
            }
            for room in room_exports
        ],
    }


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    rooms_dir = output_dir / "rooms"
    raw_dir = output_dir / "raw"
    rooms_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    export_id = output_dir.name
    page_size = max(1, int(args.page_size))

    conn = connect_mysql(args)
    try:
        snapshot_max_id = fetch_export_snapshot_max_id(conn, args.table)
        rooms = fetch_room_summaries(conn, args.table, args.room_limit, snapshot_max_id)
        raw_dir.joinpath("room_summaries.json").write_text(
            json.dumps([room.__dict__ for room in rooms], ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

        room_exports: list[RoomExportSummary] = []
        for room in rooms:
            messages = fetch_room_messages(conn, args.table, room.room_id, args.message_limit_per_room, snapshot_max_id)
            room_slug = safe_slug(room.room_id)
            room_dir = rooms_dir / room_slug
            room_dir.mkdir(parents=True, exist_ok=True)
            room_dir.joinpath("messages.json").write_text(
                json.dumps(messages, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            total_pages = max(1, math.ceil(len(messages) / page_size))
            room_exports.append(build_room_export_summary(room, room_slug, messages, page_size))
            for page_index in range(total_pages):
                start = page_index * page_size
                end = start + page_size
                html_text = render_room_page(
                    room=room,
                    page_index=page_index,
                    total_pages=total_pages,
                    messages=messages[start:end],
                    export_id=export_id,
                )
                room_dir.joinpath(f"page-{page_index + 1:03d}.html").write_text(
                    html_text,
                    encoding="utf-8",
                )

        output_dir.joinpath("index.html").write_text(
            render_index(export_id, room_exports, page_size),
            encoding="utf-8",
        )
        output_dir.joinpath("manifest.json").write_text(
            json.dumps(
                {
                    **render_manifest(export_id, room_exports, page_size),
                    "snapshot_max_id": snapshot_max_id,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(output_dir)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

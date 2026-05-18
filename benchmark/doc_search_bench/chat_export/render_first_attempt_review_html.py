from __future__ import annotations

import argparse
import base64
import html
import json
import os
import mimetypes
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="渲染每个 case 的首次 attempt 完整流程 HTML")
    parser.add_argument(
        "--run-id",
        help="例如 train-20260514T104548Z；与 --report 二选一，优先使用 --run-id",
    )
    parser.add_argument(
        "--report",
        help="report.actual.json 的绝对或相对路径；与 --run-id 二选一",
    )
    parser.add_argument(
        "--fixture",
        default="benchmark/doc_search_bench/envs/doc_search/data/train/real_world_wecom_train.fixture.json",
        help="包含用户画像与题图的 fixture 文件",
    )
    parser.add_argument(
        "--output",
        help="输出 HTML 路径；默认写到对应 run 目录下",
    )
    return parser.parse_args()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def resolve_report_path(args: argparse.Namespace) -> Path:
    root = repo_root()
    if args.run_id:
        return root / "benchmark" / "reports" / "runs" / args.run_id / "report.actual.json"
    if args.report:
        path = Path(args.report)
        return path if path.is_absolute() else (root / path)
    raise SystemExit("必须提供 --run-id 或 --report")


def resolve_output_path(args: argparse.Namespace, report_path: Path) -> Path:
    if args.output:
        out = Path(args.output)
        return out if out.is_absolute() else (repo_root() / out)
    run_dir = report_path.parent
    return run_dir / "first_attempt_review.html"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def rel_href(target: str | Path, base_dir: Path) -> str:
    value = str(target).strip()
    if not value:
        return ""
    path = Path(value)
    if not path.is_absolute():
        path = (repo_root() / value).resolve()
    return os.path.relpath(path, start=base_dir.resolve()).replace("\\", "/")


def html_text(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def infer_visible_result_count(content: dict[str, Any]) -> int:
    results = content.get("results") or []
    if not isinstance(results, list):
        return 0
    # 前端 results 列表首屏默认按 5 条分页展示
    return min(len(results), 5)


def infer_recall_hit(turn: dict[str, Any]) -> bool:
    analysis = turn.get("analysis")
    if isinstance(analysis, dict) and isinstance(analysis.get("final_hit"), bool):
        return bool(analysis["final_hit"])

    response = turn.get("response") or {}
    if isinstance(response, dict) and isinstance(response.get("final_status"), str):
        final_status = str(response.get("final_status") or "")
        if final_status.startswith("success_"):
            return True
        if final_status.startswith("failed_"):
            return False
    return False


def resolve_user_decision_reason(turn: dict[str, Any]) -> str:
    reasons: list[str] = []
    for key in ("user_decision_reason", "decision_reason"):
        value = turn.get(key)
        if value not in (None, ""):
            reasons.append(str(value))

    request_payload = turn.get("request_payload") or {}
    ask_user_answer = request_payload.get("ask_user_answer") if isinstance(request_payload, dict) else {}
    if isinstance(ask_user_answer, dict):
        metadata = ask_user_answer.get("metadata") or {}
        if isinstance(metadata, dict):
            reason = metadata.get("reason")
            if reason not in (None, ""):
                reasons.append(str(reason))

    response_body = turn.get("response_body") or {}
    content = response_body.get("content") or {}
    if isinstance(content, dict):
        ask_user = content.get("ask_user") or {}
        if isinstance(ask_user, dict):
            options = ask_user.get("options") or []
            selected_label = str(turn.get("selected_option_label") or turn.get("user_response_text") or "").strip()
            for option in options:
                if not isinstance(option, dict):
                    continue
                if selected_label and selected_label in {str(option.get("label") or ""), str(option.get("key") or "")}:
                    description = option.get("description")
                    if description not in (None, ""):
                        reasons.append(str(description))
                    break

    deduped: list[str] = []
    for reason in reasons:
        if reason and reason not in deduped:
            deduped.append(reason)
    return " | ".join(deduped)


def inline_media_href(path_or_url: str, base_dir: Path) -> str:
    link = str(path_or_url or "").strip()
    if not link:
        return ""
    if link.startswith(("http://", "https://", "data:")):
        return link

    candidate = Path(link)
    if not candidate.is_absolute():
        candidate = (repo_root() / link).resolve()

    if not candidate.exists() or not candidate.is_file():
        return rel_href(candidate, base_dir)

    mime, _ = mimetypes.guess_type(str(candidate))
    mime = mime or "application/octet-stream"
    data = candidate.read_bytes()
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def render_summary_summary(turn: dict[str, Any]) -> str:
    response_body = turn.get("response_body") or {}
    result_summary = response_body.get("result_summary") or {}
    if not isinstance(result_summary, dict):
        result_summary = {}
    pieces = []
    for key in ("display_title", "display_subtitle", "preview"):
        value = result_summary.get(key)
        if value not in (None, ""):
            pieces.append(str(value))
    if not pieces:
        return ""
    return " / ".join(pieces)


def render_fact_block(title: str, facts: Any) -> str:
    if not facts:
        return ""
    if isinstance(facts, dict):
        items = []
        for key, values in facts.items():
            if not values:
                continue
            if isinstance(values, list):
                rendered = " / ".join(str(item) for item in values)
            else:
                rendered = str(values)
            items.append(f"<li><strong>{html_text(key)}</strong>：{html_text(rendered)}</li>")
        if not items:
            return ""
        body = "".join(items)
    elif isinstance(facts, list):
        body = "".join(f"<li>{html_text(item)}</li>" for item in facts if item)
        if not body:
            return ""
    else:
        return ""
    return f"""
    <section class="fact-card">
      <div class="section-kicker">{html_text(title)}</div>
      <ul>{body}</ul>
    </section>
    """


def render_question_images(images: list[str], base_dir: Path) -> str:
    if not images:
        return ""
    blocks = []
    for image in images:
        href = inline_media_href(image, base_dir)
        blocks.append(
            f'<a class="image-link" href="{html_text(href)}" target="_blank" rel="noreferrer">'
            f'<img src="{html_text(href)}" alt="question image" loading="lazy"></a>'
        )
    return f"""
    <section class="card">
      <div class="section-title">题图</div>
      <div class="image-grid">{''.join(blocks)}</div>
    </section>
    """


def render_option(option: dict[str, Any], selected_label: str | None) -> str:
    label = str(option.get("label") or option.get("key") or "").strip()
    description = str(option.get("description") or "").strip()
    selected = label and selected_label and label == selected_label
    class_name = "option-pill selected" if selected else "option-pill"
    desc_html = f'<div class="option-desc">{html_text(description)}</div>' if description else ""
    badge_html = '<span class="picked-badge">已选</span>' if selected else ""
    return (
        f'<div class="{class_name}"><div class="option-head">{html_text(label)}{badge_html}</div>{desc_html}</div>'
    )


def render_decision_evidence(evidence: dict[str, Any] | None) -> str:
    if not isinstance(evidence, dict) or not evidence:
        return ""
    supports = evidence.get("supports") if isinstance(evidence.get("supports"), list) else []
    conflicts = evidence.get("conflicts") if isinstance(evidence.get("conflicts"), list) else []
    blocks: list[str] = []
    if supports:
        blocks.append(
            f"<div><span>supports</span><strong>{html_text(' / '.join(str(item) for item in supports if item))}</strong></div>"
        )
    if conflicts:
        blocks.append(
            f"<div><span>conflicts</span><strong>{html_text(' / '.join(str(item) for item in conflicts if item))}</strong></div>"
        )
    if not blocks:
        return ""
    return f"""
    <div class="turn-block">
      <div class="block-title">决策证据</div>
      <div class="keyvals compact">
        {''.join(blocks)}
      </div>
    </div>
    """


def render_visible_documents(turn: dict[str, Any]) -> str:
    response_body = turn.get("response_body") or {}
    content = response_body.get("content") or {}
    if not isinstance(content, dict):
        return '<div class="empty">未返回文档结果</div>'
    results = content.get("results") or []
    if not isinstance(results, list) or not results:
        return '<div class="empty">未返回文档结果</div>'
    visible_count = infer_visible_result_count(content)
    visible_results = results[:visible_count]
    if not visible_results:
        return '<div class="empty">未返回文档结果</div>'

    rows = []
    for idx, item in enumerate(visible_results, 1):
        filename = item.get("filename") or item.get("title") or item.get("file_id") or f"result_{idx}"
        score = item.get("score")
        meta = []
        if item.get("file_id"):
            meta.append(f"file_id={item['file_id']}")
        if score is not None:
            meta.append(f"score={score}")
        if item.get("ggzj_file_type"):
            meta.append(f"type={item['ggzj_file_type']}")
        rows.append(
            f"""
            <div class="doc-item">
              <div class="doc-rank">#{idx}</div>
              <div class="doc-main">
                <div class="doc-title">{html_text(filename)}</div>
                <div class="doc-meta">{html_text(' | '.join(meta))}</div>
              </div>
            </div>
            """
        )
    hidden_count = len(results) - len(visible_results)
    if hidden_count > 0:
        rows.append(f'<div class="doc-more">其余 {hidden_count} 条在前端折叠后可展开查看</div>')
    return "".join(rows)


def render_request_context(turn: dict[str, Any]) -> str:
    request_payload = turn.get("request_payload") or {}
    response_body = turn.get("response_body") or {}
    content = response_body.get("content") or {}
    request_kind = str(turn.get("request_kind") or "")

    items: list[str] = []
    if request_kind.startswith("initial_message"):
        initial_message = request_payload.get("message") or ""
        items.append(
            f'<div><span>initial_user_message</span><strong>{html_text(initial_message)}</strong></div>'
        )
    elif request_kind == "ask_user_resume":
        answer = ((request_payload.get("ask_user_answer") or {}).get("answer") or "")
        items.append(f'<div><span>resume_answer</span><strong>{html_text(answer)}</strong></div>')

    backend_query = content.get("query")
    if backend_query:
        items.append(f'<div><span>backend_query</span><strong>{html_text(backend_query)}</strong></div>')

    session_id = turn.get("session_id") or request_payload.get("session_id") or ""
    if session_id:
        items.append(f'<div><span>session_id</span><strong>{html_text(session_id)}</strong></div>')

    if not items:
        return ""
    return f"""
    <div class="turn-block">
      <div class="block-title">请求上下文</div>
      <div class="keyvals compact">
        {''.join(items)}
      </div>
    </div>
    """


def render_turn(turn: dict[str, Any], target_title: str) -> str:
    turn_index = turn.get("turn_index")
    request_kind = str(turn.get("request_kind") or "")
    response_type = str(turn.get("response_type") or "")
    ask_question = turn.get("ask_user_question")
    selected_label = turn.get("selected_option_label")
    user_decision_reason = resolve_user_decision_reason(turn)
    user_decision_source = turn.get("user_decision_source")
    user_decision_kind = str(turn.get("user_decision_kind") or "")
    user_stop_reason_code = str(turn.get("user_stop_reason_code") or "")
    user_decision_evidence = turn.get("user_decision_evidence") if isinstance(turn.get("user_decision_evidence"), dict) else {}
    response_body = turn.get("response_body") or {}
    content = response_body.get("content") or {}
    options = turn.get("clarify_options_snapshot") or []

    ask_block = ""
    if ask_question or options:
        option_html = "".join(render_option(option, selected_label) for option in options)
        ask_block = f"""
        <div class="turn-block">
          <div class="block-title">选项卡提问</div>
          <div class="ask-question">{html_text(ask_question or "未记录提问文案")}</div>
          <div class="option-list">{option_html or '<div class="empty">无选项快照</div>'}</div>
        </div>
        """

    user_block = ""
    if selected_label or turn.get("user_response_text") or user_decision_reason or user_stop_reason_code:
        user_block = f"""
        <div class="turn-block">
          <div class="block-title">模拟用户决策</div>
          <div class="keyvals">
            <div><span>决策类型</span><strong>{html_text(user_decision_kind)}</strong></div>
            <div><span>选择</span><strong>{html_text(selected_label or turn.get("user_response_text") or "")}</strong></div>
            <div><span>来源</span><strong>{html_text(user_decision_source or "")}</strong></div>
            <div><span>stop_reason_code</span><strong>{html_text(user_stop_reason_code)}</strong></div>
            <div><span>原因</span><strong>{html_text(user_decision_reason or "")}</strong></div>
          </div>
        </div>
        """

    result_block = ""
    if response_type == "documents":
        visible_summary = render_summary_summary(turn)
        result_block = f"""
        <div class="turn-block">
          <div class="block-title">最终文档返回</div>
          <div class="keyvals compact">
            <div><span>target_doc_title</span><strong>{html_text(target_title)}</strong></div>
            <div><span>summary</span><strong>{html_text(visible_summary or content.get('summary') or response_body.get('result_summary', {}).get('preview') or '')}</strong></div>
            <div><span>可见文档数</span><strong>{html_text(infer_visible_result_count(content))}</strong></div>
          </div>
          <div class="doc-list">{render_visible_documents(turn)}</div>
        </div>
        """
    elif response_type == "message":
        result_block = f"""
        <div class="turn-block">
          <div class="block-title">最终消息返回</div>
          <div class="message-result">{html_text(content.get('message') or content.get('text') or response_body.get('raw_summary') or response_body.get('summary') or '')}</div>
        </div>
        """

    return f"""
    <article class="turn-card">
      <div class="turn-head">
        <div class="turn-title">Turn {html_text(turn_index)} · {html_text(request_kind)} → {html_text(response_type)}</div>
        <div class="turn-meta">HTTP {html_text(turn.get('response_http_status'))} · stop={html_text(turn.get('stop_reason'))}</div>
      </div>
      {render_request_context(turn)}
      {ask_block}
      {user_block}
      {render_decision_evidence(user_decision_evidence)}
      {result_block}
    </article>
    """


def render_case_panel(
    result: dict[str, Any],
    fixture_case: dict[str, Any],
    base_dir: Path,
    active: bool,
) -> str:
    case_id = str(result.get("case_id") or "")
    question_text = str((result.get("input") or {}).get("question_text") or "")
    question_images = list((fixture_case.get("question_images") or []))
    profile = fixture_case.get("user_profile") or {}
    turns = (result.get("workflow") or {}).get("turns") or []
    analysis = result.get("analysis") or {}
    target_title = str((result.get("task_metadata") or {}).get("target_doc_title") or "")
    accepted_titles = (result.get("task_metadata") or {}).get("accepted_titles") or []
    final_status = str((result.get("response") or {}).get("final_status") or "")
    response_type = str((result.get("response") or {}).get("response_type") or "")
    section_class = "case-panel active" if active else "case-panel"
    turn_html = "".join(render_turn(turn, target_title) for turn in turns)
    facts_html = "".join(
        [
            render_fact_block("known_items", profile.get("known_items")),
            render_fact_block("uncertain_items", profile.get("uncertain_items")),
        ]
    )
    return f"""
    <section id="{html_text(case_id)}" class="{section_class}" data-case-id="{html_text(case_id)}">
      <div class="hero">
        <div>
          <div class="eyebrow">首次 attempt 全流程</div>
          <h1>{html_text(case_id)}</h1>
          <div class="question">{html_text(question_text)}</div>
        </div>
        <div class="hero-side">
          <div class="hero-chip">{html_text(str(result.get('input_modality') or ''))}</div>
          <div class="hero-chip">{html_text(final_status)}</div>
        </div>
      </div>

      <section class="card summary">
        <div class="summary-item">
          <div class="section-kicker">用户目标</div>
          <div>{html_text(profile.get('goal') or '')}</div>
        </div>
        <div class="summary-item">
          <div class="section-kicker">召回结果</div>
          <div>{'成功' if bool(analysis.get('final_hit')) else '失败'}</div>
        </div>
        <div class="summary-item">
          <div class="section-kicker">标准答案文档</div>
          <div>{html_text(target_title or (accepted_titles[0] if accepted_titles else ''))}</div>
        </div>
        <div class="summary-item">
          <div class="section-kicker">failure_reason</div>
          <div>{html_text(analysis.get('failure_reason') or '')}</div>
        </div>
        <div class="summary-item">
          <div class="section-kicker">response_type</div>
          <div>{html_text(response_type)}</div>
        </div>
        <div class="summary-item">
          <div class="section-kicker">turn_count</div>
          <div>{html_text(analysis.get('turn_count') or len(turns))}</div>
        </div>
      </section>

      <div class="fact-grid">{facts_html}</div>
      {render_question_images(question_images, base_dir)}

      <section class="timeline">
        <div class="section-title">完整流程</div>
        {turn_html}
      </section>
    </section>
    """


def build_html(results: list[dict[str, Any]], fixture_map: dict[str, dict[str, Any]], output_path: Path) -> str:
    nav_items: list[str] = []
    panels: list[str] = []
    for idx, result in enumerate(results):
        case_id = str(result.get("case_id") or "")
        fixture_case = fixture_map.get(case_id, {})
        active = idx == 0
        failure_reason = str((result.get("analysis") or {}).get("failure_reason") or "")
        nav_items.append(
            f"""
            <button class="nav-item{' active' if active else ''}" data-target="{html_text(case_id)}">
              <span class="nav-case">{html_text(case_id)}</span>
              <span class="nav-reason">{html_text(failure_reason or 'hit')}</span>
            </button>
            """
        )
        panels.append(render_case_panel(result, fixture_case, output_path.parent, active))

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>首次 attempt 审阅页</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f3f0e8;
      --panel: #fffdf8;
      --ink: #1b1b18;
      --muted: #6d6a61;
      --line: #d7cfbe;
      --accent: #b5542f;
      --accent-soft: #f2ddd2;
      --accent-deep: #8f3d20;
      --ok: #356859;
      --shadow: 0 10px 30px rgba(58, 47, 32, 0.08);
      --radius: 18px;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(181, 84, 47, 0.12), transparent 28%),
        linear-gradient(180deg, #f8f4eb 0%, var(--bg) 100%);
      color: var(--ink);
    }}
    .app {{
      display: grid;
      grid-template-columns: 300px minmax(0, 1fr);
      min-height: 100vh;
    }}
    .sidebar {{
      position: sticky;
      top: 0;
      height: 100vh;
      padding: 24px 18px;
      border-right: 1px solid var(--line);
      background: rgba(255, 253, 248, 0.92);
      backdrop-filter: blur(14px);
      overflow: auto;
    }}
    .brand {{
      margin-bottom: 18px;
    }}
    .brand h2 {{
      margin: 0 0 8px;
      font-size: 22px;
    }}
    .brand p {{
      margin: 0;
      line-height: 1.6;
      color: var(--muted);
      font-size: 13px;
    }}
    .nav {{
      display: grid;
      gap: 10px;
    }}
    .nav-item {{
      width: 100%;
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 14px;
      padding: 12px 14px;
      cursor: pointer;
      text-align: left;
      box-shadow: var(--shadow);
      transition: transform .14s ease, border-color .14s ease, background .14s ease;
    }}
    .nav-item:hover {{
      transform: translateY(-1px);
      border-color: var(--accent);
    }}
    .nav-item.active {{
      background: linear-gradient(180deg, #fff5ef 0%, #ffe8dc 100%);
      border-color: var(--accent);
    }}
    .nav-case {{
      display: block;
      font-weight: 700;
      margin-bottom: 4px;
      font-size: 13px;
    }}
    .nav-reason {{
      display: block;
      color: var(--muted);
      font-size: 12px;
    }}
    .main {{
      padding: 28px;
    }}
    .case-panel {{
      display: none;
      max-width: 1180px;
      margin: 0 auto;
    }}
    .case-panel.active {{
      display: block;
    }}
    .hero {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 18px;
      margin-bottom: 18px;
    }}
    .eyebrow {{
      color: var(--accent-deep);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: .08em;
      font-weight: 700;
      margin-bottom: 8px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 34px;
      line-height: 1.1;
    }}
    .question {{
      font-size: 16px;
      line-height: 1.65;
      color: var(--muted);
      max-width: 820px;
    }}
    .hero-side {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }}
    .hero-chip {{
      padding: 10px 12px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent-deep);
      border: 1px solid rgba(181, 84, 47, 0.18);
      font-size: 12px;
      font-weight: 700;
    }}
    .card, .turn-card, .fact-card {{
      background: rgba(255, 253, 248, 0.92);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
      padding: 18px;
      margin-bottom: 18px;
    }}
    .summary-item {{
      min-width: 0;
      line-height: 1.6;
    }}
    .section-kicker {{
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 8px;
      font-weight: 700;
    }}
    .fact-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px;
      margin-bottom: 18px;
    }}
    .fact-card {{
      padding: 16px;
    }}
    .fact-card ul {{
      margin: 0;
      padding-left: 18px;
      line-height: 1.7;
    }}
    .fact-card li {{
      margin-bottom: 4px;
    }}
    .card {{
      padding: 18px;
      margin-bottom: 18px;
    }}
    .section-title {{
      font-size: 20px;
      font-weight: 800;
      margin-bottom: 14px;
    }}
    .image-grid {{
      display: flex;
      flex-wrap: wrap;
      gap: 14px;
    }}
    .image-link img {{
      display: block;
      width: min(340px, 100%);
      border-radius: 14px;
      border: 1px solid var(--line);
      background: white;
    }}
    .timeline {{
      display: grid;
      gap: 16px;
    }}
    .turn-card {{
      padding: 18px;
    }}
    .turn-head {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: baseline;
      margin-bottom: 14px;
      border-bottom: 1px dashed var(--line);
      padding-bottom: 10px;
    }}
    .turn-title {{
      font-size: 18px;
      font-weight: 800;
    }}
    .turn-meta {{
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }}
    .turn-block {{
      margin-top: 14px;
    }}
    .block-title {{
      font-size: 13px;
      color: var(--accent-deep);
      font-weight: 800;
      margin-bottom: 10px;
    }}
    .ask-question {{
      font-size: 16px;
      margin-bottom: 10px;
      line-height: 1.6;
    }}
    .option-list {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }}
    .option-pill {{
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 12px;
      background: #fff;
    }}
    .option-pill.selected {{
      border-color: var(--accent);
      background: linear-gradient(180deg, #fff7f2 0%, #ffe8dc 100%);
    }}
    .option-head {{
      font-weight: 800;
      display: flex;
      justify-content: space-between;
      gap: 8px;
      align-items: center;
    }}
    .picked-badge {{
      display: inline-flex;
      align-items: center;
      padding: 3px 8px;
      border-radius: 999px;
      background: var(--accent);
      color: white;
      font-size: 11px;
      font-weight: 700;
    }}
    .option-desc {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.55;
    }}
    .keyvals {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
    }}
    .keyvals.compact {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}
    .keyvals div {{
      background: rgba(255,255,255,.74);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
      min-width: 0;
    }}
    .keyvals span {{
      display: block;
      color: var(--muted);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: .06em;
      margin-bottom: 6px;
    }}
    .keyvals strong {{
      display: block;
      line-height: 1.6;
      overflow-wrap: anywhere;
    }}
    .doc-list {{
      display: grid;
      gap: 10px;
      margin-top: 12px;
    }}
    .doc-more {{
      color: var(--muted);
      font-size: 12px;
      line-height: 1.6;
      padding: 4px 2px 0;
    }}
    .doc-item {{
      display: grid;
      grid-template-columns: 44px minmax(0, 1fr);
      gap: 12px;
      align-items: start;
      padding: 12px;
      border-radius: 14px;
      background: rgba(255,255,255,.72);
      border: 1px solid var(--line);
    }}
    .doc-rank {{
      width: 44px;
      height: 44px;
      border-radius: 12px;
      background: #efe6d6;
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: 800;
      color: var(--accent-deep);
    }}
    .doc-title {{
      font-weight: 700;
      line-height: 1.6;
      margin-bottom: 6px;
    }}
    .doc-meta {{
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }}
    .message-result, .empty {{
      padding: 14px;
      border-radius: 12px;
      background: rgba(255,255,255,.72);
      border: 1px dashed var(--line);
      line-height: 1.6;
      color: var(--muted);
    }}
    @media (max-width: 1100px) {{
      .app {{ grid-template-columns: 1fr; }}
      .sidebar {{
        position: static;
        height: auto;
        border-right: none;
        border-bottom: 1px solid var(--line);
      }}
      .summary {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .fact-grid {{ grid-template-columns: 1fr; }}
      .option-list {{ grid-template-columns: 1fr; }}
      .keyvals, .keyvals.compact {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 720px) {{
      .main {{ padding: 16px; }}
      .hero {{ flex-direction: column; }}
      .summary {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 28px; }}
      .turn-head {{ flex-direction: column; }}
    }}
  </style>
</head>
<body>
  <div class="app">
    <aside class="sidebar">
      <div class="brand">
        <h2>First Attempt Review</h2>
        <p>每个 case 只展示第一次 attempt，包含题面、用户已知信息、ask_user 选项卡、模拟用户选择与最终返回。</p>
      </div>
      <div class="nav">
        {''.join(nav_items)}
      </div>
    </aside>
    <main class="main">
      {''.join(panels)}
    </main>
  </div>
  <script>
    const navButtons = Array.from(document.querySelectorAll('.nav-item'));
    const panels = Array.from(document.querySelectorAll('.case-panel'));
    navButtons.forEach((button) => {{
      button.addEventListener('click', () => {{
        const target = button.getAttribute('data-target');
        navButtons.forEach((item) => item.classList.toggle('active', item === button));
        panels.forEach((panel) => panel.classList.toggle('active', panel.getAttribute('data-case-id') === target));
        window.scrollTo({{ top: 0, behavior: 'smooth' }});
      }});
    }});
  </script>
</body>
</html>
"""


def main() -> None:
    args = parse_args()
    report_path = resolve_report_path(args).resolve()
    fixture_path = (repo_root() / args.fixture).resolve()
    output_path = resolve_output_path(args, report_path).resolve()

    report = load_json(report_path)
    fixture = load_json(fixture_path)
    fixture_map = {
        str(case.get("case_id") or ""): case
        for case in fixture.get("cases", [])
        if isinstance(case, dict) and case.get("case_id")
    }

    results = [
        item
        for item in report.get("cases", [])
        if isinstance(item, dict) and int(item.get("attempt_index") or 0) == 1
    ]
    results.sort(key=lambda item: str(item.get("case_id") or ""))

    output_path.write_text(build_html(results, fixture_map, output_path), encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    main()

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from doc_search_bench.chat_export.render_first_attempt_review_html import build_html


def test_build_html_renders_user_persona_in_round_review(tmp_path: Path):
    html_output = build_html(
        [
            {
                "case_id": "case_persona_001",
                "attempt_index": 1,
                "layer": "atomic",
                "interaction_mode": "single_turn",
                "input_modality": "text",
                "page_goal_mode": "shadow",
                "input": {"question_text": "帮我找下这份资料"},
                "analysis": {"final_hit": True, "turn_count": 1, "failure_reason": ""},
                "response": {"final_status": "success_documents", "response_type": "documents"},
                "task_metadata": {
                    "target_doc_title": "资料A",
                    "accepted_titles": ["资料A"],
                    "target_doc_titles": ["资料A"],
                    "target_doc_count": 1,
                },
                "workflow": {"turns": []},
                "metrics": {"recall_hit": True, "hit_at_1": True, "hit_at_3": True, "mrr": 1.0},
            }
        ],
        {
            "case_persona_001": {
                "question_images": [],
                "user_profile": {
                    "persona": "term_confused",
                    "goal": "确认资料位置",
                    "known_items": ["资料A"],
                    "uncertain_items": ["具体页码"],
                },
            }
        },
        tmp_path / "round_case_review.html",
    )

    assert "用户类型" in html_output
    assert "term_confused" in html_output
    assert "确认资料位置" in html_output

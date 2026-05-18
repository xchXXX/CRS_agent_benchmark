from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import xlrd


REPO_ROOT = Path(__file__).resolve().parents[3]
TRAIN_DIR = Path(__file__).resolve().parents[1] / "envs" / "doc_search" / "data" / "train"
SOURCE_XLS = REPO_ROOT / "资料树节点及关联文件原数据表.xls"
SOURCE_FILE = SOURCE_XLS.relative_to(REPO_ROOT).as_posix()

BENCHMARK_SLUG = "search-docs-of-crs-agent"

LOW_INFORMATION_BRAND_TARGETS = {
    "东风": 49,
    "解放": 52,
}

VAGUE_KEYWORD_BRAND_TARGETS = {
    "东风": 49,
    "解放": 52,
}

NORMAL_BRAND_TARGETS = {
    "东风": 12,
    "解放": 12,
}

OBSOLETE_TRAIN_SUITE_IDS = [
    "mock_dongfeng_keyword_recall",
    "mock_jiefang_keyword_recall",
]

PERSONA_NOTES = {
    "cooperative_vague": "用户愿意配合澄清，但开口通常只报车型或常用简称，不会一开始给完整资料名。",
    "term_confused": "用户知道一些维修术语，但容易把 ECU、针脚、CAN、整车电路这类相近资料叫法混用。",
    "normal": "用户首轮会提供多个关键信息，但仍不会直接提供完整资料名、文件编号或页码。",
}

PERSONA_CORRECTION_STYLE = {
    "cooperative_vague": "delayed",
    "term_confused": "delayed",
    "normal": "immediate",
}

DOC_TYPE_RULES: list[tuple[str, str]] = [
    ("整车线束原理图", "线束图"),
    ("整车线束图解", "线束图"),
    ("整车线束图", "线束图"),
    ("驾驶室线束图", "驾驶室线束图"),
    ("左底盘线束图", "底盘线束图"),
    ("底盘线束图", "底盘线束图"),
    ("线束图解", "线束图"),
    ("线束原理图", "线束图"),
    ("线束图", "线束图"),
    ("整车电气原理图", "整车图"),
    ("整车电气图", "整车电气图"),
    ("整车原理图", "整车图"),
    ("整车电路图", "整车图"),
    ("整车图", "整车图"),
    ("ECU电路图", "ECU电路图"),
    ("ECU原理图", "ECU原理图"),
    ("保险盒定义", "保险盒定义"),
    ("针脚定义", "针脚定义"),
    ("CAN总线图", "CAN总线图"),
    ("气路图", "气路图"),
    ("电气原理图", "原理图"),
    ("电路图", "电路图"),
    ("原理图", "原理图"),
]

DOC_QUERY_ALIASES = {
    "整车图": ["整车电路图", "全车电路图"],
    "整车电气图": ["整车电路图", "整车图"],
    "线束图": ["整车线束图", "线路图"],
    "驾驶室线束图": ["驾驶室线路图", "驾驶室线束图解"],
    "底盘线束图": ["底盘线路图", "底盘线束图解"],
    "ECU电路图": ["ECU原理图", "ECU线路图"],
    "ECU原理图": ["ECU电路图", "ECU线路图"],
    "保险盒定义": ["保险盒说明", "保险盒图"],
    "针脚定义": ["针脚图", "PIN图"],
    "CAN总线图": ["CAN图", "总线图"],
    "气路图": ["气路原理图", "气路图"],
    "电路图": ["线路图", "电器原理图"],
    "原理图": ["电路图", "线路原理图"],
}

TERM_CONFUSION_QUERY_TERMS = {
    "整车图": ["整车电路图", "全车线路图", "CAN电路图"],
    "整车电气图": ["整车电路图", "整车线路图", "全车电气图"],
    "线束图": ["线路图", "整车电路图", "线束原理图"],
    "驾驶室线束图": ["驾驶室线路图", "驾驶室电路图", "驾驶室线束图解"],
    "底盘线束图": ["底盘线路图", "底盘电路图", "底盘线束图解"],
    "ECU电路图": ["ECU针脚图", "ECU原理图", "ECU CAN图"],
    "ECU原理图": ["ECU电路图", "ECU针脚图", "控制器CAN图"],
    "保险盒定义": ["保险盒针脚图", "保险盒电路图", "保险盒说明"],
    "针脚定义": ["ECU针脚图", "插头定义", "PIN图"],
    "CAN总线图": ["CAN针脚图", "CAN线路图", "整车CAN图"],
    "气路图": ["气路原理图", "气路线路图"],
    "电路图": ["线路图", "原理图", "整车电路图"],
    "原理图": ["电路图", "线路原理图", "整车原理图"],
}

COMPONENT_KEYWORDS = [
    "ECU",
    "VECU",
    "BCM",
    "ABS",
    "EBS",
    "ESC",
    "AEBS",
    "CAN",
    "ECAS",
    "DCU",
    "ACM",
    "EMS",
    "后处理",
]

SUBJECT_STOPWORDS = {
    "推荐",
    "原厂图",
    "原厂",
    "国二",
    "国三",
    "国四",
    "国五",
    "国六",
    "国四国五",
    "国五国六",
    "天然气",
    "电路",
    "线路",
    "原理",
    "图",
    "整车",
    "线束",
}

DOC_TYPE_PRIORITY = [
    "整车图",
    "线束图",
    "驾驶室线束图",
    "底盘线束图",
    "电路图",
    "原理图",
    "整车电气图",
    "ECU电路图",
    "ECU原理图",
    "针脚定义",
    "保险盒定义",
    "CAN总线图",
    "气路图",
]


@dataclass
class SourceRow:
    row_number: int
    layer_path: str
    brand: str
    file_id: str | None
    title: str
    file_type: str | None
    doc_type: str


@dataclass
class GeneratedCaseSpec:
    case_id: str
    row: SourceRow
    persona: str


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clean_title(title: str) -> str:
    return title.replace("【推荐】", "").replace(".PDF", "").strip()


def strip_brackets(title: str) -> str:
    return clean_title(title).split("【", 1)[0].strip()


def detect_doc_type(title: str) -> str | None:
    normalized = clean_title(title)
    for marker, label in DOC_TYPE_RULES:
        if marker in normalized:
            return label
    return None


def extract_brand(layer_path: str) -> str:
    parts = [part.strip() for part in layer_path.split("->") if part.strip()]
    return parts[2] if len(parts) > 2 else ""


def load_source_rows() -> list[SourceRow]:
    workbook = xlrd.open_workbook(file_contents=SOURCE_XLS.read_bytes())
    sheet = workbook.sheet_by_index(0)
    rows: list[SourceRow] = []
    for row_idx in range(1, sheet.nrows):
        layer_path = str(sheet.cell_value(row_idx, 2)).strip()
        brand = extract_brand(layer_path)
        title = str(sheet.cell_value(row_idx, 14)).strip()
        if not title or not brand:
            continue
        doc_type = detect_doc_type(title)
        if doc_type is None:
            continue
        file_id = str(sheet.cell_value(row_idx, 13)).strip() or None
        file_type = str(sheet.cell_value(row_idx, 15)).strip() or None
        rows.append(
            SourceRow(
                row_number=row_idx + 1,
                layer_path=layer_path,
                brand=brand,
                file_id=file_id,
                title=title,
                file_type=file_type,
                doc_type=doc_type,
            )
        )
    return rows


def canonical_title_key(title: str) -> str:
    base = strip_brackets(title)
    return re.sub(r"[_\s\-./]+", "", base).lower()


def tokenize_subject_segment(segment: str) -> list[str]:
    token_pattern = re.compile(r"\d+x\d+|[A-Za-z]+[\dA-Za-z./-]*|[\u4e00-\u9fff]+")
    return token_pattern.findall(segment)


def extract_subject_tokens(row: SourceRow) -> list[str]:
    text = strip_brackets(row.title)
    for marker, _label in DOC_TYPE_RULES:
        if marker in text:
            text = text.replace(marker, " ")
            break
    text = text.replace("一汽解放", "解放").replace("东风_", "东风 ").replace("解放_", "解放 ")
    if row.brand in text:
        text = text.replace(row.brand, " ", 1)
    text = re.sub(r"[()（）【】]", " ", text)
    text = re.sub(r"[_\s]+", " ", text).strip()

    tokens: list[str] = []
    for segment in text.split(" "):
        for token in tokenize_subject_segment(segment):
            cleaned = token.strip("._-/")
            if not cleaned or cleaned in SUBJECT_STOPWORDS:
                continue
            if cleaned not in tokens:
                tokens.append(cleaned)
    return tokens


def extract_component_tokens(text: str) -> list[str]:
    found: list[str] = []
    upper_text = text.upper()
    for token in COMPONENT_KEYWORDS:
        token_upper = token.upper()
        if token_upper in upper_text and token not in found:
            found.append(token)
    return found


def build_primary_query(row: SourceRow) -> str:
    subject_tokens = extract_subject_tokens(row)
    doc_type = row.doc_type
    query_tokens: list[str] = [row.brand]
    query_tokens.extend(subject_tokens[:3])
    if doc_type not in query_tokens:
        query_tokens.append(doc_type)
    return " ".join(token for token in query_tokens if token).strip()


def build_low_information_query(row: SourceRow) -> str:
    subject_tokens = extract_subject_tokens(row)
    query_tokens = subject_tokens[:2]
    if len(query_tokens) < 2 and row.doc_type not in query_tokens:
        query_tokens.append(row.doc_type)
    if not query_tokens:
        query_tokens = [row.brand, row.doc_type]
    return " ".join(token for token in query_tokens if token).strip()


def build_term_confused_query(row: SourceRow) -> str:
    subject_tokens = extract_subject_tokens(row)
    confused_terms = TERM_CONFUSION_QUERY_TERMS.get(row.doc_type) or DOC_QUERY_ALIASES.get(row.doc_type) or [row.doc_type]
    doc_term = confused_terms[0]
    query_tokens: list[str] = [row.brand]
    query_tokens.extend(subject_tokens[:2])
    components = extract_component_tokens(row.title)
    if components:
        query_tokens.append(components[0])
    query_tokens.append(doc_term)
    return " ".join(token for token in query_tokens if token).strip()


def build_normal_query(row: SourceRow) -> str:
    return build_primary_query(row)


def is_informative_normal_row(row: SourceRow) -> bool:
    return len(build_normal_query(row).split()) >= 3


def build_alternate_queries(primary_query: str, row: SourceRow) -> list[str]:
    subject_tokens = extract_subject_tokens(row)
    brand_aliases = [row.brand]
    if row.brand == "解放":
        brand_aliases.append("一汽解放")
    base_subject = " ".join(subject_tokens[:3]).strip()
    candidates: list[str] = []
    for doc_alias in [row.doc_type, *DOC_QUERY_ALIASES.get(row.doc_type, [])]:
        parts = [row.brand, base_subject, doc_alias]
        candidates.append(" ".join(part for part in parts if part).strip())
        if base_subject:
            candidates.append(" ".join([base_subject, doc_alias]).strip())
        for brand_alias in brand_aliases:
            parts = [brand_alias, base_subject, doc_alias]
            candidates.append(" ".join(part for part in parts if part).strip())
    if subject_tokens:
        candidates.append(" ".join([row.brand, subject_tokens[0], row.doc_type]).strip())

    alternates: list[str] = []
    seen = {primary_query}
    for item in candidates:
        cleaned = re.sub(r"\s+", " ", item).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        alternates.append(cleaned)
        if len(alternates) >= 8:
            break
    return alternates


def build_alias_mapping(doc_type: str, brand: str) -> dict[str, list[str]]:
    aliases: dict[str, list[str]] = {}
    if doc_type in DOC_QUERY_ALIASES:
        aliases[doc_type] = DOC_QUERY_ALIASES[doc_type]
    if brand == "解放":
        aliases["解放"] = ["一汽解放"]
    return aliases


def build_positive_user_profile(row: SourceRow, primary_query: str, persona: str) -> dict[str, Any]:
    subject_tokens = extract_subject_tokens(row)
    components = extract_component_tokens(row.title)
    chinese_tokens = [token for token in subject_tokens if re.search(r"[\u4e00-\u9fff]", token)]
    model_tokens = [token for token in subject_tokens if re.search(r"[A-Za-z0-9]", token)]

    known_facts: dict[str, list[str]] = {"brand": [row.brand]}
    if chinese_tokens:
        known_facts["series"] = chinese_tokens[:2]
    if model_tokens:
        known_facts["model"] = model_tokens[:3]
    known_facts["doc_type"] = [row.doc_type, *DOC_QUERY_ALIASES.get(row.doc_type, [])[:2]]
    if components:
        known_facts["component"] = components[:3]

    uncertain_facts: dict[str, list[str]] = {}
    if persona == "term_confused":
        confused_terms = [
            term
            for term in TERM_CONFUSION_QUERY_TERMS.get(row.doc_type, [])
            if term not in known_facts["doc_type"]
        ]
        if confused_terms:
            uncertain_facts["doc_type"] = confused_terms[:3]
    if persona != "normal" and row.brand == "解放" and len(model_tokens) > 1:
        uncertain_facts["model"] = model_tokens[1:3]

    return {
        "persona": persona,
        "goal": f"找{primary_query}相关资料",
        "known_facts": known_facts,
        "uncertain_facts": uncertain_facts,
        "unknown_facts": ["file_id", "full_title", "page"],
        "aliases": build_alias_mapping(row.doc_type, row.brand),
        "correction_style": PERSONA_CORRECTION_STYLE.get(persona, "immediate"),
        "notes": PERSONA_NOTES.get(persona),
    }


def select_rows_for_suite(
    rows: list[SourceRow],
    brand: str,
    target_count: int,
    excluded_title_keys: set[str] | None = None,
) -> list[SourceRow]:
    excluded_title_keys = excluded_title_keys or set()
    candidates = [row for row in rows if row.brand == brand]
    buckets: dict[str, list[SourceRow]] = {key: [] for key in DOC_TYPE_PRIORITY}
    leftovers: list[SourceRow] = []
    seen_full_titles: set[str] = set()

    for row in candidates:
        if row.title in seen_full_titles:
            continue
        seen_full_titles.add(row.title)
        if canonical_title_key(row.title) in excluded_title_keys:
            continue
        if excluded_title_keys and not is_informative_normal_row(row):
            continue
        if row.doc_type in buckets:
            buckets[row.doc_type].append(row)
        else:
            leftovers.append(row)

    selected: list[SourceRow] = []
    used_keys: set[str] = set()

    while len(selected) < target_count:
        progressed = False
        for doc_type in DOC_TYPE_PRIORITY:
            bucket = buckets[doc_type]
            while bucket:
                row = bucket.pop(0)
                title_key = canonical_title_key(row.title)
                if title_key in used_keys:
                    continue
                used_keys.add(title_key)
                selected.append(row)
                progressed = True
                break
            if len(selected) >= target_count:
                break
        if not progressed:
            break

    if len(selected) < target_count:
        for row in leftovers + candidates:
            title_key = canonical_title_key(row.title)
            if title_key in used_keys:
                continue
            used_keys.add(title_key)
            selected.append(row)
            if len(selected) >= target_count:
                break

    if len(selected) < target_count:
        raise RuntimeError(f"not enough rows for {brand}: expected {target_count}, got {len(selected)}")

    return selected[:target_count]


def build_positive_suite(
    *,
    suite_id: str,
    case_specs: list[GeneratedCaseSpec],
    query_builder,
    notes: str,
    required_ask_user_rounds: int,
    acceptance_threshold: float = 0.85,
) -> tuple[dict[str, Any], dict[str, Any]]:
    fixture_cases: list[dict[str, Any]] = []
    gold_cases: list[dict[str, Any]] = []

    for spec in case_specs:
        row = spec.row
        case_id = spec.case_id
        primary_query = query_builder(row)
        alternates = build_alternate_queries(primary_query, row)

        fixture_case = {
            "case_id": case_id,
            "layer": "atomic",
            "input_modality": "text",
            "question_text": primary_query,
            "question_images": [],
            "vehicle_info": None,
            "benchmark_track": "chat_completions",
            "preprocess_strategy": "none",
            "request_context": {},
            "notes": notes,
            "alternate_queries": alternates,
            "source_file": SOURCE_FILE,
            "interaction_mode": "multi_turn",
            "max_turns": 8,
            "case_repeat_count": 5,
            "initial_user_message": primary_query,
            "user_simulation_config": {
                "driver": "ai",
                "scenario": "normal",
                "wrong_selection_budget": 0,
                "rollback_intent_mode": "none",
                "rollback_min_round_gap": 0,
            },
            "user_profile": build_positive_user_profile(row, primary_query, spec.persona),
        }
        fixture_cases.append(fixture_case)

        gold_case = {
            "case_id": case_id,
            "layer": "atomic",
            "accepted_titles": [row.title],
            "preferred_title": row.title,
            "expected_response_type": "documents",
            "top_k": 10,
            "source_file": SOURCE_FILE,
            "page_goal_mode": "disabled",
            "accepted_pages": [],
            "accepted_page_ranges": [],
            "target_doc": {
                "file_id": row.file_id or row.title,
                "title": row.title,
                "facets": {},
            },
            "required_ask_user_rounds": required_ask_user_rounds,
        }
        gold_cases.append(gold_case)

    fixture_blob = {
        "benchmark_slug": BENCHMARK_SLUG,
        "suite_id": suite_id,
        "layer": "atomic",
        "source_files": [SOURCE_FILE],
        "case_count": len(fixture_cases),
        "cases": fixture_cases,
    }
    gold_blob = {
        "benchmark_slug": BENCHMARK_SLUG,
        "suite_id": suite_id,
        "layer": "atomic",
        "source_files": [SOURCE_FILE],
        "acceptance_threshold": acceptance_threshold,
        "case_count": len(gold_cases),
        "cases": gold_cases,
    }
    return fixture_blob, gold_blob


def contains_all_tokens(title: str, tokens: list[str]) -> bool:
    normalized = title.upper()
    for token in tokens:
        if re.search(r"[A-Za-z0-9]", token):
            if token.upper() not in normalized:
                return False
        elif token not in title:
            return False
    return True


def mutate_model_token(token: str) -> str:
    match = re.search(r"(\d+)(?!.*\d)", token)
    if match:
        number = match.group(1)
        replacement = str((int(number) + 7) % (10 ** len(number))).zfill(len(number))
        return token[: match.start(1)] + replacement + token[match.end(1) :]
    if re.search(r"[A-Za-z]", token):
        return f"{token}X"
    return f"{token}改"


def build_negative_candidates(
    positive_rows: list[SourceRow],
    all_titles: list[str],
) -> list[str]:
    negatives: list[str] = []
    seen: set[str] = set()

    for row in positive_rows:
        wrong_brand = "解放" if row.brand == "东风" else "东风"
        subject_tokens = extract_subject_tokens(row)
        model_tokens = [token for token in subject_tokens if re.search(r"[A-Za-z0-9]", token)]
        if model_tokens:
            mutated_subject = [
                mutate_model_token(token) if token == model_tokens[0] else token for token in subject_tokens[:3]
            ]
        else:
            mutated_subject = subject_tokens[:3]
        query_tokens = [wrong_brand, *mutated_subject, row.doc_type]
        query = " ".join(token for token in query_tokens if token).strip()
        required_tokens = [wrong_brand, *(mutated_subject[:2] or subject_tokens[:2]), row.doc_type]
        if not query or query in seen:
            continue
        if any(contains_all_tokens(title, required_tokens) for title in all_titles):
            continue
        seen.add(query)
        negatives.append(query)
        if len(negatives) >= 6:
            break

    if len(negatives) < 6:
        raise RuntimeError(f"unable to generate enough synthetic negatives, got {len(negatives)}")
    return negatives


def build_negative_user_profile(query: str) -> dict[str, Any]:
    tokens = query.split()
    brand = tokens[0] if tokens else "未知品牌"
    model_tokens = [token for token in tokens[1:] if re.search(r"[A-Za-z0-9]", token)]
    series_tokens = [
        token
        for token in tokens[1:]
        if re.search(r"[\u4e00-\u9fff]", token)
        and token not in DOC_QUERY_ALIASES
        and token not in DOC_TYPE_PRIORITY
        and token not in SUBJECT_STOPWORDS
    ]
    components = extract_component_tokens(query)
    doc_type = next((token for token in tokens if token in DOC_QUERY_ALIASES or token in DOC_TYPE_PRIORITY), "电路图")

    known_facts: dict[str, list[str]] = {"brand": [brand]}
    if series_tokens:
        known_facts["series"] = series_tokens[:2]
    if model_tokens:
        known_facts["model"] = model_tokens[:2]
    known_facts["doc_type"] = [doc_type, *DOC_QUERY_ALIASES.get(doc_type, [])[:2]]
    if components:
        known_facts["component"] = components[:2]

    return {
        "persona": "normal",
        "goal": f"找{query}相关资料",
        "known_facts": known_facts,
        "uncertain_facts": {},
        "unknown_facts": ["file_id", "full_title", "page"],
        "aliases": build_alias_mapping(doc_type, brand),
        "correction_style": "immediate",
        "notes": "用户按自己记忆提供了品牌和型号，但资料库里可能并不存在对应文件。",
    }


def build_negative_suite(
    *,
    positive_rows: list[SourceRow],
    all_titles: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    negative_queries = build_negative_candidates(positive_rows, all_titles)
    fixture_cases: list[dict[str, Any]] = []
    gold_cases: list[dict[str, Any]] = []
    for index, query in enumerate(negative_queries, start=1):
        case_id = f"noise_{index:04d}"
        fixture_case = {
            "case_id": case_id,
            "layer": "atomic",
            "input_modality": "text",
            "question_text": query,
            "question_images": [],
            "vehicle_info": None,
            "benchmark_track": "chat_completions",
            "preprocess_strategy": "none",
            "request_context": {},
            "notes": "synthetic negative query derived from xls-only title mutation",
            "source_file": SOURCE_FILE,
            "interaction_mode": "multi_turn",
            "max_turns": 8,
            "case_repeat_count": 5,
            "initial_user_message": query,
            "user_simulation_config": {
                "driver": "ai",
                "scenario": "normal",
                "wrong_selection_budget": 0,
                "rollback_intent_mode": "none",
                "rollback_min_round_gap": 0,
            },
            "user_profile": build_negative_user_profile(query),
        }
        fixture_cases.append(fixture_case)

        gold_case = {
            "case_id": case_id,
            "layer": "atomic",
            "accepted_titles": [],
            "preferred_title": None,
            "expected_response_type": "message_or_empty",
            "top_k": 10,
            "page_goal_mode": "disabled",
            "accepted_pages": [],
            "accepted_page_ranges": [],
            "target_doc": {
                "file_id": None,
                "title": None,
                "doc_path": None,
                "facets": {},
            },
            "required_ask_user_rounds": 1,
        }
        gold_cases.append(gold_case)

    fixture_blob = {
        "benchmark_slug": BENCHMARK_SLUG,
        "suite_id": "synthetic_noise_queries",
        "layer": "atomic",
        "source_files": [SOURCE_FILE],
        "case_count": len(fixture_cases),
        "cases": fixture_cases,
    }
    gold_blob = {
        "benchmark_slug": BENCHMARK_SLUG,
        "suite_id": "synthetic_noise_queries",
        "layer": "atomic",
        "source_files": [SOURCE_FILE],
        "acceptance_threshold": 1.0,
        "case_count": len(gold_cases),
        "cases": gold_cases,
    }
    return fixture_blob, gold_blob


def iter_interleaved_brand_rows(rows_by_brand: dict[str, list[SourceRow]]):
    max_count = max((len(items) for items in rows_by_brand.values()), default=0)
    for index in range(max_count):
        for brand, rows in rows_by_brand.items():
            if index < len(rows):
                yield brand, index + 1, rows[index]


def build_low_information_case_specs(rows_by_brand: dict[str, list[SourceRow]]) -> list[GeneratedCaseSpec]:
    specs: list[GeneratedCaseSpec] = []
    for index, (_brand, _brand_index, row) in enumerate(iter_interleaved_brand_rows(rows_by_brand), start=1):
        specs.append(GeneratedCaseSpec(case_id=f"low_info_{index:04d}", row=row, persona="cooperative_vague"))
    return specs


def build_vague_keyword_case_specs(rows_by_brand: dict[str, list[SourceRow]]) -> list[GeneratedCaseSpec]:
    specs: list[GeneratedCaseSpec] = []
    for index, (_brand, _brand_index, row) in enumerate(iter_interleaved_brand_rows(rows_by_brand), start=1):
        specs.append(GeneratedCaseSpec(case_id=f"vague_keyword_{index:04d}", row=row, persona="term_confused"))
    return specs


def build_normal_case_specs(rows_by_brand: dict[str, list[SourceRow]]) -> list[GeneratedCaseSpec]:
    specs: list[GeneratedCaseSpec] = []
    for index, (_brand, _brand_index, row) in enumerate(iter_interleaved_brand_rows(rows_by_brand), start=1):
        specs.append(GeneratedCaseSpec(case_id=f"normal_{index:04d}", row=row, persona="normal"))
    return specs


def remove_obsolete_brand_suites() -> None:
    for suite_id in OBSOLETE_TRAIN_SUITE_IDS:
        for suffix in ("fixture", "gold"):
            path = TRAIN_DIR / f"{suite_id}.{suffix}.json"
            if path.exists():
                path.unlink()


def regenerate_train() -> None:
    rows = load_source_rows()
    all_titles = [row.title for row in rows]

    low_information_rows_by_brand = {
        brand: select_rows_for_suite(rows=rows, brand=brand, target_count=target_count)
        for brand, target_count in LOW_INFORMATION_BRAND_TARGETS.items()
    }
    used_low_information_title_keys = {
        canonical_title_key(row.title)
        for selected_rows in low_information_rows_by_brand.values()
        for row in selected_rows
    }
    vague_keyword_rows_by_brand = {
        brand: select_rows_for_suite(
            rows=rows,
            brand=brand,
            target_count=target_count,
            excluded_title_keys=used_low_information_title_keys,
        )
        for brand, target_count in VAGUE_KEYWORD_BRAND_TARGETS.items()
    }
    used_positive_title_keys = {
        canonical_title_key(row.title)
        for selected_rows in [*low_information_rows_by_brand.values(), *vague_keyword_rows_by_brand.values()]
        for row in selected_rows
    }
    normal_rows_by_brand = {
        brand: select_rows_for_suite(
            rows=rows,
            brand=brand,
            target_count=target_count,
            excluded_title_keys=used_positive_title_keys,
        )
        for brand, target_count in NORMAL_BRAND_TARGETS.items()
    }

    low_information_fixture, low_information_gold = build_positive_suite(
        suite_id="low_information_opening",
        case_specs=build_low_information_case_specs(low_information_rows_by_brand),
        query_builder=build_low_information_query,
        notes="mock low-information opening case derived from xls title; initial user message contains only 1-2 visible facts while user_profile keeps medium private knowledge",
        required_ask_user_rounds=1,
    )
    write_json(TRAIN_DIR / "low_information_opening.fixture.json", low_information_fixture)
    write_json(TRAIN_DIR / "low_information_opening.gold.json", low_information_gold)

    vague_fixture, vague_gold = build_positive_suite(
        suite_id="vague_keyword_recall",
        case_specs=build_vague_keyword_case_specs(vague_keyword_rows_by_brand),
        query_builder=build_term_confused_query,
        notes="mock vague keyword recall case derived from xls title; user mixes adjacent repair terms such as ECU, pins, CAN and whole-vehicle circuit docs",
        required_ask_user_rounds=1,
    )
    write_json(TRAIN_DIR / "vague_keyword_recall.fixture.json", vague_fixture)
    write_json(TRAIN_DIR / "vague_keyword_recall.gold.json", vague_gold)

    normal_fixture, normal_gold = build_positive_suite(
        suite_id="normal_informative_queries",
        case_specs=build_normal_case_specs(normal_rows_by_brand),
        query_builder=build_normal_query,
        notes="mock normal informative query derived from xls title; initial user message contains multiple facts but not the full file title",
        required_ask_user_rounds=0,
    )
    write_json(TRAIN_DIR / "normal_informative_queries.fixture.json", normal_fixture)
    write_json(TRAIN_DIR / "normal_informative_queries.gold.json", normal_gold)

    positive_seed_rows = low_information_rows_by_brand["东风"][:3] + low_information_rows_by_brand["解放"][:3]
    new_noise_fixture, new_noise_gold = build_negative_suite(
        positive_rows=positive_seed_rows,
        all_titles=all_titles,
    )
    write_json(TRAIN_DIR / "synthetic_noise_queries.fixture.json", new_noise_fixture)
    write_json(TRAIN_DIR / "synthetic_noise_queries.gold.json", new_noise_gold)

    remove_obsolete_brand_suites()


def main() -> int:
    regenerate_train()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

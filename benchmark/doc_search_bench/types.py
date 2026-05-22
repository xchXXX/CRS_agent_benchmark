from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


RESPOND_ACTION_NAME = "respond"
RESPOND_ACTION_FIELD_NAME = "content"
DEFAULT_STOP_TOKEN = "###STOP###"
DEFAULT_CASE_REPEAT_COUNT = 5
ALLOWED_USER_SIMULATION_SCENARIOS = {"normal", "cooperative_vague", "term_confused", "image_parsing_required"}
ALLOWED_USER_PERSONAS = {"normal", "cooperative_vague", "term_confused"}
ALLOWED_CORRECTION_STYLES = {"immediate", "delayed"}


@dataclass(frozen=True)
class Action:
    name: str
    kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UserSimulationConfig:
    driver: str = "ai"
    scenario: str = "normal"
    rollback_intent_mode: str = "none"
    rollback_min_round_gap: int = 0
    notes: str | None = None


@dataclass(frozen=True)
class UserProfile:
    persona: str | None = None
    goal: str | None = None
    known_items: list[str] = field(default_factory=list)
    uncertain_items: list[str] = field(default_factory=list)
    aliases: dict[str, list[str]] = field(default_factory=dict)
    correction_style: str | None = None
    notes: str | None = None


RegionBox = tuple[float, float, float, float]


@dataclass(frozen=True)
class AcceptedRegionGroup:
    group_id: str | None = None
    page_number: int | None = None
    label: str | None = None
    boxes_norm: list[RegionBox] = field(default_factory=list)
    match_mode: str = "any_box"


@dataclass(frozen=True)
class RegionPageBoxes:
    page_number: int | None = None
    boxes: list[RegionBox] = field(default_factory=list)


@dataclass(frozen=True)
class TargetDocumentTruth:
    file_id: str | None = None
    title: str | None = None
    doc_path: str | None = None
    facets: dict[str, str] = field(default_factory=dict)
    accepted_pages: list[int] = field(default_factory=list)
    accepted_page_ranges: list[tuple[int, int]] = field(default_factory=list)
    locator_keywords: list[str] = field(default_factory=list)
    accepted_region_groups: list[AcceptedRegionGroup] = field(default_factory=list)


@dataclass(frozen=True)
class TaskCase:
    case_id: str
    split: str
    layer: str
    suite_id: str
    input_modality: str
    question_text: str
    question_images: list[str]
    vehicle_info: str | None
    preprocess_strategy: str
    benchmark_track: str
    request_context: dict[str, Any]
    accepted_titles: list[str]
    preferred_title: str | None
    user_id: str
    instruction: str
    actions: list[Action] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    expected_response_type: str = "documents"
    required_ask_user_rounds: int = 0
    top_k: int = 10
    interaction_mode: str = "multi_turn"
    initial_user_message: str | None = None
    max_turns: int = 8
    case_repeat_count: int = DEFAULT_CASE_REPEAT_COUNT
    stop_tokens: list[str] = field(default_factory=lambda: [DEFAULT_STOP_TOKEN])
    user_simulation_config: UserSimulationConfig = field(default_factory=UserSimulationConfig)
    page_goal_mode: str = "disabled"
    accepted_pages: list[int] = field(default_factory=list)
    accepted_page_ranges: list[tuple[int, int]] = field(default_factory=list)
    notes: str | None = None
    source_files: list[str] = field(default_factory=list)
    question_type: str | None = None
    teacher_reply: str | None = None
    benchmark_track_label: str | None = None
    legacy_source_split: str | None = None
    legacy_source_layer: str | None = None
    user_profile: UserProfile | None = None
    target_doc: TargetDocumentTruth | None = None
    target_docs: list[TargetDocumentTruth] = field(default_factory=list)
    target_match_mode: str = "any_of"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskSuite:
    split: str
    suite_id: str
    layer: str
    acceptance_threshold: float
    source_files: list[str]
    cases: list[TaskCase]
    legacy_source_split: str | None = None


@dataclass(frozen=True)
class RunConfig:
    split: str
    base_url: str
    app_token: str | None
    timeout_ms: int
    top_k: int
    request_mode: str
    max_attempts_per_case: int | None
    user_strategy: str
    user_model: str | None
    user_provider: str | None
    output_prefix: str
    suite_filters: list[str] = field(default_factory=list)
    case_filters: list[str] = field(default_factory=list)
    threshold_override: float | None = None


@dataclass
class PredictedDocument:
    rank: int
    doc_title: str
    doc_path: str
    score: float | None = None
    page_numbers: list[int] = field(default_factory=list)
    body_search: dict[str, Any] = field(default_factory=dict)
    body_search_status: str | None = None
    body_search_best_page: int | None = None
    body_search_top_pages: list[int] = field(default_factory=list)
    body_search_viewer_token_present: bool | None = None
    body_search_preview_present: bool | None = None
    locator_status: str | None = None
    locator_best_page: int | None = None
    locator_top_pages: list[int] = field(default_factory=list)
    locator_viewer_token_present: bool | None = None
    locator_preview_present: bool | None = None
    coord_predicted_page_numbers: list[int] = field(default_factory=list)
    coord_predicted_boxes_px: list[RegionPageBoxes] = field(default_factory=list)
    coord_predicted_boxes_norm: list[RegionPageBoxes] = field(default_factory=list)
    coord_viewer_token: str | None = None
    coord_metadata_present: bool | None = None


@dataclass
class ExecutionRecord:
    run_id: str
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: float | None = None
    endpoint: str | None = None
    session_id: str | None = None
    http_status: int | None = None


@dataclass
class ResponseRecord:
    response_type: str = ""
    final_status: str = ""
    business: str = "DOC_SEARCH"
    raw_summary: str | None = None


@dataclass
class PredictionRecord:
    top_k_documents: list[PredictedDocument] = field(default_factory=list)
    predicted_pages: list[int] = field(default_factory=list)
    page_confidence: float | None = None
    locator_source: str | None = None
    body_search_status: str | None = None
    body_search_best_page: int | None = None
    body_search_top_pages: list[int] = field(default_factory=list)
    body_search_viewer_token_present: bool | None = None
    body_search_preview_present: bool | None = None
    locator_status: str | None = None
    locator_best_page: int | None = None
    locator_top_pages: list[int] = field(default_factory=list)
    locator_viewer_token_present: bool | None = None
    locator_preview_present: bool | None = None
    coord_predicted_page_numbers: list[int] = field(default_factory=list)
    coord_predicted_boxes_px: list[RegionPageBoxes] = field(default_factory=list)
    coord_predicted_boxes_norm: list[RegionPageBoxes] = field(default_factory=list)
    coord_viewer_token: str | None = None
    coord_metadata_present: bool | None = None


@dataclass
class BenchmarkTurnRecord:
    turn_index: int
    request_kind: str
    request_payload: dict[str, Any] = field(default_factory=dict)
    response_http_status: int | None = None
    response_body: dict[str, Any] | None = None
    response_type: str = ""
    session_id: str | None = None
    business: str | None = None
    tool_call_id: str | None = None
    ask_user_question: str | None = None
    clarify_options_snapshot: list[dict[str, Any]] = field(default_factory=list)
    selected_option_key: str | None = None
    selected_option_label: str | None = None
    selected_selection_payload: dict[str, Any] = field(default_factory=dict)
    user_decision_source: str | None = None
    user_decision_kind: str | None = None
    user_decision_reason: str | None = None
    user_stop_reason_code: str | None = None
    user_decision_evidence: dict[str, Any] = field(default_factory=dict)
    user_response_text: str | None = None
    rollback_intent_mode: str | None = None
    rollback_target_round: int | None = None
    rollback_supported: bool | None = None
    capability_gap: str | None = None
    is_terminal: bool = False
    stop_reason: str | None = None


@dataclass
class WorkflowRecord:
    planned_queries: list[str] = field(default_factory=list)
    used_image_context: bool = False
    ask_user_rounds: int = 0
    notes: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    turns: list[BenchmarkTurnRecord] = field(default_factory=list)
    conversation_turn_count: int = 0
    conversation_completed: bool = False
    stop_reason: str | None = None
    final_agent_response: str | None = None
    final_user_response: str | None = None
    page_shadow_active: bool = False
    capability_gaps: list[str] = field(default_factory=list)
    stopped_by_user_simulation: bool = False
    simulation_stop_count: int = 0


@dataclass
class ValidationRecord:
    schema_pass: bool = True
    blocking_failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    deterministic_hash: str | None = None


@dataclass
class MetricsRecord:
    recall_hit: bool = False
    hit_at_1: bool = False
    hit_at_3: bool = False
    mrr: float = 0.0
    page_hit_at_1: bool | None = None
    page_hit_at_k: bool | None = None
    exact_page_hit: bool | None = None
    page_range_overlap_hit: bool | None = None
    min_page_distance: int | None = None
    locator_source: str | None = None
    locator_status: str | None = None
    locator_best_page: int | None = None
    locator_top_pages: list[int] = field(default_factory=list)
    locator_hit_at_1: bool | None = None
    locator_hit_at_k: bool | None = None
    locator_exact_page_hit: bool | None = None
    locator_range_overlap_hit: bool | None = None
    locator_min_page_distance: int | None = None
    locator_viewer_token_present: bool | None = None
    locator_preview_present: bool | None = None
    locator_eligible: bool | None = None
    locator_document_level_failure: str | None = None
    document_hit: bool | None = None
    document_hit_eligible: bool | None = None
    document_level_failure: str | None = None
    coord_eligible: bool | None = None
    coord_hit: bool | None = None
    coord_hit_page_numbers: list[int] = field(default_factory=list)
    coord_hit_group_ids: list[str] = field(default_factory=list)
    coord_failure_reason: str | None = None
    coord_metadata_present: bool | None = None
    coord_viewer_token_present: bool | None = None


@dataclass
class AnalysisRecord:
    final_hit: bool = False
    turn_count: int = 0
    decision_trace: list[dict[str, Any]] = field(default_factory=list)
    correction_count: int = 0
    ambiguous_turn_count: int = 0
    stop_reason: str | None = None
    failure_reason: str | None = None
    stopped_by_user_simulation: bool = False
    simulation_stop_count: int = 0
    simulation_valid_stop: bool | None = None
    user_stop_reason_code: str | None = None


@dataclass
class ArtifactsRecord:
    raw_response_path: str | None = None
    raw_response_paths: list[str] = field(default_factory=list)
    normalized_output_path: str | None = None
    score_report_path: str | None = None


@dataclass
class TaskMetadataRecord:
    split: str
    suite_id: str
    benchmark_track: str
    source_files: list[str]
    accepted_titles: list[str]
    accepted_pages: list[int]
    accepted_page_ranges: list[tuple[int, int]]
    expected_response_type: str
    required_ask_user_rounds: int
    user_id: str
    interaction_mode: str
    max_turns: int
    case_repeat_count: int
    user_simulation_driver: str
    user_simulation_scenario: str
    rollback_intent_mode: str
    rollback_min_round_gap: int
    page_goal_mode: str
    outputs: list[str]
    expected_actions: list[str]
    legacy_source_split: str | None = None
    legacy_source_layer: str | None = None
    user_profile_persona: str | None = None
    user_profile_goal: str | None = None
    user_profile_known_items: list[str] = field(default_factory=list)
    user_profile_uncertain_items: list[str] = field(default_factory=list)
    target_doc_file_id: str | None = None
    target_doc_title: str | None = None
    target_doc_count: int = 0
    target_doc_ids: list[str] = field(default_factory=list)
    target_doc_titles: list[str] = field(default_factory=list)
    locator_keywords: list[str] = field(default_factory=list)
    accepted_region_groups: list[AcceptedRegionGroup] = field(default_factory=list)
    coord_gold_page_numbers: list[int] = field(default_factory=list)
    coord_gold_group_ids: list[str] = field(default_factory=list)
    target_match_mode: str = "any_of"


@dataclass
class CaseRunResult:
    case_id: str
    attempt_index: int
    split: str
    layer: str
    suite_id: str
    business_line: str
    input_modality: str
    input: dict[str, Any]
    execution: ExecutionRecord
    response: ResponseRecord
    prediction: PredictionRecord
    workflow: WorkflowRecord
    validation: ValidationRecord
    metrics: MetricsRecord
    analysis: AnalysisRecord
    artifacts: ArtifactsRecord
    task_metadata: TaskMetadataRecord

    def to_dict(self) -> dict[str, Any]:
        return _sanitize_standard_report_payload(asdict(self))


def _sanitize_standard_report_payload(raw_value: Any) -> Any:
    if isinstance(raw_value, dict):
        sanitized: dict[str, Any] = {}
        for key, value in raw_value.items():
            if key in {"selection_payload", "selected_selection_payload"}:
                present = False
                if isinstance(value, dict):
                    present = bool(value)
                elif isinstance(value, list):
                    present = bool(value)
                elif value not in (None, "", False):
                    present = True
                sanitized[key] = {"redacted": True, "present": present}
                continue
            sanitized[key] = _sanitize_standard_report_payload(value)
        return sanitized
    if isinstance(raw_value, list):
        return [_sanitize_standard_report_payload(item) for item in raw_value]
    return raw_value


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_page_ranges(raw_value: object) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    if not isinstance(raw_value, list):
        return ranges
    for item in raw_value:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            try:
                start = int(item[0])
                end = int(item[1])
            except (TypeError, ValueError):
                continue
            ranges.append((start, end))
    return ranges


def parse_actions(raw_value: object) -> list[Action]:
    actions: list[Action] = []
    if not isinstance(raw_value, list):
        return actions
    for item in raw_value:
        if isinstance(item, Action):
            actions.append(item)
            continue
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        kwargs = item.get("kwargs")
        if not isinstance(kwargs, dict):
            kwargs = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
        actions.append(Action(name=name.strip(), kwargs=kwargs))
    return actions


def parse_outputs(raw_value: object) -> list[str]:
    if not isinstance(raw_value, list):
        return []
    outputs: list[str] = []
    for item in raw_value:
        if isinstance(item, str) and item.strip():
            outputs.append(item.strip())
    return outputs


def _parse_optional_text(raw_value: object) -> str | None:
    if isinstance(raw_value, str) and raw_value.strip():
        return raw_value.strip()
    return None


def _parse_string_list(raw_value: object) -> list[str]:
    if not isinstance(raw_value, list):
        return []
    values: list[str] = []
    for item in raw_value:
        if isinstance(item, str) and item.strip():
            values.append(item.strip())
    return values


def _parse_string_list_mapping(raw_value: object) -> dict[str, list[str]]:
    if not isinstance(raw_value, dict):
        return {}
    normalized: dict[str, list[str]] = {}
    for key, value in raw_value.items():
        normalized_key = _parse_optional_text(key)
        if normalized_key is None:
            continue
        normalized_value = _parse_string_list(value)
        if normalized_value:
            normalized[normalized_key] = normalized_value
    return normalized


def _parse_string_mapping(raw_value: object) -> dict[str, str]:
    if not isinstance(raw_value, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, value in raw_value.items():
        normalized_key = _parse_optional_text(key)
        normalized_value = _parse_optional_text(value)
        if normalized_key is None or normalized_value is None:
            continue
        normalized[normalized_key] = normalized_value
    return normalized


def _parse_region_boxes(raw_value: object) -> list[RegionBox]:
    if not isinstance(raw_value, list):
        return []
    boxes: list[RegionBox] = []
    for item in raw_value:
        if not isinstance(item, (list, tuple)) or len(item) != 4:
            continue
        try:
            x1 = float(item[0])
            y1 = float(item[1])
            x2 = float(item[2])
            y2 = float(item[3])
        except (TypeError, ValueError):
            continue
        boxes.append((x1, y1, x2, y2))
    return boxes


def _parse_accepted_region_group(raw_value: object) -> AcceptedRegionGroup | None:
    if not isinstance(raw_value, dict):
        return None
    page_number_raw = raw_value.get("page_number")
    page_number: int | None = None
    try:
        if page_number_raw is not None and not isinstance(page_number_raw, bool):
            page_number = int(page_number_raw)
    except (TypeError, ValueError):
        page_number = None
    match_mode = _parse_optional_text(raw_value.get("match_mode")) or "any_box"
    return AcceptedRegionGroup(
        group_id=_parse_optional_text(raw_value.get("group_id")),
        page_number=page_number,
        label=_parse_optional_text(raw_value.get("label")),
        boxes_norm=_parse_region_boxes(raw_value.get("boxes_norm")),
        match_mode=match_mode,
    )


def _parse_accepted_region_groups(raw_value: object) -> list[AcceptedRegionGroup]:
    if not isinstance(raw_value, list):
        return []
    groups: list[AcceptedRegionGroup] = []
    for item in raw_value:
        group = _parse_accepted_region_group(item)
        if group is not None:
            groups.append(group)
    return groups


def _collect_locator_keywords(target_docs: list[TargetDocumentTruth]) -> list[str]:
    keywords: list[str] = []
    for target_doc in target_docs:
        keywords.extend(target_doc.locator_keywords)
    return _dedupe_strings(keywords)


def _collect_accepted_region_groups(target_docs: list[TargetDocumentTruth]) -> list[AcceptedRegionGroup]:
    groups: list[AcceptedRegionGroup] = []
    for target_doc in target_docs:
        groups.extend(target_doc.accepted_region_groups)
    return groups


def _collect_coord_gold_page_numbers(region_groups: list[AcceptedRegionGroup]) -> list[int]:
    pages: list[int] = []
    seen: set[int] = set()
    for group in region_groups:
        if group.page_number is None or group.page_number in seen:
            continue
        seen.add(group.page_number)
        pages.append(group.page_number)
    return pages


def _collect_coord_gold_group_ids(region_groups: list[AcceptedRegionGroup]) -> list[str]:
    group_ids: list[str] = []
    seen: set[str] = set()
    for group in region_groups:
        group_id = _parse_optional_text(group.group_id)
        if group_id is None:
            continue
        lowered = group_id.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        group_ids.append(group_id)
    return group_ids


def parse_stop_tokens(raw_value: object) -> list[str]:
    if not isinstance(raw_value, list):
        return [DEFAULT_STOP_TOKEN]
    tokens = [str(item).strip() for item in raw_value if isinstance(item, str) and str(item).strip()]
    return tokens or [DEFAULT_STOP_TOKEN]


def normalize_layer(raw_layer: object, *, legacy_source_split: str | None = None) -> str:
    layer = str(raw_layer or "").strip().lower()
    if layer == "blind":
        return "e2e"
    if legacy_source_split == "blind":
        return "e2e"
    if layer in {"atomic", "component", "e2e", "page"}:
        return layer
    return "e2e"


def infer_interaction_mode(raw_value: object, *, layer: str) -> str:
    if isinstance(raw_value, str) and raw_value.strip():
        mode = raw_value.strip().lower()
        if mode in {"single_turn", "multi_turn"}:
            return mode
    return "single_turn" if layer == "atomic" else "multi_turn"


def infer_max_turns(raw_value: object, *, interaction_mode: str) -> int:
    try:
        value = int(raw_value)
        if value >= 1:
            return value
    except (TypeError, ValueError):
        pass
    return 1 if interaction_mode == "single_turn" else 8


def infer_case_repeat_count(raw_value: object) -> int:
    try:
        value = int(raw_value)
        if value >= 1:
            return value
    except (TypeError, ValueError):
        pass
    return DEFAULT_CASE_REPEAT_COUNT


def _normalize_non_negative_int(raw_value: object, default: int) -> int:
    try:
        value = int(raw_value)
        if value >= 0:
            return value
    except (TypeError, ValueError):
        pass
    return default


def _normalize_lower_choice(raw_value: object, *, allowed: set[str], default: str | None) -> str | None:
    text = _parse_optional_text(raw_value)
    if text is None:
        return default
    normalized = text.lower()
    if normalized in allowed:
        return normalized
    return default


def parse_user_simulation_config(
    raw_value: object,
) -> UserSimulationConfig:
    if not isinstance(raw_value, dict):
        return UserSimulationConfig()

    driver_raw = str(raw_value.get("driver") or "ai").strip().lower()
    driver = driver_raw or "ai"

    scenario = _normalize_lower_choice(
        raw_value.get("scenario"),
        allowed=ALLOWED_USER_SIMULATION_SCENARIOS,
        default="normal",
    ) or "normal"

    rollback_intent_raw = str(raw_value.get("rollback_intent_mode") or "none").strip().lower()
    if rollback_intent_raw not in {"none", "immediate", "delayed"}:
        rollback_intent_raw = "none"

    notes = str(raw_value.get("notes")).strip() if isinstance(raw_value.get("notes"), str) else None

    return UserSimulationConfig(
        driver=driver,
        scenario=scenario,
        rollback_intent_mode=rollback_intent_raw,
        rollback_min_round_gap=_normalize_non_negative_int(raw_value.get("rollback_min_round_gap"), 0),
        notes=notes,
    )


def parse_user_profile(raw_value: object) -> UserProfile | None:
    if not isinstance(raw_value, dict):
        return None
    return UserProfile(
        persona=_normalize_lower_choice(
            raw_value.get("persona"),
            allowed=ALLOWED_USER_PERSONAS,
            default=_parse_optional_text(raw_value.get("persona")),
        ),
        goal=_parse_optional_text(raw_value.get("goal")),
        known_items=_parse_string_list(raw_value.get("known_items")),
        uncertain_items=_parse_string_list(raw_value.get("uncertain_items")),
        aliases=_parse_string_list_mapping(raw_value.get("aliases")),
        correction_style=_normalize_lower_choice(
            raw_value.get("correction_style"),
            allowed=ALLOWED_CORRECTION_STYLES,
            default=_parse_optional_text(raw_value.get("correction_style")),
        ),
        notes=_parse_optional_text(raw_value.get("notes")),
    )


def _dedupe_strings(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return deduped


def resolve_known_items(profile: UserProfile | None) -> list[str]:
    if profile is None:
        return []
    return _dedupe_strings(profile.known_items)


def resolve_uncertain_items(profile: UserProfile | None) -> list[str]:
    if profile is None:
        return []
    return _dedupe_strings(profile.uncertain_items)


def parse_target_document_truth(raw_value: object) -> TargetDocumentTruth | None:
    if not isinstance(raw_value, dict):
        return None
    accepted_pages: list[int] = []
    for item in raw_value.get("accepted_pages", []):
        try:
            accepted_pages.append(int(item))
        except (TypeError, ValueError):
            continue
    return TargetDocumentTruth(
        file_id=_parse_optional_text(raw_value.get("file_id")),
        title=_parse_optional_text(raw_value.get("title")),
        doc_path=_parse_optional_text(raw_value.get("doc_path")),
        facets=_parse_string_mapping(raw_value.get("facets")),
        accepted_pages=accepted_pages,
        accepted_page_ranges=parse_page_ranges(raw_value.get("accepted_page_ranges")),
        locator_keywords=_parse_string_list(raw_value.get("locator_keywords")),
        accepted_region_groups=_parse_accepted_region_groups(raw_value.get("accepted_region_groups")),
    )


def parse_target_document_truths(raw_value: object) -> list[TargetDocumentTruth]:
    if not isinstance(raw_value, list):
        return []
    target_docs: list[TargetDocumentTruth] = []
    for item in raw_value:
        target_doc = parse_target_document_truth(item)
        if target_doc is not None:
            target_docs.append(target_doc)
    return target_docs


def infer_target_match_mode(raw_value: object) -> str:
    if isinstance(raw_value, str) and raw_value.strip():
        normalized = raw_value.strip().lower()
        if normalized in {"any_of", "all_of"}:
            return normalized
    return "any_of"


def derive_accepted_titles(
    raw_titles: object,
    *,
    target_docs: list[TargetDocumentTruth],
) -> list[str]:
    accepted_titles = [
        str(item).strip()
        for item in raw_titles
        if isinstance(item, str) and str(item).strip()
    ] if isinstance(raw_titles, list) else []
    if not target_docs:
        return accepted_titles
    derived_titles = [doc.title for doc in target_docs if isinstance(doc.title, str) and doc.title.strip()]
    return _dedupe_strings([*derived_titles, *accepted_titles])


def infer_page_goal_mode(
    raw_value: object,
    *,
    accepted_pages: list[int],
    accepted_ranges: list[tuple[int, int]],
) -> str:
    if isinstance(raw_value, str) and raw_value.strip():
        mode = raw_value.strip().lower()
        if mode in {"disabled", "shadow", "required"}:
            return mode
    if accepted_pages or accepted_ranges:
        return "shadow"
    return "disabled"


def build_default_instruction(
    *,
    fixture_case: dict[str, Any],
    gold_case: dict[str, Any],
    layer: str,
    interaction_mode: str,
    page_goal_mode: str,
) -> str:
    question_text = str(fixture_case.get("question_text") or "").strip()
    question_type = str(
        fixture_case.get("question_type") or gold_case.get("question_type") or "找资料"
    ).strip()
    notes = str(fixture_case.get("notes") or "").strip()
    teacher_reply = str(fixture_case.get("teacher_reply") or "").strip()
    has_images = bool(fixture_case.get("question_images"))

    lines = [
        "你是一个正在使用 CRS 资料检索助手的用户。",
        f"你的目标是通过对话完成这件事：{question_type}。",
    ]
    if question_text:
        lines.append(f"你开场第一句会表达：{question_text}")
    if teacher_reply:
        lines.append(f"你还记得一句已有上下文提示：{teacher_reply}")
    if has_images:
        lines.append("你上传了图片。若助手询问图片细节，而指令里没有更多信息，不要编造 OCR 或零件信息。")
    if notes:
        lines.append(f"补充约束：{notes}")
    if interaction_mode == "multi_turn":
        lines.append("不要一次性把所有信息都说完。只有在助手问到时，再逐步补充必要信息。")
    else:
        lines.append("这是一个更接近单轮的任务，尽量在首轮说清核心需求。")
    lines.append("如果助手索要指令中没有提供的信息，不要编造，直接说你不知道、记不清、或只能提供当前这些内容。")
    lines.append("你的正式成功标准是拿到正确的资料文件。")
    if page_goal_mode == "shadow":
        lines.append("当前 benchmark 阶段页码能力尚未正式实现，因此不要强行要求页码；如果助手已定位到正确文件，可以结束对话。")
    elif page_goal_mode == "required":
        lines.append("这个任务最终希望拿到文件以及对应页码，但如果助手暂时只能给出文件，也先继续配合澄清。")
    lines.append(f"当你的目标已经达到，返回 {DEFAULT_STOP_TOKEN} 作为单独一行，不要输出别的内容。")
    return "\n".join(lines)


def merge_suite_from_paths(
    *,
    split: str,
    fixture_path: Path,
    gold_path: Path,
    legacy_source_split: str | None = None,
) -> TaskSuite:
    fixture_blob = load_json(fixture_path)
    gold_blob = load_json(gold_path)
    if not isinstance(fixture_blob, dict) or not isinstance(gold_blob, dict):
        raise ValueError(f"invalid suite blob: {fixture_path} / {gold_path}")

    fixture_cases = fixture_blob.get("cases")
    gold_cases = gold_blob.get("cases")
    if not isinstance(fixture_cases, list) or not isinstance(gold_cases, list):
        raise ValueError(f"cases missing: {fixture_path} / {gold_path}")

    gold_by_case_id = {
        str(item.get("case_id")): item
        for item in gold_cases
        if isinstance(item, dict) and item.get("case_id")
    }
    cases: list[TaskCase] = []
    suite_layer = normalize_layer(fixture_blob.get("layer"), legacy_source_split=legacy_source_split)
    source_files = [str(item) for item in fixture_blob.get("source_files", []) if isinstance(item, str)]

    for fixture_case in fixture_cases:
        if not isinstance(fixture_case, dict):
            continue
        case_id = str(fixture_case.get("case_id") or "").strip()
        if not case_id:
            continue
        gold_case = gold_by_case_id.get(case_id, {})
        if not isinstance(gold_case, dict):
            gold_case = {}

        layer = normalize_layer(
            fixture_case.get("layer") or gold_case.get("layer") or suite_layer,
            legacy_source_split=legacy_source_split,
        )
        target_docs = parse_target_document_truths(gold_case.get("target_docs"))
        target_doc = parse_target_document_truth(gold_case.get("target_doc"))
        if target_docs:
            accepted_titles = derive_accepted_titles(gold_case.get("accepted_titles"), target_docs=target_docs)
            if target_doc is None:
                target_doc = target_docs[0]
        else:
            accepted_titles = derive_accepted_titles(gold_case.get("accepted_titles"), target_docs=[])
            if target_doc is not None:
                target_docs = [target_doc]
                accepted_titles = derive_accepted_titles(accepted_titles, target_docs=target_docs)
        accepted_pages = []
        for item in gold_case.get("accepted_pages", []):
            try:
                accepted_pages.append(int(item))
            except (TypeError, ValueError):
                continue
        accepted_ranges = parse_page_ranges(gold_case.get("accepted_page_ranges"))
        interaction_mode = infer_interaction_mode(
            fixture_case.get("interaction_mode") or gold_case.get("interaction_mode"),
            layer=layer,
        )
        max_turns = infer_max_turns(
            fixture_case.get("max_turns") or gold_case.get("max_turns"),
            interaction_mode=interaction_mode,
        )
        case_repeat_count = infer_case_repeat_count(
            fixture_case.get("case_repeat_count") or gold_case.get("case_repeat_count")
        )
        page_goal_mode = infer_page_goal_mode(
            fixture_case.get("page_goal_mode") or gold_case.get("page_goal_mode"),
            accepted_pages=accepted_pages,
            accepted_ranges=accepted_ranges,
        )
        user_simulation_config = parse_user_simulation_config(
            gold_case.get("user_simulation_config")
            if isinstance(gold_case.get("user_simulation_config"), dict)
            else fixture_case.get("user_simulation_config"),
        )
        user_profile = parse_user_profile(fixture_case.get("user_profile"))
        actions = parse_actions(
            gold_case.get("actions")
            if isinstance(gold_case.get("actions"), list)
            else fixture_case.get("actions")
        )
        outputs = parse_outputs(
            gold_case.get("outputs")
            if isinstance(gold_case.get("outputs"), list)
            else gold_case.get("expected_outputs") or fixture_case.get("outputs")
        )
        user_id_raw = fixture_case.get("user_id") or gold_case.get("user_id") or f"user_{case_id}"
        user_id = str(user_id_raw).strip() or f"user_{case_id}"
        initial_user_message_raw = fixture_case.get("initial_user_message") or fixture_case.get("question_text")
        initial_user_message = (
            str(initial_user_message_raw).strip()
            if isinstance(initial_user_message_raw, str) and str(initial_user_message_raw).strip()
            else None
        )
        instruction_raw = fixture_case.get("instruction") or gold_case.get("instruction")
        instruction = (
            str(instruction_raw).strip()
            if isinstance(instruction_raw, str) and str(instruction_raw).strip()
            else build_default_instruction(
                fixture_case=fixture_case,
                gold_case=gold_case,
                layer=layer,
                interaction_mode=interaction_mode,
                page_goal_mode=page_goal_mode,
            )
        )

        cases.append(
            TaskCase(
                case_id=case_id,
                split=split,
                layer=layer,
                suite_id=str(fixture_blob.get("suite_id") or fixture_path.stem),
                input_modality=str(fixture_case.get("input_modality") or "text"),
                question_text=str(fixture_case.get("question_text") or ""),
                question_images=[
                    str(item) for item in fixture_case.get("question_images", []) if isinstance(item, str)
                ],
                vehicle_info=fixture_case.get("vehicle_info"),
                preprocess_strategy=str(fixture_case.get("preprocess_strategy") or "none"),
                benchmark_track=str(fixture_case.get("benchmark_track") or "chat_completions"),
                request_context=(
                    fixture_case.get("request_context")
                    if isinstance(fixture_case.get("request_context"), dict)
                    else {}
                ),
                accepted_titles=accepted_titles,
                preferred_title=(
                    str(gold_case.get("preferred_title"))
                    if isinstance(gold_case.get("preferred_title"), str)
                    else None
                ),
                user_id=user_id,
                instruction=instruction,
                actions=actions,
                outputs=outputs,
                expected_response_type=str(gold_case.get("expected_response_type") or "documents"),
                required_ask_user_rounds=_normalize_non_negative_int(
                    gold_case.get("required_ask_user_rounds")
                    if gold_case.get("required_ask_user_rounds") is not None
                    else fixture_case.get("required_ask_user_rounds"),
                    0,
                ),
                top_k=int(gold_case.get("top_k") or 10),
                interaction_mode=interaction_mode,
                initial_user_message=initial_user_message,
                max_turns=max_turns,
                case_repeat_count=case_repeat_count,
                stop_tokens=parse_stop_tokens(
                    fixture_case.get("stop_tokens") or gold_case.get("stop_tokens")
                ),
                user_simulation_config=user_simulation_config,
                page_goal_mode=page_goal_mode,
                accepted_pages=accepted_pages,
                accepted_page_ranges=accepted_ranges,
                notes=fixture_case.get("notes"),
                source_files=source_files,
                question_type=fixture_case.get("question_type") or gold_case.get("question_type"),
                teacher_reply=fixture_case.get("teacher_reply"),
                benchmark_track_label=str(fixture_case.get("benchmark_track") or "chat_completions"),
                legacy_source_split=legacy_source_split,
                legacy_source_layer=(
                    str(fixture_case.get("layer"))
                    if fixture_case.get("layer") is not None
                    else str(gold_case.get("layer") or "")
                ),
                user_profile=user_profile,
                target_doc=target_doc,
                target_docs=target_docs,
                target_match_mode=infer_target_match_mode(gold_case.get("target_match_mode")),
                metadata={
                    "fixture_path": str(fixture_path),
                    "gold_path": str(gold_path),
                },
            )
        )

    return TaskSuite(
        split=split,
        suite_id=str(fixture_blob.get("suite_id") or fixture_path.stem),
        layer=suite_layer,
        acceptance_threshold=float(gold_blob.get("acceptance_threshold") or 1.0),
        source_files=source_files,
        cases=cases,
        legacy_source_split=legacy_source_split,
    )


def build_case_run_result(task: TaskCase, run_id: str, *, attempt_index: int = 1) -> CaseRunResult:
    target_docs = list(task.target_docs) if task.target_docs else ([task.target_doc] if task.target_doc else [])
    target_doc_ids = [doc.file_id for doc in target_docs if isinstance(doc.file_id, str) and doc.file_id.strip()]
    target_doc_titles = [doc.title for doc in target_docs if isinstance(doc.title, str) and doc.title.strip()]
    locator_keywords = _collect_locator_keywords(target_docs)
    accepted_region_groups = _collect_accepted_region_groups(target_docs)
    coord_gold_page_numbers = _collect_coord_gold_page_numbers(accepted_region_groups)
    coord_gold_group_ids = _collect_coord_gold_group_ids(accepted_region_groups)
    return CaseRunResult(
        case_id=task.case_id,
        attempt_index=attempt_index,
        split=task.split,
        layer=task.layer,
        suite_id=task.suite_id,
        business_line="DOC_SEARCH",
        input_modality=task.input_modality,
        input={
            "question_text": task.question_text,
            "question_images": list(task.question_images),
            "vehicle_info": task.vehicle_info,
        },
        execution=ExecutionRecord(run_id=run_id),
        response=ResponseRecord(),
        prediction=PredictionRecord(),
        workflow=WorkflowRecord(
            notes=task.notes,
            page_shadow_active=task.page_goal_mode == "shadow",
        ),
        validation=ValidationRecord(),
        metrics=MetricsRecord(),
        analysis=AnalysisRecord(),
        artifacts=ArtifactsRecord(),
        task_metadata=TaskMetadataRecord(
            split=task.split,
            suite_id=task.suite_id,
            benchmark_track=task.benchmark_track,
            source_files=list(task.source_files),
            accepted_titles=list(task.accepted_titles),
            accepted_pages=list(task.accepted_pages),
            accepted_page_ranges=list(task.accepted_page_ranges),
            expected_response_type=task.expected_response_type,
            required_ask_user_rounds=task.required_ask_user_rounds,
            user_id=task.user_id,
            interaction_mode=task.interaction_mode,
            max_turns=task.max_turns,
            case_repeat_count=task.case_repeat_count,
            user_simulation_driver=task.user_simulation_config.driver,
            user_simulation_scenario=task.user_simulation_config.scenario,
            rollback_intent_mode=task.user_simulation_config.rollback_intent_mode,
            rollback_min_round_gap=task.user_simulation_config.rollback_min_round_gap,
            page_goal_mode=task.page_goal_mode,
            outputs=list(task.outputs),
            expected_actions=[action.name for action in task.actions],
            legacy_source_split=task.legacy_source_split,
            legacy_source_layer=task.legacy_source_layer,
            user_profile_persona=task.user_profile.persona if task.user_profile else None,
            user_profile_goal=task.user_profile.goal if task.user_profile else None,
            user_profile_known_items=resolve_known_items(task.user_profile),
            user_profile_uncertain_items=resolve_uncertain_items(task.user_profile),
            target_doc_file_id=task.target_doc.file_id if task.target_doc else None,
            target_doc_title=task.target_doc.title if task.target_doc else None,
            target_doc_count=len(target_docs),
            target_doc_ids=target_doc_ids,
            target_doc_titles=target_doc_titles,
            locator_keywords=locator_keywords,
            accepted_region_groups=accepted_region_groups,
            coord_gold_page_numbers=coord_gold_page_numbers,
            coord_gold_group_ids=coord_gold_group_ids,
            target_match_mode=task.target_match_mode,
        ),
    )

"""Typed models for doc_search domain flow."""

from typing import Any

from pydantic import BaseModel, Field, field_validator


class DocSearchSelectionPayload(BaseModel):
    filters: dict[str, Any] = Field(default_factory=dict)
    file_ids: list[str] = Field(default_factory=list)

    @field_validator("filters", mode="before")
    @classmethod
    def _default_filters(cls, value: Any) -> dict[str, Any]:
        return value or {}

    @field_validator("file_ids", mode="before")
    @classmethod
    def _normalize_file_ids(cls, value: Any) -> list[str]:
        if not value:
            return []
        return [str(item) for item in value if item not in (None, "")]


class DocSearchRequest(BaseModel):
    query: str
    filters: dict[str, Any] = Field(default_factory=dict)
    top_k: int = 20
    selection_payload: DocSearchSelectionPayload = Field(default_factory=DocSearchSelectionPayload)

    @field_validator("filters", mode="before")
    @classmethod
    def _default_filters(cls, value: Any) -> dict[str, Any]:
        return value or {}

    @field_validator("selection_payload", mode="before")
    @classmethod
    def _default_selection_payload(cls, value: Any) -> dict[str, Any] | DocSearchSelectionPayload:
        return value or {}


class DocSearchHardConstraint(BaseModel):
    ok: bool
    missing_tokens: list[str] = Field(default_factory=list)
    checked_tokens: list[str] = Field(default_factory=list)
    message: str | None = None


class DocSearchExistence(BaseModel):
    status: str
    query_entities: dict[str, Any] = Field(default_factory=dict)
    matched_entities: dict[str, Any] = Field(default_factory=dict)
    unmatched_entities: dict[str, Any] = Field(default_factory=dict)
    suggestions: Any = Field(default_factory=dict)
    message: str | None = None
    should_continue: bool = True


class DocSearchExistenceHint(BaseModel):
    status: str
    message: str | None = None
    suggestions: Any = Field(default_factory=dict)


class DocSearchValidity(BaseModel):
    has_valid_results: bool
    reason: str | None = None
    message: str | None = None
    hard_constraint: DocSearchHardConstraint | None = None
    existence: DocSearchExistence | None = None


class DocSearchResultSummary(BaseModel):
    question: str
    result_type: str
    result_count: int
    preview: str
    display_title: str
    display_subtitle: str
    can_collapse: bool = False


class DocSearchTopResult(BaseModel):
    file_id: str
    title: str | None = None
    score: float | int | None = None
    pic_folder_url: str | None = None
    brand: str | None = None
    series: str | None = None
    model: str | None = None
    selection_payload: DocSearchSelectionPayload = Field(default_factory=DocSearchSelectionPayload)


class DocSearchClarifyContext(BaseModel):
    message: str
    query: str
    results_count: int
    clarify_round: int = 1
    top_result: DocSearchTopResult | None = None
    existence_info: DocSearchExistenceHint | None = None


class DocSearchExecutionResult(BaseModel):
    query: str
    original_query: str
    results: list[dict[str, Any]] = Field(default_factory=list)
    total: int = 0
    preprocessing: dict[str, Any] | None = None
    search_method: str | None = None
    search_time_ms: float | None = None
    requested_filters: dict[str, Any] = Field(default_factory=dict)
    applied_filters: dict[str, Any] = Field(default_factory=dict)
    applied_selection_payload: DocSearchSelectionPayload = Field(default_factory=DocSearchSelectionPayload)
    validity: DocSearchValidity
    summary: str | None = None
    summary_query: str | None = None
    result_summary: DocSearchResultSummary | None = None

    def to_tool_data(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class DocSearchAmbiguityOption(BaseModel):
    key: str
    label: str
    description: str | None = None
    selection_payload: DocSearchSelectionPayload = Field(default_factory=DocSearchSelectionPayload)


class DocSearchAmbiguityAnalysis(BaseModel):
    need_clarify: bool = False
    facet: str | None = None
    reason: str | None = None
    question: str | None = None
    source: str = "rule"
    results_count: int = 0
    options: list[DocSearchAmbiguityOption] = Field(default_factory=list)
    context: DocSearchClarifyContext | None = None


class DocSearchLLMClarifyOption(BaseModel):
    label: str
    description: str | None = None
    file_ids: list[str] = Field(default_factory=list)


class DocSearchLLMClarifyResult(BaseModel):
    question: str
    dimension: str = ""
    reason: str = "llm_smart_clarify"
    options: list[DocSearchLLMClarifyOption] = Field(default_factory=list)


class DocSearchPlannedQuery(BaseModel):
    query: str
    intent: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class DocSearchQueryPlan(BaseModel):
    primary_query: str
    queries: list[DocSearchPlannedQuery] = Field(default_factory=list)
    rationale: str = ""

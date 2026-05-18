"""Default tool catalog."""

from typing import Dict, List, Optional

from app.agent.tools.base import ToolExecutionMode, ToolSpec


class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"tool already registered: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def list(self) -> List[ToolSpec]:
        return list(self._tools.values())


def build_default_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    specs = [
        ToolSpec(
            name="ask_user_question",
            description="Unified human interaction entry for structured follow-up questions.",
            execution_mode=ToolExecutionMode.DEFERRED,
            tags=["ask_user", "interaction"],
        ),
        ToolSpec(
            name="lookup_ecu_candidates",
            description="Lookup ECU candidates from a fault code.",
            tags=["fault_diagnosis", "ecu", "external"],
        ),
        ToolSpec(
            name="dtc_diagnosis",
            description="Run DTC diagnosis with fault code and ECU.",
            tags=["fault_diagnosis", "external"],
        ),
        ToolSpec(
            name="lookup_repair_knowledge_titles",
            description="Load repair-knowledge titles from the local Excel library.",
            tags=["knowledge", "local"],
        ),
        ToolSpec(
            name="get_repair_knowledge_context",
            description="Load full repair-knowledge document content by entry id.",
            tags=["knowledge", "local"],
        ),
        ToolSpec(
            name="query_parameters",
            description="Lookup exact ECU pin parameters from the local structured cache, such as specific pin numbers, pin-definition values, connector pin numbers, and expected voltages. Do not use this for generic wiring-diagram or pinout-document retrieval requests.",
            tags=["parameter_query", "local"],
        ),
        ToolSpec(
            name="search_circuit_diagram",
            description="Search circuit diagrams and related wiring documents.",
            tags=["diagram", "external"],
        ),
    ]
    for spec in specs:
        registry.register(spec)
    return registry

"""故障码解析器."""

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ParsedFaultCode:
    original: str
    normalized: str
    code_type: str
    is_valid: bool = True


class FaultCodeParser:
    """支持从自然语言中提取并规范化故障码。"""

    PATTERN_OBD2 = re.compile(
        r"(?:^|[^A-Za-z0-9])([PCBU])([0-9A-Fa-f]{4})(?:[^A-Za-z0-9:]|$)",
        re.IGNORECASE,
    )
    PATTERN_EXTENDED = re.compile(
        r"(?:^|[^A-Za-z0-9])([PCBU])([0-9A-Fa-f]{4})[\:\-]([0-9A-Fa-f]{1,2})(?:[^A-Za-z0-9]|$)",
        re.IGNORECASE,
    )
    PATTERN_TYPE = re.compile(
        r"(?:^|[^A-Za-z0-9])([PCBU])([0-9A-Fa-f]{4})\s+TYPE\s+(\d{1,2})(?:[^A-Za-z0-9]|$)",
        re.IGNORECASE,
    )
    PATTERN_DTC = re.compile(r"\bDTC\s*(\d{1,5})\b", re.IGNORECASE)
    PATTERN_NUMERIC = re.compile(r"\b(\d{1,5})\b")
    PATTERN_SPN_FMI = re.compile(r"\bSPN[\s\:\-]*(\d+)\s*(?:FMI[\s\:\-]*(\d+))?\b", re.IGNORECASE)

    def parse(self, text: str) -> list[ParsedFaultCode]:
        results: list[ParsedFaultCode] = []

        for match in self.PATTERN_EXTENDED.finditer(text):
            prefix, code, suffix = match.groups()
            results.append(
                ParsedFaultCode(
                    original=match.group(0),
                    normalized=f"{prefix.upper()}{code.upper()}{suffix.upper()}",
                    code_type="extended",
                )
            )

        for match in self.PATTERN_TYPE.finditer(text):
            prefix, code, type_num = match.groups()
            results.append(
                ParsedFaultCode(
                    original=match.group(0),
                    normalized=f"{prefix.upper()}{code.upper()}{type_num.zfill(2)}",
                    code_type="type",
                )
            )

        for match in self.PATTERN_OBD2.finditer(text):
            original = match.group(0)
            if self._is_already_matched(original, results):
                continue
            end_pos = match.end()
            if end_pos < len(text):
                next_char = text[end_pos : end_pos + 1]
                if next_char in [":", "-"]:
                    continue
                if text[end_pos : end_pos + 5].strip().upper().startswith("TYPE"):
                    continue

            prefix, code = match.groups()
            results.append(
                ParsedFaultCode(
                    original=original,
                    normalized=f"{prefix.upper()}{code.upper()}",
                    code_type="obd2",
                )
            )

        for match in self.PATTERN_DTC.finditer(text):
            num = match.group(1)
            results.append(
                ParsedFaultCode(
                    original=match.group(0),
                    normalized=f"DTC{num}",
                    code_type="dtc",
                )
            )

        for match in self.PATTERN_SPN_FMI.finditer(text):
            spn = match.group(1)
            fmi = match.group(2)
            normalized = f"SPN{spn}_FMI{fmi}" if fmi else f"SPN{spn}"
            results.append(
                ParsedFaultCode(
                    original=match.group(0),
                    normalized=normalized,
                    code_type="spn_fmi",
                )
            )

        if not results and self._has_fault_context(text):
            for match in self.PATTERN_NUMERIC.finditer(text):
                num = match.group(1)
                if len(num) < 2 or len(num) > 5:
                    continue
                results.append(
                    ParsedFaultCode(
                        original=num,
                        normalized=num,
                        code_type="numeric",
                    )
                )
                break

        return results

    def parse_first(self, text: str) -> ParsedFaultCode | None:
        results = self.parse(text)
        return results[0] if results else None

    def extract_fault_codes(self, text: str) -> list[str]:
        return [code.normalized for code in self.parse(text)]

    @staticmethod
    def _is_already_matched(text: str, results: list[ParsedFaultCode]) -> bool:
        return any(text.upper() in result.original.upper() for result in results)

    @staticmethod
    def _has_fault_context(text: str) -> bool:
        fault_keywords = {"故障", "错误", "报警", "诊断", "fault", "error", "dtc", "码"}
        text_lower = text.lower()
        return any(keyword in text_lower for keyword in fault_keywords)


_fault_code_parser: FaultCodeParser | None = None


def get_fault_code_parser() -> FaultCodeParser:
    global _fault_code_parser
    if _fault_code_parser is None:
        _fault_code_parser = FaultCodeParser()
    return _fault_code_parser

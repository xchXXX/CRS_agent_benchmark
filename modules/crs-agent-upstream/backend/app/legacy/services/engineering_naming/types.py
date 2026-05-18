from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class EngineeringParseResult:
    """工程命名解析结果（通用大筐版本）"""

    eng_codes: List[str] = field(default_factory=list)


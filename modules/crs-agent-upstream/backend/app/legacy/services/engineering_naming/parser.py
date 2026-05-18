import re
from typing import List

from .types import EngineeringParseResult


_ALNUM_SEQ_RE = re.compile(r"[A-Za-z0-9]{2,20}")

# 允许把部分“纯字母短码”也当作工程码（平台/版本前缀等）
_PURE_ALPHA_WHITELIST = {
    "KR", "KF", "KA", "VR", "VL", "KL", "KM", "KN", "KS",
}

# 明确排除的常见缩写（避免把通用术语当成工程码）
_STOPWORDS = {
    "ECU", "CAN", "LIN", "OBD", "VIN",
}


def _normalize_token(token: str) -> str:
    token = token.strip()
    if not token:
        return ""
    return token.upper()


def extract_eng_codes(text: str) -> List[str]:
    """从文本中提取工程命名编码（通用大筐）

    规则（尽量保守但覆盖工程命名）：
    - 仅提取字母数字串（A-Za-z0-9）
    - 必须满足：
      1) 含数字；或
      2) 为白名单中的纯字母短码（KR/KF/KA/VR...）
    - 排除 stopwords
    """
    seen = set()
    codes: List[str] = []

    for m in _ALNUM_SEQ_RE.finditer(str(text)):
        token = _normalize_token(m.group(0))
        if not token:
            continue

        if token in _STOPWORDS:
            continue

        has_digit = any(ch.isdigit() for ch in token)
        if not has_digit and token not in _PURE_ALPHA_WHITELIST:
            continue

        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        codes.append(token)

    return codes


def parse_engineering_naming(text: str) -> EngineeringParseResult:
    return EngineeringParseResult(eng_codes=extract_eng_codes(text))

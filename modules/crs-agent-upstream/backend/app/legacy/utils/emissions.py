"""排放标准（国二~国六）解析与简写展开工具。

目的：把类似“国四、五 / 国四-五 / 国四_五 / 国Ⅳ、Ⅴ / 国4/5”等写法展开成标准项：
['国四', '国五']，以便用于过滤匹配与澄清选项生成。
"""

from __future__ import annotations

import re
import unicodedata
from typing import List, Optional


_ARABIC_TO_ZH = {
    "2": "二",
    "3": "三",
    "4": "四",
    "5": "五",
    "6": "六",
}

_ROMAN_TO_ZH = {
    "II": "二",
    "III": "三",
    "IV": "四",
    "V": "五",
    "VI": "六",
}

# 支持：中文数字 / 阿拉伯数字 / 罗马数字（NFKC 后通常为 I/V/X 组合）
_EMISSION_TOKEN_RE = re.compile(
    r"^(?P<num>[二三四五六]|[2-6]|II|III|IV|V|VI)(?P<suffix>[abAB])?$"
)

# 常见分隔符：中文顿号、斜杠、下划线、短横线/长横线、波浪线、以及“至/到”
_SEPARATOR_RE = re.compile(r"(、|，|,|/|_|-|\u2013|\u2014|~|～|至|到)+")


def _normalize_emission_token(token: str) -> Optional[str]:
    """把 token 归一化为 '国四/国五/国六A/国六B' 等，失败返回 None。"""
    if not token:
        return None

    t = unicodedata.normalize("NFKC", str(token)).strip()
    if not t:
        return None

    # 允许 token 自带“国”前缀
    if t.startswith("国"):
        t = t[1:].strip()

    # 只取前部最可能的 token（过滤“国四排放”“五车”等噪声）
    # 例如：'五排放' -> '五'；'VIb' -> 'VIb'
    m_prefix = re.match(r"^(II|III|IV|V|VI|[二三四五六]|[2-6])([abAB])?", t)
    if not m_prefix:
        return None

    num = m_prefix.group(1)
    suffix = m_prefix.group(2) or ""

    if num in _ARABIC_TO_ZH:
        num = _ARABIC_TO_ZH[num]
    elif num in _ROMAN_TO_ZH:
        num = _ROMAN_TO_ZH[num]

    m = _EMISSION_TOKEN_RE.match(num + suffix)
    if not m:
        return None

    num_zh = m.group("num")
    suffix_norm = (m.group("suffix") or "").upper()
    return f"国{num_zh}{suffix_norm}"


def expand_emissions_shorthand(value: str) -> List[str]:
    """展开排放标准简写。

    Examples:
        '国四、五' -> ['国四', '国五']
        '国4/5' -> ['国四', '国五']
        '国Ⅳ-Ⅴ' -> ['国四', '国五']

    Returns:
        展开后的标准项列表（去重、保持出现顺序）；若无法解析则返回空列表。
    """
    if not value:
        return []

    text = unicodedata.normalize("NFKC", str(value))
    text = re.sub(r"\s+", "", text)
    if not text:
        return []

    # 尝试把“国X分隔符Y...”拆成多个 token。注意：这里只处理“国”只出现一次的简写场景；
    # 若用户写了“国四/国五”，split 后也能正常归一化。
    parts = [p for p in _SEPARATOR_RE.split(text) if p and not _SEPARATOR_RE.fullmatch(p)]
    if len(parts) <= 1:
        # 没有分隔符时，仅尝试归一化单项（如 国5 / 国Ⅴ）
        single = _normalize_emission_token(text)
        return [single] if single else []

    # 对每个分段做 token 归一化；若第一段是“国四”，后续可能只有“五”
    expanded: List[str] = []
    seen = set()
    for part in parts:
        token = _normalize_emission_token(part)
        if not token:
            continue
        if token not in seen:
            seen.add(token)
            expanded.append(token)

    return expanded


# 燃料相关词在检索侧常与 emissions 字段混用，统一归一到同一匹配 token。
_FUEL_ALIAS_TO_TOKEN = {
    # 新能源语义簇
    "新能源": "新能源",
    "燃料电池": "新能源",
    "纯电": "新能源",
    "混动": "新能源",
    "氢能源": "新能源",
    "fcev": "新能源",
    "ev": "新能源",
    "bev": "新能源",
    "phev": "新能源",
    # 天然气语义簇
    "天然气": "天然气",
    "lng": "天然气",
    "cng": "天然气",
    "燃气": "天然气",
    # 柴油/汽油
    "柴油": "柴油",
    "diesel": "柴油",
    "燃油": "柴油",
    "燃油车": "柴油",
    "汽油": "汽油",
    "gasoline": "汽油",
    "petrol": "汽油",
}


def _normalize_for_match(text: str) -> str:
    t = unicodedata.normalize("NFKC", str(text or "")).strip().lower()
    t = re.sub(r"\s+", "", t)
    return t


def expand_emissions_match_tokens(value: str) -> List[str]:
    """展开为用于匹配的 token 列表。

    规则：
    1) 国四、五等排放简写 -> ['国四', '国五']
    2) 新能源/燃料电池/纯电 等燃料词 -> 统一 token（如 '新能源'）
    3) 其他值 -> 归一化后原样返回
    """
    if not value:
        return []

    expanded = expand_emissions_shorthand(value)
    if expanded:
        seen = set()
        tokens: List[str] = []
        for item in expanded:
            token = _normalize_for_match(item)
            if token and token not in seen:
                seen.add(token)
                tokens.append(token)
        return tokens

    token = _normalize_for_match(value)
    if not token:
        return []

    mapped = _FUEL_ALIAS_TO_TOKEN.get(token, token)
    return [mapped]

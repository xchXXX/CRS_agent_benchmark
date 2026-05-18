"""硬约束不存在判定（Hard Constraint Validator）

目标：当用户查询中包含“强指向性 token”（平台码/ECU码/驱动形式/批次号/长数字/长工程码/特定定义类资料等）时，
如果这些 token 在候选结果中完全找不到任何证据，应直接返回“暂无相关资料”，避免系统用泛词命中给出误导结果。

设计原则：
- 只做“有/没有”的证据校验，不参与召回与排序。
- 优先使用结构化字段（platform_codes/ecus/drive_types/batches/eng_codes/doc_types/subsystems），再回退到 filename/hierarchy_full。
- 仅对强约束 token 一票否决；品牌/系列/泛化文档词不作为硬约束。
- 可配置、可复用：对话入口与 /api/search 共用同一判定逻辑。
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set

from app.legacy.services.config_service import config_service


@dataclass
class HardConstraintResult:
    """硬约束判定结果"""

    ok: bool
    missing_tokens: List[str] = field(default_factory=list)
    checked_tokens: List[str] = field(default_factory=list)
    message: Optional[str] = None


@dataclass
class _ConstraintToken:
    token: str
    variants: List[str]
    # 候选结果中优先查证的字段名（enrich 后结果 dict 的 key）
    fields: List[str]
    # 是否按“码类”匹配（更严格，大小写不敏感但不做模糊语义）
    is_code_like: bool = True


class HardConstraintValidator:
    """硬约束验证器（单例轻量对象即可）"""

    # 归一化替换（与澄清模块一致，用于“起动/启动”等写法差异）
    _TEXT_NORMALIZATION_REPLACEMENTS = [
        ("起动机", "启动机"),
        ("起動機", "启动机"),
        ("起动", "启动"),
        ("起動", "启动"),
    ]

    # 常见泛化词，不作为硬约束
    _GENERIC_DOC_WORDS: Set[str] = {
        "资料",
        "文档",
        "电路图",
        "线路图",
        "整车图",
        "整车电路图",
        "线束图",
        "图",
    }

    # 平台/车型码：D + 2-4 位数字
    _RE_PLATFORM = re.compile(r"^[dD]\d{2,4}$")
    # 驱动形式：6x2/6X4 等
    _RE_DRIVE = re.compile(r"^\d+x\d+$", re.IGNORECASE)

    def validate(self, results: Sequence[Dict[str, Any]], preprocessing: Optional[Dict[str, Any]]) -> HardConstraintResult:
        """对 enrich 后结果做硬约束证据校验。"""
        enabled = bool(config_service.get("hard_constraint_enabled", True))
        if not enabled:
            return HardConstraintResult(ok=True)

        if not preprocessing:
            return HardConstraintResult(ok=True)

        constraints = self._extract_constraints(preprocessing)
        if not constraints:
            return HardConstraintResult(ok=True)

        top_n = int(config_service.get("hard_constraint_top_n", 50) or 50)
        candidates = list(results[: max(1, top_n)]) if results else []

        missing: List[str] = []
        checked: List[str] = []

        for c in constraints:
            checked.append(c.token)
            if not self._has_any_evidence(candidates, c):
                missing.append(c.token)

        if missing:
            return HardConstraintResult(
                ok=False,
                missing_tokens=missing,
                checked_tokens=checked,
                message="抱歉，暂无相关资料在数据库中。",
            )

        return HardConstraintResult(ok=True, checked_tokens=checked)

    # -------------------- token 提取（硬约束定义） --------------------

    def _extract_constraints(self, preprocessing: Dict[str, Any]) -> List[_ConstraintToken]:
        entities: Dict[str, List[str]] = preprocessing.get("entities") or {}
        query_tokens: List[str] = preprocessing.get("query_tokens") or []
        token_expansions: Dict[str, List[str]] = preprocessing.get("token_expansions") or {}

        constraints: List[_ConstraintToken] = []

        # 1) 平台/车型码（强约束）
        platform_values = entities.get("platform") or []
        for v in platform_values:
            variants = self._unique_variants([v])
            constraints.append(
                _ConstraintToken(
                    token=v,
                    variants=variants,
                    fields=["platform_codes", "eng_codes", "filename", "hierarchy_full"],
                    is_code_like=True,
                )
            )

        # 2) ECU/控制器型号（强约束）
        ecu_values = entities.get("ecu") or []
        for v in ecu_values:
            variants = self._unique_variants([v])
            constraints.append(
                _ConstraintToken(
                    token=v,
                    variants=variants,
                    fields=["ecus", "filename", "hierarchy_full"],
                    is_code_like=True,
                )
            )

        # 3) 驱动形式（强约束）
        drive_values = entities.get("drive_type") or []
        for v in drive_values:
            variants = self._unique_variants([v])
            constraints.append(
                _ConstraintToken(
                    token=v,
                    variants=variants,
                    fields=["drive_types", "filename", "hierarchy_full"],
                    is_code_like=True,
                )
            )

        # 4) 批次号（强约束）
        batch_values = entities.get("batch") or []
        for v in batch_values:
            variants = self._unique_variants([v])
            constraints.append(
                _ConstraintToken(
                    token=v,
                    variants=variants,
                    fields=["batches", "eng_codes", "filename", "hierarchy_full"],
                    is_code_like=True,
                )
            )

        # 5) 工程命名编码（仅对“带数字/较长”的值强约束，避免 KR/KF 等短码误伤）
        eng_values = entities.get("eng_code") or []
        for v in eng_values:
            vv = str(v).strip()
            if not vv:
                continue
            if vv.isalpha() and len(vv) <= 2:
                continue
            if (not any(ch.isdigit() for ch in vv)) and len(vv) < 4:
                continue

            variants = self._unique_variants([vv])
            constraints.append(
                _ConstraintToken(
                    token=vv,
                    variants=variants,
                    fields=["eng_codes", "filename", "hierarchy_full"],
                    is_code_like=True,
                )
            )

        # 6) 查询 token 中的“长数字”（强约束，例如 10086）
        long_num_min_len = int(config_service.get("hard_constraint_long_number_min_len", 5) or 5)
        for t in query_tokens:
            if t.isdigit() and len(t) >= long_num_min_len:
                constraints.append(
                    _ConstraintToken(
                        token=t,
                        variants=self._unique_variants([t]),
                        fields=["filename", "hierarchy_full", "eng_codes", "platform_codes", "ecus"],
                        is_code_like=True,
                    )
                )

        # 7) 3-4 位纯数字短码：仅当 token 扩展能反查出 Dxxx（如 530→D530）才视为平台强约束
        for t in query_tokens:
            if not (t.isdigit() and 3 <= len(t) <= 4):
                continue
            variants = token_expansions.get(t) or [t]
            # 只在扩展中出现 Dxx/Dxxx/Dxxxx 时启用硬约束
            platform_variants = [v for v in variants if self._RE_PLATFORM.match(str(v).strip())]
            if platform_variants:
                constraints.append(
                    _ConstraintToken(
                        token=t,
                        variants=self._unique_variants([t, *platform_variants]),
                        fields=["platform_codes", "eng_codes", "filename", "hierarchy_full"],
                        is_code_like=True,
                    )
                )

        # 8) 特定“定义/专项图”类关键词（条件强约束：只对配置的具体词启用）
        specific_keywords = config_service.get("hard_constraint_specific_doc_keywords", [])
        if isinstance(specific_keywords, str):
            try:
                specific_keywords = json.loads(specific_keywords)
            except Exception:
                specific_keywords = []
        if not isinstance(specific_keywords, list):
            specific_keywords = []

        # 从 entities/doc_type/subsystem 和 query_tokens 中捕获
        candidate_text_tokens: List[str] = []
        candidate_text_tokens.extend(entities.get("doc_type") or [])
        candidate_text_tokens.extend(entities.get("subsystem") or [])
        candidate_text_tokens.extend(query_tokens)

        for kw in self._match_specific_keywords(candidate_text_tokens, specific_keywords):
            if kw in self._GENERIC_DOC_WORDS:
                continue
            constraints.append(
                _ConstraintToken(
                    token=kw,
                    variants=self._unique_variants([kw]),
                    fields=["doc_types", "subsystems", "filename", "hierarchy_full"],
                    is_code_like=False,
                )
            )

        # 去重：同 token 保留一次
        dedup: Dict[str, _ConstraintToken] = {}
        for c in constraints:
            key = self._normalize_for_code(c.token) if c.is_code_like else self._normalize_for_text(c.token)
            if not key:
                continue
            if key not in dedup:
                dedup[key] = c
            else:
                # 合并 variants/fields
                prev = dedup[key]
                prev.variants = self._unique_variants([*prev.variants, *c.variants])
                prev.fields = list(dict.fromkeys([*prev.fields, *c.fields]))

        return list(dedup.values())

    def _match_specific_keywords(self, tokens: List[str], keywords: List[str]) -> List[str]:
        """在 tokens 中找出与 keywords 匹配的项（基于归一化子串匹配）。"""
        if not tokens or not keywords:
            return []

        keywords_norm = []
        for k in keywords:
            kn = self._normalize_for_text(k)
            if kn:
                keywords_norm.append((k, kn))

        matched: List[str] = []
        for t in tokens:
            tn = self._normalize_for_text(t)
            if not tn:
                continue
            for raw_kw, kw_norm in keywords_norm:
                if kw_norm and kw_norm in tn:
                    matched.append(raw_kw)
        # 去重保序
        seen = set()
        out = []
        for x in matched:
            xn = self._normalize_for_text(x)
            if xn in seen:
                continue
            seen.add(xn)
            out.append(x)
        return out

    # -------------------- 证据校验 --------------------

    def _has_any_evidence(self, candidates: Sequence[Dict[str, Any]], constraint: _ConstraintToken) -> bool:
        if not candidates:
            return False

        for doc in candidates:
            if self._doc_has_evidence(doc, constraint):
                return True
        return False

    def _doc_has_evidence(self, doc: Dict[str, Any], constraint: _ConstraintToken) -> bool:
        if not doc:
            return False

        variants = constraint.variants or [constraint.token]
        if constraint.is_code_like:
            variant_norms = [self._normalize_for_code(v) for v in variants if self._normalize_for_code(v)]
        else:
            variant_norms = [self._normalize_for_text(v) for v in variants if self._normalize_for_text(v)]

        if not variant_norms:
            return False

        for field in constraint.fields:
            value = doc.get(field)
            if value is None:
                continue

            if isinstance(value, list):
                hay_list = [str(x) for x in value if x]
                for item in hay_list:
                    if self._match_any(item, variant_norms, code_like=constraint.is_code_like):
                        return True
            else:
                if self._match_any(str(value), variant_norms, code_like=constraint.is_code_like):
                    return True

        return False

    def _match_any(self, haystack: str, needles_norm: List[str], code_like: bool) -> bool:
        if not haystack:
            return False

        hay_norm = self._normalize_for_code(haystack) if code_like else self._normalize_for_text(haystack)
        if not hay_norm:
            return False

        # 码类：直接子串匹配（大小写已归一化，不做模糊）
        for n in needles_norm:
            if n and n in hay_norm:
                return True
        return False

    # -------------------- 归一化/工具 --------------------

    def _normalize_for_code(self, text: Any) -> str:
        if text is None:
            return ""
        s = str(text).strip().lower()
        if not s:
            return ""
        # 去掉常见分隔符
        s = re.sub(r"[\s·•\-_/.(),，。:：;；!?！？（）【】\[\]{}]+", "", s)
        return s

    def _normalize_for_text(self, text: Any) -> str:
        if text is None:
            return ""
        normalized = unicodedata.normalize("NFKC", str(text)).strip().lower()
        if not normalized:
            return ""
        normalized = re.sub(r"\s+", "", normalized)
        normalized = re.sub(r"[·•\-_/.(),，。:：;；!?！？（）【】\[\]{}]+", "", normalized)
        for src, dst in self._TEXT_NORMALIZATION_REPLACEMENTS:
            normalized = normalized.replace(src.lower(), dst.lower())
        return normalized

    def _unique_variants(self, values: List[Any]) -> List[str]:
        seen = set()
        out = []
        for v in values:
            if v is None:
                continue
            s = str(v).strip()
            if not s:
                continue
            key = s.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
        return out


# 全局轻量单例
hard_constraint_validator = HardConstraintValidator()

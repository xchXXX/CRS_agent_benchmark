"""查询预处理模块

负责在检索之前对用户查询进行处理：
1. 规范化（去空白、统一大小写、全角→半角）
2. 拼音纠错（解决语音识别错误，如"天景"→"天锦"）
3. 查询实体抽取（从查询中识别品牌/系列/ECU等）
4. 同义词扩展
5. 返回结构化结果
"""

import re
import logging
import unicodedata
from typing import Dict, List, Set, Optional
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.legacy.config.regex_patterns import (
    BRAND_PATTERNS, SERIES_PATTERNS, MODEL_PATTERNS,
    PLATFORM_PATTERNS, ECU_PATTERNS, SUPPLIER_PATTERNS,
    EMISSION_PATTERNS, DRIVE_PATTERNS, BATCH_PATTERNS, DOC_TYPE_PATTERNS
)
from app.legacy.services.engineering_naming import extract_eng_codes
from app.legacy.services.pinyin_service import PinyinService, CorrectionResult
from app.legacy.services.synonym_service import SynonymService


_COLLAPSED_PLATFORM_PREFIX_RE = re.compile(
    r"(?i)(K[FLMNR]|V[LR])(?=D\d{2,4}(?![A-Za-z0-9]))"
)


def _restore_collapsed_platform_spacing(text: str) -> str:
    """Restore missing space between platform prefix and D-code (e.g., KLD320 -> KL D320)."""
    if not text:
        return ""
    return _COLLAPSED_PLATFORM_PREFIX_RE.sub(lambda m: f"{m.group(1)} ", text)

logger = logging.getLogger(__name__)


@dataclass
class QueryResult:
    """查询预处理结果"""
    # 原始查询
    original_query: str
    # 规范化后的查询
    normalized_query: str
    # 纠错后的查询（拼音纠错）
    corrected_query: str
    # 扩展后的查询（用于检索）
    expanded_query: str
    # 识别出的实体
    entities: Dict[str, List[str]] = field(default_factory=dict)
    # 同义词扩展映射 {原词: [扩展词]}
    synonym_expansions: Dict[str, List[str]] = field(default_factory=dict)
    # 拼音纠错详情列表
    pinyin_corrections: List[CorrectionResult] = field(default_factory=list)
    # 是否有拼音纠错
    has_correction: bool = False
    # 查询token列表（用于重排）
    query_tokens: List[str] = field(default_factory=list)
    # token扩展映射 {原始token: [原始token + 扩展形式]}（用于重排）
    token_expansions: Dict[str, List[str]] = field(default_factory=dict)
    # 扩展后的FULLTEXT查询（用于候选召回）
    expanded_fulltext_query: str = ""


class QueryPreprocessor:
    """查询预处理器"""

    # 全角→半角映射表
    FULLWIDTH_TO_HALFWIDTH = {
        '　': ' ',  # 全角空格
        '！': '!', '＂': '"', '＃': '#', '＄': '$', '％': '%',
        '＆': '&', '＇': "'", '（': '(', '）': ')', '＊': '*',
        '＋': '+', '，': ',', '－': '-', '．': '.', '／': '/',
        '０': '0', '１': '1', '２': '2', '３': '3', '４': '4',
        '５': '5', '６': '6', '７': '7', '８': '8', '９': '9',
        '：': ':', '；': ';', '＜': '<', '＝': '=', '＞': '>',
        '？': '?', '＠': '@',
        'Ａ': 'A', 'Ｂ': 'B', 'Ｃ': 'C', 'Ｄ': 'D', 'Ｅ': 'E',
        'Ｆ': 'F', 'Ｇ': 'G', 'Ｈ': 'H', 'Ｉ': 'I', 'Ｊ': 'J',
        'Ｋ': 'K', 'Ｌ': 'L', 'Ｍ': 'M', 'Ｎ': 'N', 'Ｏ': 'O',
        'Ｐ': 'P', 'Ｑ': 'Q', 'Ｒ': 'R', 'Ｓ': 'S', 'Ｔ': 'T',
        'Ｕ': 'U', 'Ｖ': 'V', 'Ｗ': 'W', 'Ｘ': 'X', 'Ｙ': 'Y',
        'Ｚ': 'Z',
        'ａ': 'a', 'ｂ': 'b', 'ｃ': 'c', 'ｄ': 'd', 'ｅ': 'e',
        'ｆ': 'f', 'ｇ': 'g', 'ｈ': 'h', 'ｉ': 'i', 'ｊ': 'j',
        'ｋ': 'k', 'ｌ': 'l', 'ｍ': 'm', 'ｎ': 'n', 'ｏ': 'o',
        'ｐ': 'p', 'ｑ': 'q', 'ｒ': 'r', 'ｓ': 's', 'ｔ': 't',
        'ｕ': 'u', 'ｖ': 'v', 'ｗ': 'w', 'ｘ': 'x', 'ｙ': 'y',
        'ｚ': 'z',
    }

    # 常见品牌关键词（已迁移到 dim_values 表，保留作为 fallback）
    # BRAND_KEYWORDS - 已弃用，由 DimensionService 管理

    # 常见系列关键词（已迁移到 dim_values 表，保留作为 fallback）
    # SERIES_KEYWORDS - 已弃用，由 DimensionService 管理

    # 常见子系统关键词（已迁移到 dim_values 表，保留作为 fallback）
    # SUBSYSTEM_KEYWORDS - 已弃用，由 DimensionService 管理

    # 常见文档类型关键词（已迁移到 dim_values 表，保留作为 fallback）
    # DOC_TYPE_KEYWORDS - 已弃用，由 DimensionService 管理

    _DOC_TYPE_NORMALIZATION_REPLACEMENTS = [
        ('起动', '启动'),
        ('起動', '启动'),
    ]

    def __init__(self, db: Session):
        """
        初始化预处理器

        Args:
            db: 数据库会话
        """
        self.db = db
        self.synonym_service = SynonymService(db)
        self.pinyin_service = PinyinService(db)
        # 预加载缓存
        self.synonym_service.load_cache()
        self.pinyin_service.load_cache()

    def process(self, query: str) -> QueryResult:
        """
        处理用户查询

        Args:
            query: 原始查询字符串

        Returns:
            QueryResult 结构化结果
        """
        logger.info(f"开始预处理查询: '{query}'")

        # 1. 规范化
        normalized = self._normalize(query)
        logger.debug(f"规范化后: '{normalized}'")

        # 2. 拼音纠错（解决语音识别错误）
        correction_result = self.pinyin_service.correct_query(normalized)
        corrected = correction_result.corrected_query
        pinyin_corrections = correction_result.corrections
        has_correction = correction_result.has_correction

        if has_correction:
            logger.info(f"拼音纠错: '{normalized}' → '{corrected}'")
            for c in pinyin_corrections:
                auto_str = "自动" if c.is_auto else "建议"
                logger.debug(f"  [{auto_str}] {c.original} → {c.corrected} (相似度: {c.similarity:.2f})")

        # 3. 实体抽取（使用纠错后的查询）
        entities = self._extract_entities(corrected)
        logger.debug(f"识别实体: {entities}")

        # 4. 同义词扩展
        expanded, expansions = self._expand_synonyms(corrected, entities)
        logger.debug(f"扩展后: '{expanded}'")

        # 5. Token 提取与维度扩展（用于 FULLTEXT 召回和重排）
        query_tokens = self._extract_tokens(corrected)
        token_expansions = self._expand_tokens(query_tokens, entities)
        expanded_fulltext_query = self._build_expanded_fulltext_query(
            query_tokens, token_expansions, expanded
        )
        logger.info(f"Token扩展: tokens={query_tokens}, 扩展={token_expansions}")
        logger.info(f"扩展FULLTEXT查询: '{expanded_fulltext_query}'")

        result = QueryResult(
            original_query=query,
            normalized_query=normalized,
            corrected_query=corrected,
            expanded_query=expanded,
            entities=entities,
            synonym_expansions=expansions,
            pinyin_corrections=pinyin_corrections,
            has_correction=has_correction,
            query_tokens=query_tokens,
            token_expansions=token_expansions,
            expanded_fulltext_query=expanded_fulltext_query
        )

        logger.info(f"预处理完成: 原始='{query}' → 纠错='{corrected}' → 扩展='{expanded}'")
        return result

    # 排放标准归一化映射（阿拉伯数字 → 中文数字）
    EMISSION_NORMALIZE_MAP = {
        '国2': '国二', '国3': '国三', '国4': '国四',
        '国5': '国五', '国6': '国六',
        '欧2': '欧二', '欧3': '欧三', '欧4': '欧四',
        '欧5': '欧五', '欧6': '欧六',
    }

    # 燃料/能源语义（数据层暂无 fuel_type 字段时，合并到 emissions 参与过滤）
    _FUEL_KEYWORDS_TO_EMISSIONS = [
        ('燃料电池', '新能源'),
        ('氢燃料电池', '新能源'),
        ('氢能源', '新能源'),
        ('新能源', '新能源'),
        ('纯电', '新能源'),
        ('混动', '新能源'),
        ('天然气', '天然气'),
        ('lng', '天然气'),
        ('cng', '天然气'),
        ('燃气', '天然气'),
        ('柴油', '柴油'),
        ('燃油', '柴油'),
        ('汽油', '汽油'),
        ('fcev', '新能源'),
        ('bev', '新能源'),
        ('phev', '新能源'),
        ('ev', '新能源'),
    ]

    def _normalize(self, query: str) -> str:
        """
        规范化查询

        处理：
        1. 去除首尾空白
        2. 合并连续空白为单个空格
        3. 全角转半角
        4. 统一连接符
        5. 排放标准归一化（国5→国五）
        """
        if not query:
            return ""

        text = query.strip()

        # 全角转半角
        for full, half in self.FULLWIDTH_TO_HALFWIDTH.items():
            text = text.replace(full, half)

        # 统一连接符（_、-、.）保留，但确保周围没有多余空格
        text = re.sub(r'\s*([_\-.])\s*', r'\1', text)

        # 合并连续空白
        text = re.sub(r'\s+', ' ', text)

        # 排放标准归一化（国5→国五，国6→国六等）
        for arabic, chinese in self.EMISSION_NORMALIZE_MAP.items():
            text = text.replace(arabic, chinese)

        # 恢复语音输入导致的粘连（如 KLD320 → KL D320）
        text = _restore_collapsed_platform_spacing(text)

        return text.strip()

    @classmethod
    def _extract_fuel_entities_as_emissions(cls, query: str) -> List[str]:
        """从 query 中提取燃料/能源语义，映射到 emissions 实体。"""
        if not query:
            return []

        normalized = unicodedata.normalize('NFKC', str(query)).lower()
        compact = re.sub(r'\s+', '', normalized)

        extracted: List[str] = []
        for keyword, mapped in cls._FUEL_KEYWORDS_TO_EMISSIONS:
            if keyword.isascii():
                # 英文缩写做边界约束，避免在长英文词中误匹配（如 clever 命中 ev）。
                if re.search(rf'(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])', normalized):
                    extracted.append(mapped)
            else:
                if keyword in compact:
                    extracted.append(mapped)

        return extracted

    def _extract_entities(self, query: str) -> Dict[str, List[str]]:
        """
        从查询中抽取实体

        策略：
        1. 使用 DimensionService 字典匹配（覆盖 brand, series, doc_type, subsystem, supplier, emissions, ecu）
        2. 保留正则匹配（用于 model, platform, ecu, drive_type, batch 等结构化模式）
        3. 合并两种结果并去重

        Returns:
            {
                'brand': ['东风'],
                'series': ['天锦'],
                'ecu': ['EDC17'],
                ...
            }
        """
        entities = {
            'brand': [],
            'series': [],
            'model': [],
            'platform': [],
            'ecu': [],
            'supplier': [],
            'emissions': [],
            'subsystem': [],
            'doc_type': [],
            'drive_type': [],
            'batch': [],
            'eng_code': [],
        }

        # 第一层：DimensionService 字典匹配（数据库驱动）
        from app.legacy.services.dimension_service import dimension_service
        if dimension_service.is_loaded:
            dim_matches = dimension_service.match(query)
            for facet_key, values in dim_matches.items():
                if facet_key in entities:
                    entities[facet_key].extend(values)
                elif facet_key == 'fuel_type':
                    entities['emissions'].extend(values)

        # 第二层：正则匹配（补充结构化模式，DimensionService 可能无法覆盖的变体）
        # ECU - 正则能捕获带后缀的变体（如 EDC17CV44, CM2150F 等）
        for pattern in ECU_PATTERNS:
            matches = re.findall(pattern, query, re.IGNORECASE)
            entities['ecu'].extend(matches)

        # 型号 - 结构化代码，依赖正则
        for pattern in MODEL_PATTERNS:
            matches = re.findall(pattern, query, re.IGNORECASE)
            entities['model'].extend(matches)

        # 平台代码 - 结构化代码，依赖正则
        for pattern in PLATFORM_PATTERNS:
            matches = re.findall(pattern, query, re.IGNORECASE)
            entities['platform'].extend(matches)

        # 驱动类型/批次 - 结构化代码，依赖正则
        for pattern in DRIVE_PATTERNS:
            matches = re.findall(pattern, query, re.IGNORECASE)
            entities['drive_type'].extend(matches)

        for pattern in BATCH_PATTERNS:
            matches = re.findall(pattern, query, re.IGNORECASE)
            entities['batch'].extend(matches)

        # 工程命名编码（通用大筐，不参与存在性拦截，只用于召回/过滤/澄清）
        entities['eng_code'].extend(extract_eng_codes(query))

        # 清理：批次/版本号不应混入“型号”
        if entities.get('model') and entities.get('batch'):
            batch_upper = {str(x).upper() for x in entities['batch'] if x}
            entities['model'] = [m for m in entities['model'] if str(m).upper() not in batch_upper]

        # 如果 DimensionService 未加载（降级模式），使用正则作为 fallback
        if not dimension_service.is_loaded:
            logger.warning("DimensionService 未加载，使用正则 fallback 匹配品牌/系列/供应商/排放")
            for pattern in BRAND_PATTERNS:
                matches = re.findall(pattern, query, re.IGNORECASE)
                entities['brand'].extend(matches)
            for pattern in SERIES_PATTERNS:
                matches = re.findall(pattern, query, re.IGNORECASE)
                entities['series'].extend(matches)
            for pattern in SUPPLIER_PATTERNS:
                matches = re.findall(pattern, query, re.IGNORECASE)
                entities['supplier'].extend(matches)
            for pattern in EMISSION_PATTERNS:
                matches = re.findall(pattern, query, re.IGNORECASE)
                entities['emissions'].extend(matches)
            for pattern in DOC_TYPE_PATTERNS:
                matches = re.findall(pattern, query, re.IGNORECASE)
                entities['doc_type'].extend(matches)

        # 燃料/能源关键词兜底：即使 dim_facet 缺失或未命中，也可参与 emissions 过滤。
        entities['emissions'].extend(self._extract_fuel_entities_as_emissions(query))

        # 去重并保持顺序
        for key in entities:
            seen = set()
            unique = []
            for item in entities[key]:
                item_lower = item.lower() if isinstance(item, str) else str(item).lower()
                if item_lower not in seen:
                    seen.add(item_lower)
                    unique.append(item)
            entities[key] = unique

        # 文档类型做轻量归一化（避免 起动/启动 等写法差异）
        if entities.get('doc_type'):
            normalized = []
            seen = set()
            for item in entities['doc_type']:
                value = str(item)
                for src, dst in self._DOC_TYPE_NORMALIZATION_REPLACEMENTS:
                    value = value.replace(src, dst)
                key = value.lower()
                if key in seen:
                    continue
                seen.add(key)
                normalized.append(value)
            entities['doc_type'] = normalized

        return entities

    def _expand_synonyms(
        self,
        query: str,
        entities: Dict[str, List[str]]
    ) -> tuple[str, Dict[str, List[str]]]:
        """
        扩展同义词

        Args:
            query: 规范化后的查询
            entities: 识别出的实体

        Returns:
            (扩展后的查询, 同义词映射)
        """
        expansions = {}
        all_expanded_terms = set()

        # 先添加原始查询的所有词
        original_terms = query.split()
        all_expanded_terms.update(original_terms)

        # 1. 对识别出的实体进行同义词扩展
        for entity_type, entity_values in entities.items():
            for value in entity_values:
                synonyms = self.synonym_service.expand_term(value)
                if len(synonyms) > 1:  # 有同义词
                    expansions[value] = list(synonyms - {value})
                    all_expanded_terms.update(synonyms)

        # 2. 对查询中的每个词也尝试同义词扩展
        for term in original_terms:
            if term not in expansions:  # 避免重复扩展
                synonyms = self.synonym_service.expand_term(term)
                if len(synonyms) > 1:
                    expansions[term] = list(synonyms - {term})
                    all_expanded_terms.update(synonyms)

        # 构建扩展后的查询字符串
        # 策略：原始查询 + 扩展词（去重）
        expanded_query = ' '.join(sorted(all_expanded_terms))

        return expanded_query, expansions

    # ==================== Token 提取与扩展 ====================

    # 停用词（查询中无检索意义的词汇）
    _STOP_WORDS = {
        '我要找', '我想找', '帮我找', '帮忙找', '有没有', '谁有',
        '找一下', '搜一下', '查一下', '看看', '给我',
        '的', '了', '吗', '呢', '啊', '吧', '把', '在', '和', '或',
        '怎么', '如何', '请问', '哪里', '什么', '哪个',
        '要', '想', '能', '可以', '需要',
    }

    # 通用文档词后缀（用于把“李自广电路图”拆成“李自广” + “电路图”）
    # 注意：提取时仍保留原始 token，避免影响现有召回能力。
    _DOC_SUFFIX_TOKENS = (
        '整车电路图',
        '电路图',
        '线路图',
        '原理图',
        '维修手册',
        '操作手册',
        '使用手册',
        '手册',
        '资料',
        '文档',
        '图纸',
    )

    def _extract_tokens(self, query: str) -> List[str]:
        """从用户查询中提取有意义的 token

        策略：
        1. 去除停用词
        2. 按空格分割，对每段提取中文词组和字母数字串
        3. 过滤掉过短的无意义片段

        Args:
            query: 纠错后的查询（如 "东风天龙 530 整车图"）

        Returns:
            token 列表，如 ["东风天龙", "530", "整车图"]
        """
        if not query:
            return []

        # 先移除停用词
        text = query
        for sw in self._STOP_WORDS:
            text = text.replace(sw, ' ')

        # 按空格分割
        segments = text.split()

        tokens = []
        for seg in segments:
            seg = seg.strip()
            if not seg:
                continue
            # 从每个段中提取中文词组和字母数字串
            # 例如 "东风天龙" → ["东风天龙"]
            # 例如 "D530" → ["D530"]
            # 例如 "ECU电路图" → ["ECU", "电路图"]
            parts = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z0-9]+', seg)
            for p in parts:
                if len(p) >= 2 or (p.isdigit() and len(p) >= 3):
                    tokens.append(p)
                    tokens.extend(self._split_doc_suffix_token(p))

        # 去重保持顺序
        seen = set()
        unique_tokens = []
        for t in tokens:
            t_lower = t.lower()
            if t_lower not in seen:
                seen.add(t_lower)
                unique_tokens.append(t)

        return unique_tokens

    def _split_doc_suffix_token(self, token: str) -> List[str]:
        """拆分“前缀 + 文档词后缀”复合 token（不包含原 token 本身）。"""
        if not token:
            return []

        # 仅处理纯中文 token，避免影响型号码/英文串
        if not re.fullmatch(r'[\u4e00-\u9fff]+', token):
            return []

        for suffix in self._DOC_SUFFIX_TOKENS:
            if token.endswith(suffix) and token != suffix:
                prefix = token[:-len(suffix)].strip()
                extra_tokens: List[str] = []
                if len(prefix) >= 2:
                    extra_tokens.append(prefix)
                if len(suffix) >= 2:
                    extra_tokens.append(suffix)
                return extra_tokens

        return []

    def _expand_tokens(
        self,
        tokens: List[str],
        entities: Dict[str, List[str]]
    ) -> Dict[str, List[str]]:
        """利用维度服务扩展 token

        对每个 token，通过 DimensionService 查找其匹配到的维度值的所有 patterns，
        将这些 patterns 作为扩展形式。同时利用 entities 中的品牌/系列信息拆分复合词。

        Args:
            tokens: 原始 token 列表
            entities: 已识别的实体

        Returns:
            {原始token: [原始token + 所有扩展形式]}
            例如: {"530": ["530", "D530"], "整车图": ["整车图", "整车电路图", "电路图"]}
        """
        from app.legacy.services.dimension_service import dimension_service
        if not dimension_service.is_loaded:
            return {t: [t] for t in tokens}

        expansions: Dict[str, List[str]] = {}

        for token in tokens:
            variants: Set[str] = {token}

            # 1. 通过维度服务反查：token 作为 pattern → 找到维度值 → 获取所有 patterns
            result = dimension_service.find_value_by_pattern(token)
            if result:
                facet_key, value, all_patterns = result
                # 添加所有 patterns 和主值本身
                variants.add(value)
                for p in all_patterns:
                    variants.add(p)

            # 2. 复合词拆分：如 "东风天龙" → 利用 entities 的 brand/series 拆分
            brands = entities.get('brand', [])
            series_list = entities.get('series', [])

            for brand in brands:
                if brand in token and token != brand:
                    # token 包含品牌名，拆出品牌和剩余部分
                    remainder = token.replace(brand, '', 1).strip()
                    if len(remainder) >= 2:
                        variants.add(brand)
                        variants.add(remainder)

            for series in series_list:
                if series in token and token != series:
                    remainder = token.replace(series, '', 1).strip()
                    if len(remainder) >= 2:
                        variants.add(series)
                        variants.add(remainder)

            expansions[token] = list(variants)

        return expansions

    def _build_expanded_fulltext_query(
        self,
        tokens: List[str],
        token_expansions: Dict[str, List[str]],
        synonym_expanded: str
    ) -> str:
        """构建扩展后的 FULLTEXT 查询

        将所有原始 token + 扩展形式 + 同义词扩展去重合并。

        Args:
            tokens: 原始 token 列表
            token_expansions: token 扩展映射
            synonym_expanded: 同义词扩展后的查询

        Returns:
            扩展查询字符串，如 "东风 天龙 D530 530 整车图 整车电路图 电路图"
        """
        all_terms: Set[str] = set()

        # 添加 token 扩展的所有形式
        for token in tokens:
            variants = token_expansions.get(token, [token])
            for v in variants:
                if v and len(v) >= 2:
                    all_terms.add(v)

        # 合并同义词扩展中的词
        if synonym_expanded:
            for term in synonym_expanded.split():
                if term and len(term) >= 2:
                    all_terms.add(term)

        if not all_terms:
            return synonym_expanded or ''

        return ' '.join(sorted(all_terms))

    def get_search_variants(self, query: str) -> List[str]:
        """
        生成查询变体（用于多路召回）

        Returns:
            查询变体列表
        """
        result = self.process(query)

        variants = [
            result.normalized_query,  # 原始规范化查询
            result.expanded_query,    # 同义词扩展查询
        ]

        # 如果识别出明确实体，生成实体组合查询
        entity_terms = []
        for entity_type, values in result.entities.items():
            entity_terms.extend(values)

        if entity_terms:
            variants.append(' '.join(entity_terms))

        # 去重
        seen = set()
        unique_variants = []
        for v in variants:
            if v and v not in seen:
                seen.add(v)
                unique_variants.append(v)

        return unique_variants


# 便捷函数
def preprocess_query(db: Session, query: str) -> QueryResult:
    """
    预处理查询（便捷函数）

    Args:
        db: 数据库会话
        query: 原始查询

    Returns:
        QueryResult
    """
    preprocessor = QueryPreprocessor(db)
    return preprocessor.process(query)

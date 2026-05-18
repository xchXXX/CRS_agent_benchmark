"""维度服务 - 管理维度定义和值的加载、匹配、反查、冲突检测

核心功能：
1. 从数据库加载维度配置到内存
2. 对用户查询进行字典匹配（替代硬编码关键词）
3. 父级反查（如 天锦→东风）
4. 冲突检测（如 东风+J6P → J6P 是解放的）
"""

import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Set

logger = logging.getLogger(__name__)


# ==================== 数据结构 ====================

@dataclass
class DimFacetConfig:
    """维度配置"""
    facet_key: str
    facet_name: str
    question: str
    priority: int
    db_field: str
    parent_facet_key: Optional[str]
    match_mode: str  # 'dict' or 'regex'
    specificity: int


@dataclass
class DimValueConfig:
    """维度值配置"""
    id: int
    facet_key: str
    value: str
    patterns: List[str]  # 解析后的匹配模式列表
    parent_value_id: Optional[int]
    parent_value: Optional[str] = None      # 反查后的父值
    parent_facet_key: Optional[str] = None   # 反查后的父维度 key
    sort_order: int = 0


@dataclass
class Conflict:
    """冲突信息"""
    type: str  # 'parent_mismatch'
    facets: List[str]  # 涉及的维度 [parent_facet, child_facet]
    user_values: Dict[str, str]  # 用户输入的值 {facet: value}
    expected_values: Dict[str, str]  # 期望的值 {facet: value}
    message: str  # 冲突提示信息
    options: List[Dict] = field(default_factory=list)  # 给用户的选项


# ==================== 匹配索引条目 ====================

@dataclass
class MatchEntry:
    """匹配索引条目：一个 pattern 对应的维度和值"""
    pattern: str        # 匹配模式文本（小写）
    facet_key: str      # 所属维度
    value: str          # 对应的主值
    pattern_len: int    # 模式长度（用于优先匹配更长的）


class DimensionService:
    """维度服务（单例，启动时加载到内存）"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._facets: Dict[str, DimFacetConfig] = {}
        self._values: Dict[str, Dict[str, DimValueConfig]] = {}  # {facet_key: {value: config}}
        self._values_by_id: Dict[int, DimValueConfig] = {}  # {id: config}
        self._match_entries: List[MatchEntry] = []  # 所有匹配条目（按长度降序）
        self._loaded = False
        self._initialized = True

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def load(self, db_session) -> None:
        """从数据库加载所有维度配置

        Args:
            db_session: SQLAlchemy Session
        """
        from app.legacy.models.database import DimFacet, DimValue

        try:
            # 1. 加载维度定义
            facet_rows = db_session.query(DimFacet).filter_by(is_active=True).order_by(DimFacet.priority).all()
            facets: Dict[str, DimFacetConfig] = {}
            for row in facet_rows:
                facets[row.facet_key] = DimFacetConfig(
                    facet_key=row.facet_key,
                    facet_name=row.facet_name,
                    question=row.question or f'请选择{row.facet_name}：',
                    priority=row.priority or 0,
                    db_field=row.db_field or row.facet_key,
                    parent_facet_key=row.parent_facet_key,
                    match_mode=row.match_mode or 'dict',
                    specificity=row.specificity or 0,
                )

            # 2. 加载维度值
            value_rows = db_session.query(DimValue).filter_by(is_active=True).order_by(DimValue.sort_order.desc()).all()
            values: Dict[str, Dict[str, DimValueConfig]] = {}
            values_by_id: Dict[int, DimValueConfig] = {}

            for row in value_rows:
                patterns = self._parse_patterns(row.match_patterns)
                config = DimValueConfig(
                    id=row.id,
                    facet_key=row.facet_key,
                    value=row.value,
                    patterns=patterns,
                    parent_value_id=row.parent_value_id,
                    sort_order=row.sort_order or 0,
                )
                values.setdefault(row.facet_key, {})[row.value] = config
                values_by_id[row.id] = config

            # 3. 解析父子关系
            for config in values_by_id.values():
                if config.parent_value_id and config.parent_value_id in values_by_id:
                    parent = values_by_id[config.parent_value_id]
                    config.parent_value = parent.value
                    config.parent_facet_key = parent.facet_key

            # 4. 构建匹配索引（按 pattern 长度降序，长模式优先匹配）
            match_entries: List[MatchEntry] = []
            for facet_key, facet_values in values.items():
                for value, config in facet_values.items():
                    for pattern in config.patterns:
                        pattern_lower = pattern.lower()
                        match_entries.append(MatchEntry(
                            pattern=pattern_lower,
                            facet_key=facet_key,
                            value=value,
                            pattern_len=len(pattern_lower),
                        ))

            # 按长度降序排列，确保更长的模式优先匹配
            match_entries.sort(key=lambda e: e.pattern_len, reverse=True)

            # 原子替换
            self._facets = facets
            self._values = values
            self._values_by_id = values_by_id
            self._match_entries = match_entries
            self._loaded = True

            logger.info(
                f"维度服务加载完成: "
                f"{len(facets)}个维度, "
                f"{sum(len(v) for v in values.values())}个值, "
                f"{len(match_entries)}个匹配模式"
            )

        except Exception as e:
            logger.error(f"维度服务加载失败: {e}", exc_info=True)
            raise

    def match(self, query: str) -> Dict[str, List[str]]:
        """从查询中匹配实体

        使用字典子串匹配（不区分大小写），长模式优先。
        对于短的字母数字模式（如 D5、J6），使用边界匹配防止误匹配。

        Args:
            query: 用户查询文本

        Returns:
            {facet_key: [matched_values]}
        """
        if not self._loaded:
            logger.warning("维度服务尚未加载，返回空结果")
            return {}

        query_lower = query.lower()
        result: Dict[str, List[str]] = {}
        # 记录已匹配的文本范围，避免重叠匹配
        matched_spans: List[Tuple[int, int]] = []

        for entry in self._match_entries:
            # 在查询中查找该模式的所有出现位置
            start = 0
            while True:
                pos = query_lower.find(entry.pattern, start)
                if pos == -1:
                    break

                end = pos + entry.pattern_len

                # 边界检查：对于短的字母数字模式（如 D5、J6），检查后续字符
                # 防止 D530 误匹配到 D5，但允许 D5电路图 匹配到 D5
                if self._is_alphanumeric_pattern(entry.pattern) and entry.pattern_len <= 3:
                    if end < len(query_lower) and query_lower[end].isdigit():
                        # 模式后面紧跟数字，跳过此匹配（如 D5 后面是 3）
                        start = end
                        continue

                # 对 platform 维度的纯数字短模式（如 370/530）做边界约束：
                # 仅在左右不与 ASCII 字母/数字相连时命中，避免 3700001 误命中 370 -> D370。
                # 该规则只影响 platform，尽量降低对其他维度和业务的回归风险。
                if entry.facet_key == "platform" and self._is_short_numeric_pattern(entry.pattern):
                    if not self._has_ascii_alnum_boundaries(query_lower, pos, end):
                        start = end
                        continue

                # 检查是否与已匹配的范围重叠（同一维度内允许重叠，不同维度也允许）
                # 但同一维度内避免重复添加相同的值
                if entry.value not in result.get(entry.facet_key, []):
                    result.setdefault(entry.facet_key, []).append(entry.value)

                start = end  # 继续查找下一个出现位置
                break  # 每个模式只需检测一次存在性即可

        return result

    @staticmethod
    def _is_alphanumeric_pattern(pattern: str) -> bool:
        """判断模式是否为纯字母数字组合（如 D5、J6P、KL）"""
        return pattern.isalnum() and any(c.isalpha() for c in pattern) and any(c.isdigit() for c in pattern)

    @staticmethod
    def _is_short_numeric_pattern(pattern: str) -> bool:
        """判断是否为需要边界约束的短数字模式（如 370、530）。"""
        return pattern.isdigit() and len(pattern) <= 4

    @staticmethod
    def _is_ascii_alnum_char(ch: str) -> bool:
        """仅判断 ASCII 字母数字，避免把中文当作 token 粘连字符。"""
        if not ch:
            return False
        return ("0" <= ch <= "9") or ("a" <= ch <= "z") or ("A" <= ch <= "Z")

    @classmethod
    def _has_ascii_alnum_boundaries(cls, text: str, start: int, end: int) -> bool:
        """检查匹配片段左右边界是否与 ASCII 字母数字断开。"""
        left_ok = start == 0 or not cls._is_ascii_alnum_char(text[start - 1])
        right_ok = end >= len(text) or not cls._is_ascii_alnum_char(text[end])
        return left_ok and right_ok

    def get_parent(self, facet_key: str, value: str) -> Optional[Tuple[str, str]]:
        """获取父级值

        Args:
            facet_key: 维度 key（如 'series'）
            value: 维度值（如 '天锦'）

        Returns:
            (parent_facet_key, parent_value) 或 None
        """
        facet_values = self._values.get(facet_key, {})
        config = facet_values.get(value)
        if config and config.parent_value and config.parent_facet_key:
            return (config.parent_facet_key, config.parent_value)
        return None

    def get_root_value_in_facet(self, facet_key: str, value: str) -> str:
        """获取同一维度内的最顶级父值

        用于处理子系列：如 '天龙KL' → '天龙'，'天锦KR' → '天锦'
        递归向上查找，直到父级维度不同或无父级为止。

        Args:
            facet_key: 维度 key（如 'series'）
            value: 维度值（如 '天龙KL'）

        Returns:
            同一维度内的最顶级父值，如果无父级则返回原值
        """
        current_value = value
        visited = {value}  # 防止循环

        while True:
            parent = self.get_parent(facet_key, current_value)
            if not parent:
                break

            parent_facet, parent_value = parent

            # 如果父级维度不同，停止（已到达不同层级）
            if parent_facet != facet_key:
                break

            # 防止循环引用
            if parent_value in visited:
                logger.warning(f"检测到循环引用: {facet_key}={value} -> {parent_value}")
                break

            visited.add(parent_value)
            current_value = parent_value

        if current_value != value:
            logger.debug(f"子系列映射: {facet_key}={value} → {current_value}")

        return current_value

    def get_ancestor_chain(self, facet_key: str, value: str) -> List[Tuple[str, str]]:
        """获取完整的祖先链（跨维度）

        例如：天龙KL → [('series', '天龙'), ('brand', '东风')]

        Args:
            facet_key: 维度 key
            value: 维度值

        Returns:
            祖先链列表 [(facet_key, value), ...]
        """
        chain = []
        current_facet, current_value = facet_key, value
        visited = {(facet_key, value)}

        while True:
            parent = self.get_parent(current_facet, current_value)
            if not parent:
                break

            parent_facet, parent_value = parent

            # 防止循环
            if (parent_facet, parent_value) in visited:
                break

            visited.add((parent_facet, parent_value))
            chain.append((parent_facet, parent_value))
            current_facet, current_value = parent_facet, parent_value

        return chain

    def detect_conflicts(self, entities: Dict[str, List[str]]) -> List[Conflict]:
        """检测实体之间的冲突

        检测逻辑：
        - 如果用户同时输入了子维度和父维度的值
        - 但子维度值的完整祖先链中不包含用户输入的父级
        - 则产生冲突

        例如：brand=['东风'], series=['J6P']
        → J6P 的祖先链是 [('brand', '解放')]，不包含 '东风' → 冲突

        例如：brand=['东风'], series=['天龙KL']
        → 天龙KL 的祖先链是 [('series', '天龙'), ('brand', '东风')]，包含 '东风' → 不冲突

        Args:
            entities: 识别出的实体 {facet_key: [values]}

        Returns:
            冲突列表
        """
        conflicts: List[Conflict] = []

        for facet_key, values in entities.items():
            facet_config = self._facets.get(facet_key)
            if not facet_config or not facet_config.parent_facet_key:
                continue  # 没有父维度，跳过

            parent_facet = facet_config.parent_facet_key
            parent_values_from_user = entities.get(parent_facet, [])

            if not parent_values_from_user:
                continue  # 用户没输入父维度值，无法比较

            for value in values:
                # 获取完整祖先链
                ancestor_chain = self.get_ancestor_chain(facet_key, value)
                if not ancestor_chain:
                    continue

                # 提取祖先链中所有相关维度的值
                ancestor_values_by_facet: Dict[str, Set[str]] = {}
                for anc_facet, anc_value in ancestor_chain:
                    ancestor_values_by_facet.setdefault(anc_facet, set()).add(anc_value.lower())

                # 检查用户输入的父维度值是否在祖先链中
                for user_parent in parent_values_from_user:
                    ancestor_values = ancestor_values_by_facet.get(parent_facet, set())
                    if user_parent.lower() not in ancestor_values:
                        # 找到实际的顶级父值
                        actual_parent = None
                        for anc_facet, anc_value in ancestor_chain:
                            if anc_facet == parent_facet:
                                actual_parent = anc_value
                                break

                        if not actual_parent:
                            continue

                        parent_facet_config = self._facets.get(parent_facet)
                        child_facet_name = facet_config.facet_name
                        parent_facet_name = parent_facet_config.facet_name if parent_facet_config else parent_facet

                        conflict = Conflict(
                            type='parent_mismatch',
                            facets=[parent_facet, facet_key],
                            user_values={parent_facet: user_parent, facet_key: value},
                            expected_values={parent_facet: actual_parent},
                            message=f"「{value}」是{actual_parent}的{child_facet_name}，"
                                    f"与您输入的「{user_parent}」不一致",
                        )
                        conflict.options = self._build_conflict_options(conflict, facet_config)
                        conflicts.append(conflict)

        return conflicts

    def get_facet_config(self, facet_key: str) -> Optional[DimFacetConfig]:
        """获取维度配置"""
        return self._facets.get(facet_key)

    def get_facet_priority(self) -> List[str]:
        """获取维度优先级列表（按 priority 升序排列）"""
        return [f.facet_key for f in sorted(self._facets.values(), key=lambda x: x.priority)]

    def get_facet_question(self, facet_key: str) -> str:
        """获取澄清问题模板"""
        config = self._facets.get(facet_key)
        return config.question if config else f'请选择{facet_key}：'

    def get_db_field(self, facet_key: str) -> str:
        """获取 docs 表中对应的字段名"""
        config = self._facets.get(facet_key)
        return config.db_field if config else facet_key

    def get_facet_field_map(self) -> Dict[str, str]:
        """获取所有维度的字段映射 {facet_key: db_field}"""
        return {key: config.db_field for key, config in self._facets.items()}

    def get_facet_questions(self) -> Dict[str, str]:
        """获取所有维度的问题模板 {facet_key: question}"""
        return {key: config.question for key, config in self._facets.items()}

    def get_all_values(self, facet_key: str) -> List[str]:
        """获取某个维度的所有值"""
        facet_values = self._values.get(facet_key, {})
        return [config.value for config in
                sorted(facet_values.values(), key=lambda x: x.sort_order, reverse=True)]

    def get_children(self, facet_key: str, value: str) -> List[DimValueConfig]:
        """获取某个值的所有子值

        例如：get_children('brand', '东风') → [天锦, 天龙, 大力神, ...]

        Args:
            facet_key: 父维度 key
            value: 父维度值

        Returns:
            子值配置列表
        """
        # 找到父值的 id
        facet_values = self._values.get(facet_key, {})
        parent_config = facet_values.get(value)
        if not parent_config:
            return []

        parent_id = parent_config.id
        children = []
        for facet_values_dict in self._values.values():
            for config in facet_values_dict.values():
                if config.parent_value_id == parent_id:
                    children.append(config)

        return sorted(children, key=lambda x: x.sort_order, reverse=True)

    def find_value_by_pattern(self, pattern: str) -> Optional[Tuple[str, str, List[str]]]:
        """根据 pattern 反查维度值及其所有 patterns

        遍历匹配索引，查找该 pattern 对应的维度值及其所有匹配模式。
        用于查询扩展：如输入 "530" → 找到 platform:D530 → 返回所有 patterns ["D530","530","d530"]

        Args:
            pattern: 匹配模式字符串（如 "530"）

        Returns:
            (facet_key, value, all_patterns) 或 None
            例如: ('platform', 'D530', ['D530', '530', 'd530'])
        """
        if not self._loaded or not pattern:
            return None

        pattern_lower = pattern.lower()

        for entry in self._match_entries:
            if entry.pattern == pattern_lower:
                # 找到匹配，获取该值的所有 patterns
                facet_values = self._values.get(entry.facet_key, {})
                config = facet_values.get(entry.value)
                if config:
                    return (entry.facet_key, entry.value, config.patterns)
                return (entry.facet_key, entry.value, [pattern])

        return None

    def is_known_value(self, facet_key: str, value: str) -> bool:
        """判断某个值是否为已知值"""
        facet_values = self._values.get(facet_key, {})
        return value in facet_values

    def refresh(self, db_session) -> None:
        """刷新缓存

        Args:
            db_session: SQLAlchemy Session
        """
        logger.info("刷新维度服务缓存...")
        self.load(db_session)

    # ==================== 私有方法 ====================

    @staticmethod
    def _parse_patterns(match_patterns: Optional[str]) -> List[str]:
        """解析匹配模式字符串为列表

        Args:
            match_patterns: 逗号分隔的匹配模式，如 "东风,dongfeng,df,DFAC"

        Returns:
            模式列表
        """
        if not match_patterns:
            return []
        return [p.strip() for p in match_patterns.split(',') if p.strip()]

    def _build_conflict_options(self, conflict: Conflict, child_facet_config: DimFacetConfig) -> List[Dict]:
        """为冲突生成用户选项

        策略：更具体的维度优先推荐（specificity 更高的优先）
        key 使用 JSON 编码的 filters，便于 handle_clarify 解析

        Args:
            conflict: 冲突信息
            child_facet_config: 子维度配置

        Returns:
            选项列表
        """
        import json

        parent_facet = conflict.facets[0]  # 如 'brand'
        child_facet = conflict.facets[1]   # 如 'series'
        user_parent = conflict.user_values[parent_facet]       # '东风'
        child_value = conflict.user_values[child_facet]        # 'J6P'
        actual_parent = conflict.expected_values[parent_facet]  # '解放'

        parent_config = self._facets.get(parent_facet)
        parent_specificity = parent_config.specificity if parent_config else 0
        child_specificity = child_facet_config.specificity

        options = []

        # 具体度更高的放前面作为推荐
        if child_specificity >= parent_specificity:
            # 子维度更具体，推荐按子维度走
            filters_1 = {parent_facet: actual_parent, child_facet: child_value}
            filters_2 = {parent_facet: user_parent}
            options = [
                {
                    "key": json.dumps(filters_1, ensure_ascii=False),
                    "label": f"{actual_parent} {child_value}",
                    "description": f"{child_value} 是{actual_parent}的{child_facet_config.facet_name}",
                    "filters": filters_1,
                },
                {
                    "key": json.dumps(filters_2, ensure_ascii=False),
                    "label": f"{user_parent}",
                    "description": f"只按{parent_config.facet_name if parent_config else parent_facet}筛选",
                    "filters": filters_2,
                },
            ]
        else:
            # 父维度更具体（罕见情况），推荐按父维度走
            filters_1 = {parent_facet: user_parent}
            filters_2 = {parent_facet: actual_parent, child_facet: child_value}
            options = [
                {
                    "key": json.dumps(filters_1, ensure_ascii=False),
                    "label": f"{user_parent}",
                    "description": f"按{parent_config.facet_name if parent_config else parent_facet}筛选",
                    "filters": filters_1,
                },
                {
                    "key": json.dumps(filters_2, ensure_ascii=False),
                    "label": f"{actual_parent} {child_value}",
                    "description": f"{child_value} 是{actual_parent}的{child_facet_config.facet_name}",
                    "filters": filters_2,
                },
            ]

        return options

        return options


# 全局单例
dimension_service = DimensionService()

"""澄清服务 - 智能判断是否需要澄清并生成选项"""

import difflib
import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple, Any, Set
from collections import Counter, defaultdict

from app.legacy.services.config_service import config_service
from app.legacy.utils.emissions import expand_emissions_match_tokens, expand_emissions_shorthand

logger = logging.getLogger(__name__)


@dataclass
class ClarifyDecision:
    """澄清决策结果"""
    need: bool                      # 是否需要澄清
    facet: Optional[str]            # 澄清维度
    question: Optional[str]         # 澄清问题
    options: List[str]              # 选项列表
    reason: Optional[str] = None    # 触发原因（调试用）


class ClarifyService:
    """澄清服务 - 分析检索结果并决定是否需要澄清"""

    # ==================== 维度配置（已迁移到数据库） ====================
    # 以下配置现在从 DimensionService 动态获取，保留注释供参考
    # FACET_PRIORITY = ['brand', 'series', 'model', 'doc_type', ...]
    # FACET_QUESTIONS = {'brand': '请选择品牌：', ...}
    # FACET_FIELD_MAP = {'brand': 'brand', 'doc_type': 'doc_types', ...}

    def __init__(self):
        """初始化，维度配置从 DimensionService 动态获取"""
        from app.legacy.services.dimension_service import dimension_service
        self._dim_service = dimension_service
        self.min_options = 2

    @property
    def facet_priority(self) -> list:
        """获取维度优先级列表（从数据库动态获取）"""
        if self._dim_service.is_loaded:
            return self._dim_service.get_facet_priority()
        # fallback: 默认优先级
        return ['brand', 'series', 'model', 'doc_type', 'subsystem', 'ecu', 'supplier', 'emissions']

    @property
    def facet_questions(self) -> dict:
        """获取维度问题模板（从数据库动态获取）"""
        if self._dim_service.is_loaded:
            return self._dim_service.get_facet_questions()
        # fallback: 默认问题模板
        return {
            'brand': '请选择品牌：',
            'series': '请选择车型系列：',
            'model': '请选择型号：',
            'doc_type': '请选择资料类型：',
            'subsystem': '请选择子系统：',
            'ecu': '请选择控制器：',
            'supplier': '请选择供应商：',
            'emissions': '请选择排放标准：'
        }

    @property
    def facet_field_map(self) -> dict:
        """获取维度字段映射（从数据库动态获取）"""
        if self._dim_service.is_loaded:
            return self._dim_service.get_facet_field_map()
        # fallback: 默认字段映射
        return {
            'brand': 'brand',
            'series': 'series',
            'model': 'model',
            'doc_type': 'doc_types',
            'subsystem': 'subsystems',
            'ecu': 'ecus',
            'supplier': 'suppliers',
            'emissions': 'emissions'
        }

    # 选项/文本归一化：用于消除常见写法差异（例如 “起动/启动”）
    _TEXT_NORMALIZATION_REPLACEMENTS: List[Tuple[str, str]] = [
        # 更长的短语优先，避免被更短替换提前命中
        ('起动机', '启动机'),
        ('起動機', '启动机'),
        ('起动', '启动'),
        ('起動', '启动'),
    ]

    @property
    def target_results(self):
        return config_service.get('clarify_target_results', 5)

    @property
    def result_threshold(self):
        return config_service.get('clarify_result_threshold', 5)

    @property
    def score_threshold(self):
        return config_service.get('clarify_score_threshold', 0.5)

    @property
    def score_gap(self):
        return config_service.get('clarify_score_gap', 0.05)

    @property
    def dominant_ratio(self):
        return config_service.get('clarify_dominant_ratio', 0.8)

    @property
    def max_options(self):
        return config_service.get('clarify_max_options', 5)

    @property
    def max_rounds(self):
        return config_service.get('clarify_max_rounds', 5)

    def analyze(
        self,
        results: List[Dict],
        preprocessing: Optional[Dict] = None,
        existing_filters: Optional[Dict] = None,
        clarify_round: int = 0
    ) -> ClarifyDecision:
        """
        分析检索结果，决定是否需要澄清

        Args:
            results: 检索结果列表
            preprocessing: 预处理信息（包含识别出的实体）
            existing_filters: 已有的过滤条件（避免重复澄清）
            clarify_round: 用户已完成的澄清轮次（不含自动实体过滤）

        Returns:
            ClarifyDecision
        """
        existing_filters = existing_filters or {}

        # 检查澄清轮数限制（仅计用户主动澄清的轮次，不含自动实体过滤）
        if clarify_round >= self.max_rounds:
            logger.info(f"已达到最大澄清轮数({self.max_rounds})，不再澄清")
            return self._no_clarify()

        # 无结果，无需澄清
        if not results:
            logger.debug("无结果，无需澄清")
            return self._no_clarify()

        # 结果已经足够精确（<= 目标数量），无需澄清
        if len(results) <= self.target_results:
            logger.debug(f"结果数量({len(results)})已达到目标({self.target_results})，无需澄清")
            return self._no_clarify()

        # 检查是否需要澄清
        need, reason = self._should_clarify(results, preprocessing)

        if not need:
            logger.debug(f"不需要澄清: {reason}")
            return self._no_clarify()

        # 选择澄清维度（可能因为“与查询语义重复”的过滤导致选项不足，因此尝试多个维度）
        attempted_facets: Set[str] = set()
        facet: Optional[str] = None
        options: List[str] = []

        while len(attempted_facets) < len(self.facet_priority):
            facet = self._select_facet(results, preprocessing, existing_filters, exclude_facets=attempted_facets)
            if not facet:
                logger.debug("未找到合适的澄清维度")
                return self._no_clarify()

            # 生成选项（传入预处理信息，用于过滤查询中已包含/语义重复的选项）
            options = self._generate_options(results, facet, preprocessing)
            if len(options) >= self.min_options:
                break

            logger.debug(f"维度 {facet} 的有效选项不足({len(options)})，尝试下一个维度")
            attempted_facets.add(facet)

        if len(options) < self.min_options:
            logger.debug(f"所有维度的选项数量均不足({len(options)})，无需澄清")
            return self._no_clarify()

        logger.info(f"需要澄清: 维度={facet}, 选项={options}, 原因={reason}")

        return ClarifyDecision(
            need=True,
            facet=facet,
            question=self.facet_questions.get(facet, f'请选择{facet}：'),
            options=options,
            reason=reason
        )

    def apply_choice(
        self,
        results: List[Dict],
        facet: str,
        choice: str
    ) -> List[Dict]:
        """
        根据用户选择过滤结果

        Args:
            results: 原始结果列表
            facet: 澄清维度
            choice: 用户选择

        Returns:
            过滤后的结果列表
        """
        # "其他"或"不确定"不过滤
        if choice in ['其他', '不确定']:
            logger.debug(f"用户选择'{choice}'，不进行过滤")
            return results

        filtered = []
        for result in results:
            value = self._get_facet_raw_value(result, facet)
            # 排放标准：支持“国四、五”这类简写展开后再匹配
            if facet == "emissions":
                value = self._expand_emissions_raw_value(value)
                if self._match_emissions_choice(value, choice):
                    filtered.append(result)
            elif self._match_choice(value, choice):
                filtered.append(result)

        logger.info(f"过滤结果: facet={facet}, choice={choice}, 原始数量={len(results)}, 过滤后={len(filtered)}")

        # 如果过滤后无结果，返回原结果
        return filtered if filtered else results

    # ==================== 私有方法 ====================

    def _no_clarify(self) -> ClarifyDecision:
        """返回无需澄清的决策"""
        return ClarifyDecision(
            need=False,
            facet=None,
            question=None,
            options=[],
            reason=None
        )

    @property
    def exact_match_threshold(self):
        """精确匹配分数阈值"""
        return config_service.get('clarify_exact_match_threshold', 0.8)

    @property
    def exact_match_gap(self):
        """精确匹配分数差阈值"""
        return config_service.get('clarify_exact_match_gap', 0.15)

    @property
    def exact_match_gap_ratio(self):
        """精确匹配分数差距比例阈值（默认 0.25 即 25%）

        用于 RRF 分数场景：当 Top1 与 Top2 分数差距比例 >= 此阈值时，
        认为是精确匹配，跳过澄清。
        """
        return config_service.get('clarify_exact_match_gap_ratio', 0.25)

    @property
    def min_valid_rrf_score(self):
        """结果有效性最低 RRF 分数"""
        return config_service.get('min_valid_rrf_score', 0.015)

    def _should_clarify(
        self,
        results: List[Dict],
        preprocessing: Optional[Dict]
    ) -> Tuple[bool, Optional[str]]:
        """
        判断是否需要澄清

        跳过条件（满足则不澄清）：
        - 结果分数过低：top1 分数 < 最低有效分数阈值（无有效结果）
        - 高分精确匹配：top1 分数 >= 阈值 且 (top1 - top2) >= 差值阈值

        触发条件（满足任一）：
        1. 结果数量过多（> threshold）
        2. 置信度不足（top1 < threshold 且 gap < threshold）
        3. 结果分布分散（无主簇）
        4. 查询实体不足
        """
        # 前置条件0：结果分数过低 - 不触发澄清（无有效结果）
        if len(results) >= 1:
            top1_score = results[0].get('score', 0)
            if top1_score < self.min_valid_rrf_score:
                logger.info(f"结果分数过低，跳过澄清: top1={top1_score:.4f} < {self.min_valid_rrf_score}")
                return False, 'invalid_results'

        # 前置条件1：高分精确匹配检测 - 跳过澄清
        if len(results) >= 1:
            top1_score = results[0].get('score', 0)
            top2_score = results[1].get('score', 0) if len(results) >= 2 else 0

            # 原有条件：绝对分数阈值（用于高分场景）
            if top1_score >= self.exact_match_threshold and (top1_score - top2_score) >= self.exact_match_gap:
                logger.info(f"高分精确匹配，跳过澄清: top1={top1_score:.3f}, gap={top1_score - top2_score:.3f}")
                return False, 'exact_match'

            # 新增条件：基于分数比例差距（适用于 RRF 分数范围 0.01-0.05）
            # 当 Top1 与 Top2 差距比例 >= 25% 时，认为是精确匹配
            if len(results) >= 2 and top1_score > 0:
                gap_ratio = (top1_score - top2_score) / top1_score
                if gap_ratio >= self.exact_match_gap_ratio:
                    logger.info(f"分数差距比例高，跳过澄清: top1={top1_score:.4f}, gap_ratio={gap_ratio:.1%}")
                    return False, 'exact_match_by_ratio'

        # 条件1：结果数量过多
        if len(results) > self.result_threshold:
            return True, 'result_count_high'

        # 条件2：置信度不足
        if len(results) >= 2:
            top1_score = results[0].get('score', 0)
            top2_score = results[1].get('score', 0)
            if top1_score < self.score_threshold:
                if (top1_score - top2_score) < self.score_gap:
                    return True, 'low_confidence'

        # 条件3：结果分布分散
        if self._is_dispersed(results):
            return True, 'dispersed_results'

        # 条件4：查询实体不足
        if preprocessing:
            entities = preprocessing.get('entities', {})
            entity_count = sum(1 for v in entities.values() if v)
            if entity_count < 2 and len(results) > 10:
                return True, 'insufficient_entities'

        return False, 'no_trigger'

    def _is_dispersed(self, results: List[Dict]) -> bool:
        """判断结果是否分散"""
        # 取前20个结果进行分析
        top_results = results[:20]

        for facet in ['brand', 'series']:
            values = [self._get_facet_value(r, facet) for r in top_results]
            values = [v for v in values if v]  # 过滤空值

            if not values:
                continue

            counter = Counter(values)
            total = len(values)
            max_count = counter.most_common(1)[0][1] if counter else 0

            # 主簇占比低于阈值，认为分散
            if max_count / total < self.dominant_ratio:
                return True

        return False

    def _select_facet(
        self,
        results: List[Dict],
        preprocessing: Optional[Dict],
        existing_filters: Optional[Dict],
        exclude_facets: Optional[Set[str]] = None
    ) -> Optional[str]:
        """
        选择最佳澄清维度

        策略：
        1. 排除已过滤的维度
        2. 排除查询中已明确的实体
        3. 按优先级选择区分度最高的维度
        """
        existing_filters = existing_filters or {}
        query_entities = {}
        if preprocessing:
            query_entities = preprocessing.get('entities', {})

        top_results = results[:50]  # 取前50分析

        best_facet = None
        best_score = 0

        for facet in self.facet_priority:
            if exclude_facets and facet in exclude_facets:
                continue
            # 排除已过滤的维度
            if facet in existing_filters:
                continue

            # 排除查询中已明确的实体（如果该维度已识别出值）
            if query_entities.get(facet):
                continue

            # 计算该维度的区分度
            score = self._calculate_discrimination(top_results, facet)

            logger.debug(f"维度 {facet} 区分度: {score:.3f}")

            if score > best_score:
                best_score = score
                best_facet = facet

        return best_facet

    def _normalize_for_compare(self, text: str) -> str:
        """
        用于“是否重复/是否同义”的轻量归一化：
        - NFKC（全角半角/兼容字符）
        - 小写、去空白、去常见标点
        - 领域内常见同义写法替换（例如 起动→启动）
        """
        if not text:
            return ""

        normalized = unicodedata.normalize("NFKC", str(text)).strip().lower()
        normalized = re.sub(r"\s+", "", normalized)
        normalized = re.sub(r"[·•\-_/.(),，。:：;；!?！？（）【】\\[\\]{}]+", "", normalized)

        for src, dst in self._TEXT_NORMALIZATION_REPLACEMENTS:
            normalized = normalized.replace(src.lower(), dst.lower())

        return normalized

    def _get_query_norm_texts(self, preprocessing: Optional[Dict]) -> Set[str]:
        """获取多个版本的查询文本，用于判断选项是否已在查询中明确表达。"""
        if not preprocessing:
            return set()

        texts = [
            preprocessing.get("original_query", ""),
            preprocessing.get("normalized_query", ""),
            preprocessing.get("corrected_query", ""),
            preprocessing.get("expanded_query", ""),
        ]
        return {t for t in (self._normalize_for_compare(x) for x in texts) if t}

    def _is_redundant_option(self, option: str, query_norm_texts: Set[str]) -> bool:
        """
        判断选项是否与用户查询“语义重复/等价”：
        - 选项归一化后出现在任意查询文本中（子串）
        - 或（可选）与查询高度相似且长度差很小
        """
        option_norm = self._normalize_for_compare(option)
        if not option_norm:
            return True

        for q in query_norm_texts:
            if option_norm in q:
                return True

            # 避免把“更具体的选项”误判为重复（如 电路图 vs 整车电路图）
            # 只有当长度差极小，才认为 query ⊂ option 也属于重复
            if q and q in option_norm and (len(option_norm) - len(q)) <= 1:
                return True

            similarity = difflib.SequenceMatcher(None, option_norm, q).ratio()
            if similarity >= config_service.get("clarify_option_similarity_threshold", 0.93) and abs(
                len(option_norm) - len(q)
            ) <= 1:
                return True

        return False

    @property
    def min_facet_coverage(self):
        """维度最低覆盖率阈值（低于此值不使用该维度澄清）"""
        return config_service.get('clarify_min_facet_coverage', 0.3)

    @property
    def top_n_check_count(self):
        """TopN 覆盖率检查的结果数量"""
        return config_service.get('clarify_top_n_check_count', 5)

    @property
    def min_top_n_coverage(self):
        """TopN 结果的最低覆盖率阈值（低于此值不使用该维度澄清）

        这个阈值用于保护高分结果：如果 Top5 中大部分结果缺少某维度值，
        说明用户想要的结果可能没有这个维度的数据，不应使用该维度澄清。
        """
        return config_service.get('clarify_min_top_n_coverage', 0.6)

    def _calculate_discrimination(
        self,
        results: List[Dict],
        facet: str
    ) -> float:
        """
        计算维度的区分度

        区分度 = 唯一值数量系数 * 覆盖率
        理想情况：有多个选项（2-5个），每个选项覆盖相当比例的结果

        保护机制：
        1. 全局覆盖率检查：如果大多数结果没有该字段，跳过
        2. TopN 覆盖率检查：如果高分结果（Top5）大多没有该字段，跳过
           这样可以避免把用户最想要的结果过滤掉
        """
        values, coverage = self._collect_facet_values(results, facet)
        if not values:
            return 0

        # 检查1：全局覆盖率过低时，不使用该维度澄清
        if coverage < self.min_facet_coverage:
            logger.debug(f"维度 {facet} 全局覆盖率过低({coverage:.1%} < {self.min_facet_coverage:.0%})，跳过")
            return 0

        # 检查2：TopN 覆盖率检查（保护高分结果）
        # 如果 Top5 中大部分结果没有该维度值，说明用户想要的结果可能缺少这个字段
        top_n = results[:self.top_n_check_count]
        if top_n:
            top_n_with_value = 0
            for r in top_n:
                raw_value = self._get_facet_raw_value(r, facet)
                if raw_value and (not isinstance(raw_value, list) or len(raw_value) > 0):
                    top_n_with_value += 1

            top_n_coverage = top_n_with_value / len(top_n)
            if top_n_coverage < self.min_top_n_coverage:
                logger.debug(
                    f"维度 {facet} Top{len(top_n)} 覆盖率过低"
                    f"({top_n_with_value}/{len(top_n)} = {top_n_coverage:.0%} < {self.min_top_n_coverage:.0%})，跳过"
                )
                return 0

        counter = Counter(values)
        unique_count = len(counter)

        # 区分度评分
        if unique_count < 2:
            return 0  # 只有1个值，无需澄清

        if unique_count > 10:
            score = 0.5  # 选项过多，区分度降低
        elif unique_count >= 2 and unique_count <= 5:
            score = 1.0  # 2-5个选项最佳
        else:
            score = 0.7  # 6-10个选项

        return score * coverage

    def _generate_options(
        self,
        results: List[Dict],
        facet: str,
        preprocessing: Optional[Dict] = None
    ) -> List[str]:
        """
        生成澄清选项

        策略：
        1. 统计前50个结果中该维度的值分布
        2. 过滤掉查询中已包含的词（避免重复询问）
        3. 根据父子关系过滤（如已识别品牌，只显示该品牌下的系列）
        4. 取Top N高频值
        5. 添加"其他"选项
        """
        top_results = results[:50]
        values, _coverage = self._collect_facet_values(top_results, facet)

        # 先对候选值做归一化去重（例如 起动原理图/启动原理图）
        canonical_counter: Counter[str] = Counter()
        variant_counters = defaultdict(Counter)
        for v in values:
            canonical = self._normalize_for_compare(v)
            if not canonical:
                continue
            canonical_counter[canonical] += 1
            variant_counters[canonical][v] += 1

        canonical_to_display = {
            canonical: variants.most_common(1)[0][0] for canonical, variants in variant_counters.items()
        }

        query_norm_texts = self._get_query_norm_texts(preprocessing)

        # 获取父子关系过滤的合法选项集合
        valid_child_values = self._get_valid_child_values(facet, preprocessing)

        # 过滤选项：排除查询中已明确提及的词 + 父子关系过滤
        filtered_options: List[str] = []
        for canonical, _count in canonical_counter.most_common(self.max_options + 10):  # 多取一些，以防过滤后不足
            item = canonical_to_display.get(canonical, canonical)

            # 父子关系过滤：如果已识别父维度值，只保留该父值下的子选项
            if valid_child_values is not None:
                if not self._is_valid_child_option(item, valid_child_values):
                    logger.debug(f"过滤选项 '{item}'：不属于已识别的父维度")
                    continue

            if query_norm_texts and self._is_redundant_option(item, query_norm_texts):
                logger.debug(f"过滤选项 '{item}'：与查询语义重复")
                continue

            filtered_options.append(item)
            if len(filtered_options) >= self.max_options - 1:
                break

        # 如果还有其他值，添加"其他"
        remaining_count = len(canonical_counter) - len(filtered_options)
        if remaining_count > 0 and len(filtered_options) >= self.min_options:
            filtered_options.append('其他')

        return filtered_options

    def _get_valid_child_values(
        self,
        facet: str,
        preprocessing: Optional[Dict]
    ) -> Optional[Set[str]]:
        """
        根据父子关系获取合法的子选项集合

        Args:
            facet: 当前澄清维度（如 'series'）
            preprocessing: 预处理信息（包含已识别的实体）

        Returns:
            合法子选项集合，如果无父子约束则返回 None
        """
        if not preprocessing or not self._dim_service.is_loaded:
            return None

        # 获取当前维度的配置
        facet_config = self._dim_service.get_facet_config(facet)
        if not facet_config or not facet_config.parent_facet_key:
            return None  # 无父维度，不需要过滤

        parent_facet = facet_config.parent_facet_key
        query_entities = preprocessing.get('entities', {})

        # 检查是否已识别父维度的值
        parent_values = query_entities.get(parent_facet, [])
        if not parent_values:
            return None  # 未识别父维度值，不过滤

        # 收集所有合法的子选项
        valid_children: Set[str] = set()
        for parent_value in parent_values:
            children = self._dim_service.get_children(parent_facet, parent_value)
            for child in children:
                valid_children.add(child.value)
                # 也添加归一化版本，便于匹配
                valid_children.add(self._normalize_for_compare(child.value))

        logger.debug(f"父维度 {parent_facet}={parent_values}，合法子选项: {valid_children}")
        return valid_children if valid_children else None

    def _is_valid_child_option(self, option: str, valid_child_values: Set[str]) -> bool:
        """
        判断选项是否属于合法的子选项集合

        Args:
            option: 候选选项
            valid_child_values: 合法子选项集合

        Returns:
            是否合法
        """
        # 精确匹配
        if option in valid_child_values:
            return True

        # 归一化匹配
        option_norm = self._normalize_for_compare(option)
        if option_norm in valid_child_values:
            return True

        # 子串匹配（如 "天锦KR" 包含 "天锦"）
        for valid in valid_child_values:
            if valid and len(valid) >= 2:
                if valid in option or option in valid:
                    return True

        return False

    def _get_facet_value(self, result: Dict, facet: str) -> Optional[str]:
        """获取结果中指定维度的值"""
        # 获取字段名
        field_name = self.facet_field_map.get(facet, facet)

        value = result.get(field_name)

        # 如果是列表，取第一个
        if isinstance(value, list):
            return value[0] if value else None

        return value

    def _collect_facet_values(self, results: List[Dict], facet: str) -> Tuple[List[str], float]:
        """收集某个维度在结果集中的值（支持 list 字段展开）。

        Returns:
            (values, coverage)
            - values: 展开后的值列表（用于统计频次/选项）
            - coverage: 有该维度值的结果占比（0-1）
        """
        values: List[str] = []
        with_value = 0

        for r in results:
            raw = self._get_facet_raw_value(r, facet)
            if raw is None:
                continue

            if isinstance(raw, list):
                if facet == "emissions":
                    items = []
                    for x in raw:
                        if not x:
                            continue
                        expanded = expand_emissions_shorthand(str(x))
                        items.extend(expanded if expanded else [str(x)])
                else:
                    items = [str(x) for x in raw if x]
            else:
                if facet == "emissions" and raw:
                    expanded = expand_emissions_shorthand(str(raw))
                    items = expanded if expanded else [str(raw)]
                else:
                    items = [str(raw)] if raw else []

            if not items:
                continue

            with_value += 1
            values.extend(items)

        coverage = (with_value / len(results)) if results else 0.0
        return values, coverage

    def _expand_emissions_raw_value(self, value: Any) -> Any:
        """把 emissions 的原始值（str/list）展开成 list[str] 便于统一匹配。"""
        if value is None:
            return None

        if isinstance(value, list):
            expanded_all: List[str] = []
            for v in value:
                if not v:
                    continue
                expanded = expand_emissions_shorthand(str(v))
                expanded_all.extend(expanded if expanded else [str(v)])
            return expanded_all

        if not value:
            return value

        expanded = expand_emissions_shorthand(str(value))
        return expanded if expanded else value

    def _get_facet_raw_value(self, result: Dict, facet: str) -> Any:
        """获取结果中指定维度的原始值（用于过滤匹配，保留列表）。"""
        field_name = self.facet_field_map.get(facet, facet)
        return result.get(field_name)

    def _match_choice(self, value: Any, choice: str) -> bool:
        """判断值是否匹配用户选择"""
        if not value:
            return False

        choice_lower = choice.lower()
        choice_norm = self._normalize_for_compare(choice)

        if isinstance(value, list):
            for v in value:
                v_str = str(v)
                if choice_lower in v_str.lower():
                    return True
                if choice_norm and choice_norm in self._normalize_for_compare(v_str):
                    return True
            return False

        value_str = str(value)
        if choice_lower in value_str.lower():
            return True
        return bool(choice_norm and choice_norm in self._normalize_for_compare(value_str))

    def _match_emissions_choice(self, value: Any, choice: str) -> bool:
        """emissions 维度匹配：支持国标简写与燃料语义同类词匹配。"""
        if not value:
            return False

        wanted = set(expand_emissions_match_tokens(choice))
        if not wanted:
            return False

        if isinstance(value, list):
            for item in value:
                got = set(expand_emissions_match_tokens(str(item)))
                if got & wanted:
                    return True
            return False

        got = set(expand_emissions_match_tokens(str(value)))
        return bool(got & wanted)


# ==================== 便捷函数 ====================

def apply_filters(results: List[Dict], filters: Dict[str, str]) -> List[Dict]:
    """
    应用多个过滤条件

    Args:
        results: 结果列表
        filters: 过滤条件字典 {facet: choice}

    Returns:
        过滤后的结果
    """
    if not filters:
        return results

    service = ClarifyService()
    filtered = results

    for facet, choice in filters.items():
        filtered = service.apply_choice(filtered, facet, choice)

    return filtered


# ==================== 测试代码 ====================

if __name__ == "__main__":
    import logging

    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    print("=" * 80)
    print("澄清服务测试")
    print("=" * 80)

    # 模拟检索结果
    mock_results = [
        {'file_id': '1', 'brand': '东风', 'series': '天锦', 'score': 0.8, 'ecus': ['EDC17']},
        {'file_id': '2', 'brand': '东风', 'series': '天龙', 'score': 0.75, 'ecus': ['MD1CE']},
        {'file_id': '3', 'brand': '解放', 'series': 'J6P', 'score': 0.7, 'ecus': ['EDC17']},
        {'file_id': '4', 'brand': '解放', 'series': 'J6L', 'score': 0.65, 'ecus': ['CM2150']},
        {'file_id': '5', 'brand': '重汽', 'series': '豪沃', 'score': 0.6, 'ecus': ['EDC17']},
        {'file_id': '6', 'brand': '重汽', 'series': '汕德卡', 'score': 0.55, 'ecus': ['MD1CE']},
        {'file_id': '7', 'brand': '陕汽', 'series': '德龙', 'score': 0.5, 'ecus': ['CM2150']},
        {'file_id': '8', 'brand': '东风', 'series': '天锦', 'score': 0.45, 'ecus': ['EDC17']},
        {'file_id': '9', 'brand': '东风', 'series': '天龙', 'score': 0.4, 'ecus': ['MD1CE']},
        {'file_id': '10', 'brand': '解放', 'series': 'J7', 'score': 0.35, 'ecus': ['CM876']},
    ] * 3  # 复制3份，模拟30个结果

    # 创建服务
    service = ClarifyService()

    # 测试1：分析是否需要澄清
    print("\n测试1：分析30个分散结果")
    decision = service.analyze(mock_results)
    print(f"  需要澄清: {decision.need}")
    print(f"  维度: {decision.facet}")
    print(f"  问题: {decision.question}")
    print(f"  选项: {decision.options}")
    print(f"  原因: {decision.reason}")

    # 测试2：应用用户选择
    print("\n测试2：用户选择'东风'")
    filtered = service.apply_choice(mock_results, 'brand', '东风')
    print(f"  过滤前: {len(mock_results)} 个结果")
    print(f"  过滤后: {len(filtered)} 个结果")

    # 测试3：继续澄清
    print("\n测试3：继续分析过滤后的结果")
    decision2 = service.analyze(filtered, existing_filters={'brand': '东风'})
    print(f"  需要澄清: {decision2.need}")
    print(f"  维度: {decision2.facet}")
    print(f"  选项: {decision2.options}")

    print("\n" + "=" * 80)
    print("测试完成")
    print("=" * 80)

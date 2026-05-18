"""存在性验证服务

基于搜索结果判断用户查询的资料是否存在于资料库中。
不维护独立维度表，通过分析 top-50 结果推断库的边界。
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from collections import Counter
import logging

from app.legacy.services.config_service import config_service

logger = logging.getLogger(__name__)


@dataclass
class ExistenceResult:
    """存在性验证结果"""
    status: str  # 'exact_match' | 'partial_match' | 'no_match'
    query_entities: Dict[str, List[str]] = field(default_factory=dict)
    matched_entities: Dict[str, List[str]] = field(default_factory=dict)
    unmatched_entities: Dict[str, List[str]] = field(default_factory=dict)
    suggestions: Dict[str, List[str]] = field(default_factory=dict)
    message: Optional[str] = None
    should_continue: bool = True


class ExistenceValidator:
    """存在性验证器"""

    VALIDATE_FACETS = ['brand', 'series', 'model']
    FACET_NAMES = {'brand': '品牌', 'series': '系列', 'model': '型号'}
    MIN_RESULTS = 3
    MATCH_THRESHOLD = 0.05  # 5% 的结果包含该值即认为匹配
    SUGGESTION_LIMIT = 5

    # 通用文档类型词（不作为覆盖度判断依据）
    DOC_TYPE_WORDS = {
        '电路图', '线路图', '原理图', '整车电路图', '手册', '维修手册', '拆装手册',
        '资料', '文档', '图纸', '零件目录', '配件目录', '故障码', '说明书',
        '培训资料', '维修资料', '技术资料', '操作手册', '使用手册',
    }

    # 通用搜索动作词
    ACTION_WORDS = {'搜索', '查找', '找', '搜', '查', '下载', '看', '要'}

    def validate(self, results: List[Dict], preprocessing: Optional[Dict]) -> ExistenceResult:
        """验证查询实体是否存在于资料库"""

        # 无预处理或无结果，跳过验证
        if not preprocessing or not results:
            return ExistenceResult(status='exact_match', should_continue=True)

        # 新增：基于分数的前置检查
        # 如果 top1 分数过低，直接判定为无匹配（即使有结果，也是不相关的）
        min_valid_score = config_service.get('min_valid_rrf_score', 0.015)
        if results:
            top_score = results[0].get('score', 0)
            if top_score < min_valid_score:
                logger.info(f"存在性验证: 分数过低 ({top_score:.4f} < {min_valid_score})，判定为无匹配")
                return ExistenceResult(
                    status='no_match',
                    query_entities=preprocessing.get('entities', {}),
                    message="未找到与查询相关的资料，请检查关键词或尝试其他搜索词",
                    should_continue=False
                )

        query_entities = preprocessing.get('entities', {})

        # 提取可验证的实体
        key_entities = {
            f: v for f, v in query_entities.items()
            if v and f in self.VALIDATE_FACETS
        }

        # 无可验证实体时，进行关键词覆盖度检查
        # 捕获"特斯拉电路图"类查询：品牌不在实体库中，结果中也完全不存在
        if not key_entities:
            remaining_keywords = self._extract_unrecognized_keywords(preprocessing)
            if remaining_keywords and not self._check_keyword_coverage(results, remaining_keywords):
                # 新增：检查向量搜索是否有高置信度匹配
                # 如果向量搜索找到了语义相关的结果（如"保鲜盒"→"保险盒"），信任向量搜索
                if self._has_semantic_confidence(results):
                    logger.info(f"关键词覆盖检查: 「{remaining_keywords}」字面不匹配，但向量搜索有高置信度结果，信任语义匹配")
                    return ExistenceResult(status='exact_match', query_entities=query_entities, should_continue=True)

                logger.info(f"关键词覆盖检查: 未识别关键词「{remaining_keywords}」在结果中不存在")
                distributions = self._analyze_distributions(results)
                brand_suggestions = [
                    v for v, _ in distributions.get('brand', Counter()).most_common(self.SUGGESTION_LIMIT) if v
                ]
                suggestions = {'brand': brand_suggestions} if brand_suggestions else {}
                msg = f"资料库中暂无「{remaining_keywords}」的相关资料"
                if brand_suggestions:
                    msg += f"，目前收录的品牌有：{'、'.join(brand_suggestions[:5])}"
                return ExistenceResult(
                    status='no_match',
                    query_entities=query_entities,
                    unmatched_entities={'keyword': [remaining_keywords]},
                    suggestions=suggestions,
                    message=msg,
                    should_continue=False
                )
            return ExistenceResult(status='exact_match', query_entities=query_entities, should_continue=True)

        # 结果太少
        if len(results) < self.MIN_RESULTS:
            # 结果数少，但如果向量搜索有高置信度匹配，仍然继续
            if not self._has_semantic_confidence(results):
                # 结果数不足但 > 0 时，不直接拒绝，继续走实体匹配逻辑做最终判断。
                # 典型场景：外部搜索结果经过实体过滤后只剩 1-2 条，但确实是正确匹配。
                if len(results) > 0:
                    logger.info(f"结果数较少({len(results)})，无语义置信度，继续实体匹配验证")
                else:
                    return self._build_no_match(key_entities, query_entities, {})
            else:
                logger.info(f"结果数较少({len(results)})，但向量搜索有高置信度结果，继续验证")

        # 分析结果分布
        distributions = self._analyze_distributions(results)

        # 逐维度验证
        matched, unmatched, suggestions = {}, {}, {}

        for facet, values in key_entities.items():
            dist = distributions.get(facet, Counter())
            for val in values:
                if self._is_matched(val, dist, len(results)):
                    matched.setdefault(facet, []).append(val)
                else:
                    unmatched.setdefault(facet, []).append(val)
                    # 生成建议
                    sug = [v for v, _ in dist.most_common(self.SUGGESTION_LIMIT) if v]
                    if sug:
                        suggestions[facet] = sug

        # 文件名兜底检查：结构化字段未匹配时，检查 top 结果的文件名
        # 外部搜索结果的结构化字段由文件名解析得到，可能不完整（如 D310 未被维度词典收录为 model）
        # 但文件名中确实包含该实体文本，此时不应误报为"未找到"
        if unmatched:
            rescued = {}
            for facet, vals in list(unmatched.items()):
                for val in vals:
                    val_lower = val.lower()
                    if self._entity_in_filenames(results[:20], val_lower):
                        rescued.setdefault(facet, []).append(val)
                        logger.info(f"存在性验证: {facet}='{val}' 结构化字段未匹配，但文件名中存在，视为已匹配")

            # 将兜底匹配的实体从 unmatched 移到 matched
            for facet, vals in rescued.items():
                for val in vals:
                    unmatched[facet].remove(val)
                    matched.setdefault(facet, []).append(val)
                # 清理空列表
                if not unmatched[facet]:
                    del unmatched[facet]
                    suggestions.pop(facet, None)

        # 判断状态
        status = self._determine_status(matched, unmatched)
        message = self._generate_message(status, matched, unmatched, suggestions)

        return ExistenceResult(
            status=status,
            query_entities=query_entities,
            matched_entities=matched,
            unmatched_entities=unmatched,
            suggestions=suggestions,
            message=message,
            should_continue=(status != 'no_match')
        )

    def _analyze_distributions(self, results: List[Dict]) -> Dict[str, Counter]:
        """分析结果中各维度的值分布"""
        distributions = {}
        for facet in self.VALIDATE_FACETS:
            values = [r.get(facet) for r in results if r.get(facet)]
            distributions[facet] = Counter(values)
        return distributions

    @staticmethod
    def _entity_in_filenames(results: List[Dict], val_lower: str) -> bool:
        """检查实体文本是否出现在结果文件名中"""
        for r in results:
            fn = (r.get('filename') or '').lower()
            if val_lower in fn:
                return True
        return False

    def _is_matched(self, query_val: str, dist: Counter, total: int) -> bool:
        """判断查询值是否匹配"""
        if not dist:
            return False
        q_lower = query_val.lower()
        for d_val, count in dist.items():
            d_lower = str(d_val).lower()
            # 精确匹配或包含关系
            if q_lower == d_lower or q_lower in d_lower or d_lower in q_lower:
                return count / total >= self.MATCH_THRESHOLD
        return False

    def _determine_status(self, matched: Dict, unmatched: Dict) -> str:
        """判断整体状态"""
        if not unmatched:
            return 'exact_match'
        # 品牌不匹配 = no_match
        if 'brand' in unmatched and 'brand' not in matched:
            return 'no_match'
        return 'partial_match'

    def _generate_message(self, status: str, matched: Dict, unmatched: Dict, suggestions: Dict) -> Optional[str]:
        """生成提示消息"""
        if status == 'exact_match':
            return None

        if status == 'no_match':
            parts = [f"{self.FACET_NAMES.get(f, f)}「{'、'.join(v)}」" for f, v in unmatched.items()]
            msg = f"资料库中暂无{'/'.join(parts)}的相关资料"
            if suggestions:
                sug_parts = [f"{self.FACET_NAMES.get(f, f)}：{'、'.join(v[:3])}" for f, v in suggestions.items()]
                msg += f"，但有以下资料可供参考：{'; '.join(sug_parts)}"
            return msg

        # partial_match
        if unmatched:
            parts = [f"{self.FACET_NAMES.get(f, f)}「{'、'.join(v)}」" for f, v in unmatched.items()]
            msg = f"未找到{'/'.join(parts)}"
            for f in unmatched:
                if f in suggestions:
                    msg += f"（有：{'、'.join(suggestions[f][:3])}）"
            return msg
        return None

    def _build_no_match(self, key_entities: Dict, query_entities: Dict, suggestions: Dict) -> ExistenceResult:
        """构建无匹配结果"""
        return ExistenceResult(
            status='no_match',
            query_entities=query_entities,
            unmatched_entities=key_entities,
            suggestions=suggestions,
            message="未找到相关资料，请检查输入或尝试其他关键词",
            should_continue=False
        )

    def _extract_unrecognized_keywords(self, preprocessing: Dict) -> str:
        """从查询中提取未被实体识别的关键词

        去掉已识别的实体文本和通用文档类型词后，剩余的就是"未知关键词"。
        例如：'特斯拉电路图' → 实体识别出 doc_type=[电路图] → 剩余 '特斯拉'
        """
        original_query = preprocessing.get('original_query', '')
        if not original_query:
            return ''

        remaining = original_query

        # 去除已识别的实体文本
        entities = preprocessing.get('entities', {})
        for entity_type, values in entities.items():
            for v in values:
                remaining = remaining.replace(v, '')

        # 去除通用文档类型词
        for word in self.DOC_TYPE_WORDS:
            remaining = remaining.replace(word, '')

        # 去除通用动作词
        for word in self.ACTION_WORDS:
            remaining = remaining.replace(word, '')

        # 清理空白和标点
        remaining = remaining.strip().strip('，。、？！ ')

        return remaining

    def _check_keyword_coverage(self, results: List[Dict], keywords: str) -> bool:
        """检查未识别关键词是否在搜索结果中出现

        遍历 top 结果的标题、品牌、系列等字段，检查关键词是否有匹配。
        如果完全没有匹配，说明该关键词对应的内容在资料库中不存在。

        Args:
            results: 搜索结果列表
            keywords: 未识别的关键词文本

        Returns:
            True 表示有覆盖（可能有效），False 表示无覆盖（无效搜索）
        """
        if not keywords:
            return True

        kw_lower = keywords.lower()
        check_fields = ['filename', 'brand', 'series', 'model', 'hierarchy_full']
        top_results = results[:30]

        for r in top_results:
            for field_name in check_fields:
                val = r.get(field_name)
                if not val:
                    continue
                val_str = str(val).lower()
                if kw_lower in val_str or val_str in kw_lower:
                    return True

        return False

    def _has_semantic_confidence(self, results: List[Dict]) -> bool:
        """检查向量搜索是否有高置信度匹配

        当用户输入的关键词与结果字面不匹配，但向量搜索找到了语义相关的结果时，
        应该信任向量搜索（例如"保鲜盒"→"保险盒"、"田径"→"天锦"）。

        判断标准：
        1. Top1 结果有语义排名（说明向量搜索有贡献）
        2. 语义分数 >= 阈值（说明向量搜索有一定置信度）

        Args:
            results: 搜索结果列表

        Returns:
            True 表示向量搜索有高置信度匹配，应该信任语义结果
        """
        if not results:
            return False

        top_result = results[0]

        # 外部API结果：使用 match_score 替代语义分数
        if str(top_result.get('file_id', '')).startswith('ggzj_'):
            match_score = top_result.get('match_score', 0)
            if match_score >= 0.5:
                logger.debug(f"外部结果置信度检查: match_score={match_score:.4f} >= 0.5，信任结果")
                return True
            return False

        # 本地结果：保持原逻辑 — 检查向量搜索是否有贡献
        # 检查向量搜索是否有贡献
        semantic_rank = top_result.get('semantic_rank')
        semantic_score = top_result.get('semantic_score', 0)

        # 阈值可配置，默认 0.40
        # 根据测试数据：有效语义匹配通常在 0.45~0.67，无效的（如特斯拉）为 0.0
        min_semantic_score = config_service.get('semantic_confidence_threshold', 0.40)

        # 向量搜索有贡献（有排名）且分数达到阈值
        if semantic_rank is not None and semantic_score >= min_semantic_score:
            logger.debug(f"向量搜索置信度检查: rank={semantic_rank}, score={semantic_score:.4f} >= {min_semantic_score}")
            return True

        return False

"""拼音服务 - 语音纠错和拼音搜索

功能：
1. 汉字转拼音
2. 基于拼音的相似度匹配
3. 查询纠错（解决语音识别错误，如"天景"→"天锦"）
4. 拼音索引管理
"""

import re
import logging
import threading
from typing import Dict, List, Set, Optional, Tuple
from dataclasses import dataclass
from collections import defaultdict

from sqlalchemy import select, func
from sqlalchemy.orm import Session

try:
    from pypinyin import pinyin, Style, lazy_pinyin
    PYPINYIN_AVAILABLE = True
except ImportError:
    PYPINYIN_AVAILABLE = False
    logging.warning("pypinyin 未安装，拼音功能将不可用。请运行: pip install pypinyin")

from app.legacy.models.database import Doc, EntityPinyin

logger = logging.getLogger(__name__)


@dataclass
class CorrectionResult:
    """纠错结果"""
    original: str           # 原始词
    corrected: str          # 纠正后的词
    similarity: float       # 相似度
    entity_type: str        # 实体类型
    is_auto: bool           # 是否自动纠错（True=自动，False=建议）


@dataclass
class QueryCorrectionResult:
    """查询纠错结果"""
    original_query: str                    # 原始查询
    corrected_query: str                   # 纠正后的查询
    corrections: List[CorrectionResult]    # 纠错详情
    has_correction: bool                   # 是否有纠错


class PinyinService:
    """拼音服务 - 转换、匹配、纠错"""

    # 全局缓存：避免每个请求重复从数据库加载 entity_pinyin
    _global_entity_cache: Dict[str, Tuple[str, str]] = {}
    _global_pinyin_cache: Dict[str, List[Tuple[str, str, int]]] = defaultdict(list)
    _global_abbr_cache: Dict[str, List[Tuple[str, str, str]]] = defaultdict(list)
    _global_cache_loaded: bool = False
    _global_lock = threading.Lock()

    # 相似度阈值
    # 只对完全同音词进行自动纠错，避免误纠错
    AUTO_CORRECT_THRESHOLD = 0.98      # 自动纠错阈值（基本完全匹配）
    SUGGEST_THRESHOLD = 0.90           # 建议阈值（高度相似）
    MIN_SIMILARITY = 0.85              # 最低相似度（低于此值不考虑）

    # 实体类型优先级（用于多个匹配时选择）
    ENTITY_PRIORITY = ['brand', 'series', 'ecu', 'supplier', 'emissions', 'doc_type', 'subsystem']

    # 常见中文词黑名单（不进行纠错的词）
    COMMON_WORDS_BLACKLIST = {
        '电路图', '电路', '发动机', '控制器', '系统', '模块',
        '故障', '诊断', '维修', '手册', '图纸', '线路',
        '接线', '原理', '说明', '资料', '文档',
        '国三', '国四', '国五', '国六',  # 排放标准
    }

    def __init__(self, db: Session):
        """
        初始化服务

        Args:
            db: 数据库会话
        """
        self.db = db
        # 绑定到全局缓存（多实例共享）
        self._entity_cache = PinyinService._global_entity_cache
        self._pinyin_cache = PinyinService._global_pinyin_cache
        self._abbr_cache = PinyinService._global_abbr_cache

    # ==================== 核心方法 ====================

    def to_pinyin(self, text: str, style: str = 'normal') -> str:
        """
        汉字转拼音

        Args:
            text: 输入文本
            style: 'normal'=全拼无声调, 'tone'=带声调, 'abbr'=首字母

        Returns:
            拼音字符串
        """
        if not PYPINYIN_AVAILABLE:
            return text

        if not text:
            return ""

        if style == 'abbr':
            # 首字母
            result = lazy_pinyin(text, style=Style.FIRST_LETTER)
            return ''.join(result).lower()
        elif style == 'tone':
            # 带声调
            result = pinyin(text, style=Style.TONE)
            return ''.join([item[0] for item in result])
        else:
            # 无声调全拼
            result = lazy_pinyin(text)
            return ''.join(result).lower()

    def correct_query(self, query: str) -> QueryCorrectionResult:
        """
        纠错查询（核心方法）

        保守策略：只对完全同音的实体词进行纠错，避免误纠错。

        Args:
            query: 用户查询 "国五天津东风电路图"

        Returns:
            QueryCorrectionResult
        """
        self._ensure_cache_loaded()

        if not PYPINYIN_AVAILABLE:
            return QueryCorrectionResult(
                original_query=query,
                corrected_query=query,
                corrections=[],
                has_correction=False
            )

        corrections = []
        corrected_query = query

        # 保守策略：只检测 2-4 字的窗口（实体名通常是这个长度）
        found_corrections = []

        for window_size in range(2, 5):
            i = 0
            while i <= len(query) - window_size:
                window = query[i:i + window_size]

                # 跳过非纯中文窗口
                if not self._is_all_chinese(window):
                    i += 1
                    continue

                # 跳过黑名单中的常见词
                if window in self.COMMON_WORDS_BLACKLIST:
                    i += 1
                    continue

                # 检查是否已存在于实体库中（正确的词不需要纠错）
                if window.lower() in self._entity_cache:
                    i += 1
                    continue

                # 检查是否已被其他纠错覆盖
                already_covered = False
                for fc in found_corrections:
                    if fc['start'] <= i < fc['end'] or fc['start'] < i + window_size <= fc['end']:
                        already_covered = True
                        break
                if already_covered:
                    i += 1
                    continue

                # 计算窗口的拼音
                window_pinyin = self.to_pinyin(window)

                # 1. 优先寻找完全同音的实体（拼音完全相同）
                exact_match = self._find_exact_pinyin_match(window_pinyin, window)

                if exact_match:
                    entity_value, entity_type = exact_match
                    found_corrections.append({
                        'start': i,
                        'end': i + window_size,
                        'original': window,
                        'corrected': entity_value,
                        'similarity': 1.0,  # 完全同音
                        'entity_type': entity_type,
                        'is_auto': True
                    })
                else:
                    # 2. 寻找高度相似的实体（拼音编辑距离<=1）
                    similar_match = self._find_similar_pinyin_match(window_pinyin, window)
                    if similar_match:
                        entity_value, entity_type, similarity = similar_match
                        found_corrections.append({
                            'start': i,
                            'end': i + window_size,
                            'original': window,
                            'corrected': entity_value,
                            'similarity': similarity,
                            'entity_type': entity_type,
                            'is_auto': True  # 编辑距离<=1的都自动纠错
                        })

                i += 1

        # 按相似度和位置排序
        found_corrections.sort(key=lambda x: (-x['similarity'], x['start']))

        # 去重：同一位置只保留最佳匹配
        used_positions = set()
        for fc in found_corrections:
            positions = set(range(fc['start'], fc['end']))
            if positions & used_positions:
                continue

            used_positions |= positions
            corrections.append(CorrectionResult(
                original=fc['original'],
                corrected=fc['corrected'],
                similarity=fc['similarity'],
                entity_type=fc['entity_type'],
                is_auto=fc['is_auto']
            ))

            # 自动替换
            if fc['is_auto']:
                corrected_query = corrected_query.replace(fc['original'], fc['corrected'], 1)

        return QueryCorrectionResult(
            original_query=query,
            corrected_query=corrected_query,
            corrections=corrections,
            has_correction=len(corrections) > 0
        )

    def _find_exact_pinyin_match(self, pinyin: str, original: str) -> Optional[Tuple[str, str]]:
        """
        查找完全同音的实体（拼音完全相同但文字不同）

        Args:
            pinyin: 拼音字符串
            original: 原始文字

        Returns:
            (实体值, 实体类型) 或 None
        """
        if pinyin not in self._pinyin_cache:
            return None

        candidates = self._pinyin_cache[pinyin]

        # 按实体优先级和频次排序
        best_match = None
        best_priority = len(self.ENTITY_PRIORITY)
        best_freq = -1

        for entity_value, entity_type, freq in candidates:
            # 跳过与原词相同的
            if entity_value == original:
                continue

            # 长度必须相同（避免"天"匹配到"天锦"）
            if len(entity_value) != len(original):
                continue

            # 计算优先级
            try:
                priority = self.ENTITY_PRIORITY.index(entity_type)
            except ValueError:
                priority = len(self.ENTITY_PRIORITY)

            # 选择优先级高或频次高的
            if priority < best_priority or (priority == best_priority and freq > best_freq):
                best_match = (entity_value, entity_type)
                best_priority = priority
                best_freq = freq

        return best_match

    def _is_all_chinese(self, text: str) -> bool:
        """判断文本是否全部为中文"""
        return all(self._is_chinese_char(char) for char in text)

    def _find_similar_pinyin_match(
        self,
        pinyin: str,
        original: str
    ) -> Optional[Tuple[str, str, float]]:
        """
        查找高度相似的实体（拼音编辑距离<=1）

        Args:
            pinyin: 拼音字符串
            original: 原始文字

        Returns:
            (实体值, 实体类型, 相似度) 或 None
        """
        best_match = None
        best_similarity = 0.0
        best_priority = len(self.ENTITY_PRIORITY)
        best_freq = -1

        # 遍历所有缓存的拼音
        for cached_pinyin, candidates in self._pinyin_cache.items():
            # 计算编辑距离
            distance = self._levenshtein_distance(pinyin, cached_pinyin)

            # 只考虑编辑距离<=1的（非常相似）
            if distance > 1:
                continue

            # 计算相似度
            max_len = max(len(pinyin), len(cached_pinyin))
            similarity = 1 - (distance / max_len)

            # 必须高于阈值（0.85 允许编辑距离=1的匹配）
            if similarity < 0.85:
                continue

            for entity_value, entity_type, freq in candidates:
                # 跳过与原词相同的
                if entity_value == original:
                    continue

                # 长度必须相同
                if len(entity_value) != len(original):
                    continue

                # 计算优先级
                try:
                    priority = self.ENTITY_PRIORITY.index(entity_type)
                except ValueError:
                    priority = len(self.ENTITY_PRIORITY)

                # 选择相似度高、优先级高、频次高的
                if (similarity > best_similarity or
                    (similarity == best_similarity and priority < best_priority) or
                    (similarity == best_similarity and priority == best_priority and freq > best_freq)):
                    best_match = (entity_value, entity_type, similarity)
                    best_similarity = similarity
                    best_priority = priority
                    best_freq = freq

        return best_match

    def find_similar(
        self,
        term: str,
        entity_type: Optional[str] = None,
        top_k: int = 5
    ) -> List[Tuple[str, float, str]]:
        """
        查找相似实体（基于拼音）

        Args:
            term: 输入词 "天景"
            entity_type: 限定实体类型 "series"（可选）
            top_k: 返回数量

        Returns:
            [(实体值, 相似度, 实体类型), ...]
        """
        self._ensure_cache_loaded()

        if not PYPINYIN_AVAILABLE or not term:
            return []

        term_pinyin = self.to_pinyin(term)
        term_abbr = self.to_pinyin(term, style='abbr')

        candidates = []

        # 1. 精确拼音匹配
        if term_pinyin in self._pinyin_cache:
            for entity_value, etype, freq in self._pinyin_cache[term_pinyin]:
                if entity_type and etype != entity_type:
                    continue
                candidates.append((entity_value, 1.0, etype, freq))

        # 2. 模糊拼音匹配
        for py, entities in self._pinyin_cache.items():
            if py == term_pinyin:
                continue

            similarity = self._calculate_similarity(term_pinyin, py)
            if similarity >= self.MIN_SIMILARITY:
                for entity_value, etype, freq in entities:
                    if entity_type and etype != entity_type:
                        continue
                    candidates.append((entity_value, similarity, etype, freq))

        # 3. 首字母匹配（作为补充）
        if term_abbr in self._abbr_cache:
            for entity_value, py, etype in self._abbr_cache[term_abbr]:
                if entity_type and etype != entity_type:
                    continue
                # 首字母匹配给一个基础分
                similarity = self._calculate_similarity(term_pinyin, py)
                if similarity >= self.MIN_SIMILARITY:
                    # 检查是否已存在
                    existing = [c for c in candidates if c[0] == entity_value]
                    if not existing:
                        candidates.append((entity_value, similarity, etype, 0))

        # 排序：相似度优先，频次其次
        candidates.sort(key=lambda x: (-x[1], -x[3]))

        # 去重并返回
        seen = set()
        results = []
        for entity_value, similarity, etype, _ in candidates:
            if entity_value not in seen:
                seen.add(entity_value)
                results.append((entity_value, similarity, etype))
                if len(results) >= top_k:
                    break

        return results

    # ==================== 索引管理 ====================

    def load_cache(self) -> None:
        """从数据库加载实体拼音到缓存"""
        if PinyinService._global_cache_loaded:
            return

        with PinyinService._global_lock:
            if PinyinService._global_cache_loaded:
                return

            logger.info("加载拼音缓存...")

            PinyinService._global_entity_cache.clear()
            PinyinService._global_pinyin_cache.clear()
            PinyinService._global_abbr_cache.clear()

            stmt = select(EntityPinyin).order_by(EntityPinyin.frequency.desc())
            result = self.db.execute(stmt)
            entities = result.scalars().all()

            for entity in entities:
                key = entity.entity_value.lower()
                PinyinService._global_entity_cache[key] = (entity.pinyin, entity.entity_type)
                PinyinService._global_pinyin_cache[entity.pinyin].append(
                    (entity.entity_value, entity.entity_type, entity.frequency)
                )
                PinyinService._global_abbr_cache[entity.pinyin_abbr].append(
                    (entity.entity_value, entity.pinyin, entity.entity_type)
                )

            PinyinService._global_cache_loaded = True
            logger.info(f"拼音缓存加载完成: {len(entities)} 个实体")

    def add_entity(
        self,
        entity_type: str,
        entity_value: str,
        frequency: int = 0
    ) -> bool:
        """
        添加实体到拼音索引

        Args:
            entity_type: 实体类型
            entity_value: 实体值
            frequency: 出现频次

        Returns:
            是否成功
        """
        if not PYPINYIN_AVAILABLE:
            logger.warning("pypinyin 未安装，无法添加实体")
            return False

        try:
            py = self.to_pinyin(entity_value)
            py_tone = self.to_pinyin(entity_value, style='tone')
            py_abbr = self.to_pinyin(entity_value, style='abbr')

            entity = EntityPinyin(
                entity_type=entity_type,
                entity_value=entity_value,
                pinyin=py,
                pinyin_tone=py_tone,
                pinyin_abbr=py_abbr,
                frequency=frequency
            )

            self.db.merge(entity)
            self.db.commit()

            # 更新全局缓存（若尚未加载则标记已加载并初始化结构）
            with PinyinService._global_lock:
                PinyinService._global_cache_loaded = True
                key = entity_value.lower()
                PinyinService._global_entity_cache[key] = (py, entity_type)
                PinyinService._global_pinyin_cache[py].append((entity_value, entity_type, frequency))
                PinyinService._global_abbr_cache[py_abbr].append((entity_value, py, entity_type))

            logger.debug(f"添加实体拼音: {entity_type}:{entity_value} -> {py}")
            return True

        except Exception as e:
            logger.error(f"添加实体拼音失败: {e}")
            self.db.rollback()
            return False

    def build_index_from_docs(self) -> Dict[str, int]:
        """
        从 docs 表提取所有实体构建拼音索引

        Returns:
            {'brand': 10, 'series': 20, ...} 各类型添加数量
        """
        if not PYPINYIN_AVAILABLE:
            logger.error("pypinyin 未安装，无法构建索引")
            return {}

        logger.info("开始从文档构建拼音索引...")

        # 统计各实体的出现频次
        entity_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

        # 字段映射
        field_mapping = {
            'brand': 'brand',
            'series': 'series',
            'ecu': 'ecus',
            'supplier': 'suppliers',
            'emissions': 'emissions',
            'subsystem': 'subsystems',
            'doc_type': 'doc_types',
        }

        # 查询所有文档
        docs = self.db.query(Doc).all()

        for doc in docs:
            # 单值字段
            if doc.brand:
                entity_stats['brand'][doc.brand] += 1
            if doc.series:
                entity_stats['series'][doc.series] += 1

            # 列表字段
            if doc.ecus:
                for ecu in doc.ecus:
                    entity_stats['ecu'][ecu] += 1
            if doc.suppliers:
                for supplier in doc.suppliers:
                    entity_stats['supplier'][supplier] += 1
            if doc.emissions:
                for emission in doc.emissions:
                    entity_stats['emissions'][emission] += 1
            if doc.subsystems:
                for subsystem in doc.subsystems:
                    entity_stats['subsystem'][subsystem] += 1
            if doc.doc_types:
                for doc_type in doc.doc_types:
                    entity_stats['doc_type'][doc_type] += 1

        # 批量插入
        counts = {}
        for entity_type, values in entity_stats.items():
            count = 0
            for entity_value, frequency in values.items():
                if entity_value and len(entity_value) >= 2:
                    if self.add_entity(entity_type, entity_value, frequency):
                        count += 1
            counts[entity_type] = count
            logger.info(f"  {entity_type}: 添加 {count} 个实体")

        total = sum(counts.values())
        logger.info(f"拼音索引构建完成: 共 {total} 个实体")

        # 重新加载缓存
        self.load_cache()

        return counts

    def get_stats(self) -> Dict:
        """获取拼音索引统计"""
        self._ensure_cache_loaded()

        # 按类型统计
        type_counts = defaultdict(int)
        for _, (_, entity_type) in PinyinService._global_entity_cache.items():
            type_counts[entity_type] += 1

        return {
            'total_entities': len(PinyinService._global_entity_cache),
            'unique_pinyins': len(PinyinService._global_pinyin_cache),
            'by_type': dict(type_counts),
            'pypinyin_available': PYPINYIN_AVAILABLE
        }

    # ==================== 私有方法 ====================

    def _ensure_cache_loaded(self) -> None:
        """确保缓存已加载"""
        if not PinyinService._global_cache_loaded:
            self.load_cache()

    def _segment_query(self, query: str) -> List[str]:
        """
        分割查询字符串

        策略：按中文字符块和非中文字符块分割
        "东风天景电路图" -> ["东风", "天景", "电路图"]
        "EDC17天景" -> ["EDC17", "天景"]
        """
        segments = []
        current = ""
        current_is_chinese = None

        for char in query:
            is_chinese = self._is_chinese_char(char)

            if current_is_chinese is None:
                current_is_chinese = is_chinese
                current = char
            elif is_chinese == current_is_chinese:
                current += char
            else:
                if current:
                    segments.append(current)
                current = char
                current_is_chinese = is_chinese

        if current:
            segments.append(current)

        return segments

    def _is_chinese(self, text: str) -> bool:
        """判断文本是否包含中文"""
        for char in text:
            if self._is_chinese_char(char):
                return True
        return False

    def _is_chinese_char(self, char: str) -> bool:
        """判断是否为中文字符"""
        return '\u4e00' <= char <= '\u9fff'

    def _find_best_match(self, term: str) -> Optional[Tuple[str, float, str]]:
        """
        查找最佳匹配

        Returns:
            (实体值, 相似度, 实体类型) 或 None
        """
        matches = self.find_similar(term, top_k=3)

        if not matches:
            return None

        # 返回相似度最高的
        best = matches[0]
        if best[1] >= self.MIN_SIMILARITY:
            return best

        return None

    def _calculate_similarity(self, pinyin1: str, pinyin2: str) -> float:
        """
        计算两个拼音的相似度

        策略：
        1. 完全匹配 → 1.0
        2. 编辑距离归一化
        """
        if pinyin1 == pinyin2:
            return 1.0

        if not pinyin1 or not pinyin2:
            return 0.0

        # 计算编辑距离
        distance = self._levenshtein_distance(pinyin1, pinyin2)
        max_len = max(len(pinyin1), len(pinyin2))

        # 归一化相似度
        similarity = 1 - (distance / max_len)

        return max(0, similarity)

    def _levenshtein_distance(self, s1: str, s2: str) -> int:
        """计算编辑距离（Levenshtein Distance）"""
        if len(s1) < len(s2):
            s1, s2 = s2, s1

        if len(s2) == 0:
            return len(s1)

        previous_row = range(len(s2) + 1)

        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row

        return previous_row[-1]


# ==================== 便捷函数 ====================

def correct_query(db: Session, query: str) -> QueryCorrectionResult:
    """
    纠错查询（便捷函数）
    """
    service = PinyinService(db)
    return service.correct_query(query)


def build_pinyin_index(db: Session) -> Dict[str, int]:
    """
    构建拼音索引（便捷函数）
    """
    service = PinyinService(db)
    return service.build_index_from_docs()


# ==================== 测试代码 ====================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    print("=" * 80)
    print("拼音服务测试")
    print("=" * 80)

    if not PYPINYIN_AVAILABLE:
        print("错误: pypinyin 未安装，请运行: pip install pypinyin")
        exit(1)

    from app.legacy.models.database import get_db

    db = next(get_db())

    try:
        service = PinyinService(db)

        # 测试拼音转换
        print("\n1. 拼音转换测试:")
        test_words = ["天锦", "东风", "博世", "尿素泵"]
        for word in test_words:
            py = service.to_pinyin(word)
            abbr = service.to_pinyin(word, style='abbr')
            print(f"  {word} -> 拼音: {py}, 首字母: {abbr}")

        # 构建索引
        print("\n2. 构建拼音索引:")
        counts = service.build_index_from_docs()
        print(f"  结果: {counts}")

        # 测试相似度匹配
        print("\n3. 相似度匹配测试:")
        test_terms = ["天景", "天金", "天精", "东分"]
        for term in test_terms:
            matches = service.find_similar(term, top_k=3)
            print(f"  '{term}' 的相似实体:")
            for entity, sim, etype in matches:
                print(f"    - {entity} ({etype}): 相似度 {sim:.2f}")

        # 测试查询纠错
        print("\n4. 查询纠错测试:")
        test_queries = [
            "东风天景电路图",
            "东分天金尿素泵",
            "博士控制器",
        ]
        for query in test_queries:
            result = service.correct_query(query)
            print(f"  原始: '{result.original_query}'")
            print(f"  纠正: '{result.corrected_query}'")
            if result.corrections:
                for c in result.corrections:
                    auto = "自动" if c.is_auto else "建议"
                    print(f"    [{auto}] {c.original} → {c.corrected} (相似度: {c.similarity:.2f})")
            print()

        # 统计信息
        print("5. 统计信息:")
        stats = service.get_stats()
        print(f"  {stats}")

    finally:
        db.close()

    print("\n" + "=" * 80)
    print("测试完成")
    print("=" * 80)

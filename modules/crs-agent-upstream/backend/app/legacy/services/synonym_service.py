"""同义词服务"""

import hashlib
import logging
import threading
from typing import List, Dict, Set, Optional
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.legacy.models.database import Synonym

logger = logging.getLogger(__name__)


class SynonymService:
    """同义词服务 - 管理和查询同义词"""

    # 全局缓存：避免每个请求重复从数据库加载
    _global_term_to_group_cache: Optional[Dict[str, str]] = None
    _global_group_to_terms_cache: Optional[Dict[str, Set[str]]] = None
    _global_lock = threading.Lock()

    def __init__(self, db: Session):
        """
        初始化服务

        Args:
            db: 数据库会话
        """
        self.db = db
        # 缓存：term -> group_id
        self._term_to_group_cache: Optional[Dict[str, str]] = None
        # 缓存：group_id -> [terms]
        self._group_to_terms_cache: Optional[Dict[str, Set[str]]] = None

    def load_cache(self):
        """加载同义词到缓存"""
        # 优先复用全局缓存
        if SynonymService._global_term_to_group_cache is not None:
            self._term_to_group_cache = SynonymService._global_term_to_group_cache
            self._group_to_terms_cache = SynonymService._global_group_to_terms_cache
            return

        with SynonymService._global_lock:
            # double-check
            if SynonymService._global_term_to_group_cache is not None:
                self._term_to_group_cache = SynonymService._global_term_to_group_cache
                self._group_to_terms_cache = SynonymService._global_group_to_terms_cache
                return

            logger.info("加载同义词缓存...")

            term_to_group: Dict[str, str] = {}
            group_to_terms: Dict[str, Set[str]] = {}

            # 从数据库加载所有同义词
            stmt = select(Synonym)
            result = self.db.execute(stmt)
            synonyms = result.scalars().all()

            for syn in synonyms:
                # term -> group_id 映射
                term_to_group[syn.term.lower()] = syn.group_id

                # group_id -> terms 映射
                if syn.group_id not in group_to_terms:
                    group_to_terms[syn.group_id] = set()
                group_to_terms[syn.group_id].add(syn.term)

            SynonymService._global_term_to_group_cache = term_to_group
            SynonymService._global_group_to_terms_cache = group_to_terms

            self._term_to_group_cache = SynonymService._global_term_to_group_cache
            self._group_to_terms_cache = SynonymService._global_group_to_terms_cache

            logger.info(f"同义词缓存加载完成: {len(synonyms)} 条记录, {len(group_to_terms)} 个同义词组")

    def reload_cache(self):
        """重新加载缓存"""
        with SynonymService._global_lock:
            SynonymService._global_term_to_group_cache = None
            SynonymService._global_group_to_terms_cache = None
        self._term_to_group_cache = None
        self._group_to_terms_cache = None
        self.load_cache()

    def expand_term(self, term: str) -> Set[str]:
        """
        扩展单个词条，返回它的所有同义词（包括自己）

        Args:
            term: 词条

        Returns:
            同义词集合
        """
        self.load_cache()

        term_lower = term.lower()

        # 查找该词所属的同义词组
        group_id = self._term_to_group_cache.get(term_lower)

        if group_id:
            # 返回该组的所有词条
            return self._group_to_terms_cache[group_id].copy()
        else:
            # 没有同义词，只返回自己
            return {term}

    def expand_terms(self, terms: List[str]) -> Set[str]:
        """
        批量扩展多个词条

        Args:
            terms: 词条列表

        Returns:
            所有同义词的并集
        """
        result = set()
        for term in terms:
            result.update(self.expand_term(term))
        return result

    def expand_query(self, query: str) -> str:
        """
        扩展查询字符串（简单实现：按空格分词，扩展每个词）

        Args:
            query: 查询字符串

        Returns:
            扩展后的查询字符串
        """
        words = query.split()
        expanded = self.expand_terms(words)
        return ' '.join(expanded)

    def add_synonym_group(
        self,
        terms: List[str],
        category: str,
        primary_term: Optional[str] = None
    ) -> str:
        """
        添加一组同义词

        Args:
            terms: 同义词列表
            category: 分类（brand/series/subsystem/supplier/emissions/doc_type）
            primary_term: 主要词条（用于显示），默认为第一个

        Returns:
            group_id
        """
        if not terms:
            raise ValueError("同义词列表不能为空")

        # 生成 group_id
        group_id = self._generate_group_id(terms, category)

        # 确定主要词条
        if primary_term is None:
            primary_term = terms[0]
        elif primary_term not in terms:
            raise ValueError(f"主要词条 '{primary_term}' 不在同义词列表中")

        # 插入数据库
        for term in terms:
            synonym = Synonym(
                group_id=group_id,
                term=term,
                category=category,
                is_primary=(term == primary_term)
            )
            self.db.merge(synonym)  # 使用 merge 避免重复插入

        self.db.commit()
        logger.info(f"添加同义词组: {group_id}, 类别={category}, 词条={terms}")

        # 重新加载缓存
        self.reload_cache()

        return group_id

    def remove_synonym_group(self, group_id: str) -> bool:
        """
        删除一组同义词

        Args:
            group_id: 同义词组ID

        Returns:
            是否成功
        """
        try:
            # 删除该组的所有记录
            stmt = select(Synonym).where(Synonym.group_id == group_id)
            result = self.db.execute(stmt)
            synonyms = result.scalars().all()

            for syn in synonyms:
                self.db.delete(syn)

            self.db.commit()
            logger.info(f"删除同义词组: {group_id}, 删除了 {len(synonyms)} 条记录")

            # 重新加载缓存
            self.reload_cache()

            return True

        except Exception as e:
            logger.error(f"删除同义词组失败: {group_id}, 错误: {e}")
            self.db.rollback()
            return False

    def get_all_groups(self) -> List[Dict]:
        """
        获取所有同义词组

        Returns:
            同义词组列表
        """
        self.load_cache()

        groups = []
        for group_id, terms in self._group_to_terms_cache.items():
            # 查询一条记录获取 category
            stmt = select(Synonym).where(Synonym.group_id == group_id).limit(1)
            result = self.db.execute(stmt)
            syn = result.scalar()

            if syn:
                groups.append({
                    'group_id': group_id,
                    'category': syn.category,
                    'terms': list(terms),
                    'count': len(terms)
                })

        return groups

    def get_groups_by_category(self, category: str) -> List[Dict]:
        """
        按分类获取同义词组

        Args:
            category: 分类

        Returns:
            同义词组列表
        """
        stmt = select(Synonym).where(Synonym.category == category)
        result = self.db.execute(stmt)
        synonyms = result.scalars().all()

        # 按 group_id 聚合
        groups_dict = {}
        for syn in synonyms:
            if syn.group_id not in groups_dict:
                groups_dict[syn.group_id] = {
                    'group_id': syn.group_id,
                    'category': syn.category,
                    'terms': []
                }
            groups_dict[syn.group_id]['terms'].append(syn.term)

        groups = list(groups_dict.values())
        for group in groups:
            group['count'] = len(group['terms'])

        return groups

    def _generate_group_id(self, terms: List[str], category: str) -> str:
        """
        生成同义词组ID

        Args:
            terms: 词条列表
            category: 分类

        Returns:
            group_id
        """
        # 排序后生成hash，确保相同的词条组产生相同的ID
        sorted_terms = sorted(terms)
        content = f"{category}:{','.join(sorted_terms)}"
        hash_obj = hashlib.md5(content.encode('utf-8'))
        return f"{category}_{hash_obj.hexdigest()[:16]}"

    def get_stats(self) -> Dict:
        """
        获取同义词统计信息

        Returns:
            统计信息
        """
        self.load_cache()

        # 按分类统计
        category_stats = {}
        stmt = select(Synonym)
        result = self.db.execute(stmt)
        synonyms = result.scalars().all()

        for syn in synonyms:
            if syn.category not in category_stats:
                category_stats[syn.category] = {
                    'groups': set(),
                    'terms': 0
                }
            category_stats[syn.category]['groups'].add(syn.group_id)
            category_stats[syn.category]['terms'] += 1

        # 转换为可序列化的格式
        for category in category_stats:
            category_stats[category]['groups'] = len(category_stats[category]['groups'])

        return {
            'total_terms': len(synonyms),
            'total_groups': len(self._group_to_terms_cache),
            'by_category': category_stats
        }

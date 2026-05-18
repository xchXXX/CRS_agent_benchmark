"""检索引擎 - 当前启用 MySQL FULLTEXT 词法检索"""

import logging
import time
from collections import defaultdict
from typing import Dict, List

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.legacy.models.database import Doc, ExternalFileUrl, PhysicalFile
from app.legacy.services.query_preprocessor import QueryPreprocessor, QueryResult

logger = logging.getLogger(__name__)


class SearchEngine:
    """混合检索引擎"""

    # 通用文档词：不应参与 entity 覆盖判定，否则会把“李自广电路图”这类未知词整体覆盖掉
    _GENERIC_DOC_TOKENS = {
        '资料', '文档', '图', '图纸',
        '电路图', '线路图', '原理图', '整车图', '整车电路图', '线束图',
        '手册', '维修手册', '操作手册', '使用手册', '培训资料', '技术资料',
    }

    def __init__(self, db_session: Session):
        """
        初始化检索引擎

        Args:
            db_session: SQLAlchemy 数据库会话
        """
        self.db = db_session
        self.query_preprocessor = QueryPreprocessor(db_session)
        self.vector_enabled = False

    def search(
        self,
        query: str,
        top_k: int = 20,
        lexical_top_k: int = 200,
        semantic_top_k: int = 200,
        use_vector: bool = False,
        rrf_k: int = 60,
        use_preprocessing: bool = True
    ) -> Dict:
        """
        混合检索

        Args:
            query: 用户查询
            top_k: 最终返回结果数
            lexical_top_k: 词法检索返回数
            semantic_top_k: 语义检索返回数
            use_vector: 是否使用向量检索
            rrf_k: RRF算法的K参数
            use_preprocessing: 是否使用查询预处理

        Returns:
            检索结果：
            {
                'results': [
                    {
                        'file_id': str,
                        'filename': str,
                        'physical_path': str,
                        'score': float,
                        'lexical_rank': int,
                        'semantic_rank': int,
                        'brand': str,
                        'series': str,
                        'model': str,
                        'ecus': List[str],
                        ...
                    },
                    ...
                ],
                'query': str,
                'total_results': int,
                'search_time_ms': float,
                'search_method': str,  # 'hybrid', 'lexical_only', 'semantic_only'
                'preprocessing': {  # 预处理信息（如果启用）
                    'original_query': str,
                    'normalized_query': str,
                    'expanded_query': str,
                    'entities': Dict,
                    'synonym_expansions': Dict
                }
            }
        """
        start_time = time.time()

        logger.info(f"检索查询: '{query}', top_k={top_k}")

        # 0. 查询预处理
        preprocessing_result = None
        lexical_query = query
        semantic_query = query

        if use_preprocessing:
            try:
                preprocessing_result = self.query_preprocessor.process(query)
                # 词法检索使用扩展后的FULLTEXT查询（包含维度扩展+同义词，提高召回率）
                lexical_query = preprocessing_result.expanded_fulltext_query or preprocessing_result.expanded_query
                # 语义检索使用扩展后的FULLTEXT查询（包含更多语义信息，embedding质量更好）
                semantic_query = preprocessing_result.expanded_fulltext_query or preprocessing_result.corrected_query
                logger.info(f"预处理完成: 原始='{query}' → 纠错='{preprocessing_result.corrected_query}' → 词法='{lexical_query}' / 语义='{semantic_query}'")
            except Exception as e:
                logger.warning(f"查询预处理失败，使用原始查询: {e}")

        # 1. 词法检索（MySQL FULLTEXT）
        lexical_results = self._lexical_search(lexical_query, top_k=lexical_top_k)
        logger.info(f"词法检索返回 {len(lexical_results)} 个结果")

        # 2. 语义检索（Qdrant向量）
        semantic_results = []
        if use_vector and self.vector_enabled:
            semantic_results = self._semantic_search(semantic_query, top_k=semantic_top_k)
            logger.info(f"语义检索返回 {len(semantic_results)} 个结果")
        else:
            logger.info("跳过语义检索")

        # 3. 结果融合
        if lexical_results and semantic_results:
            # 混合检索：使用RRF融合
            search_method = 'hybrid'
            merged_results = self._rrf_fusion(
                lexical_results,
                semantic_results,
                k=rrf_k
            )
        elif lexical_results:
            # 仅词法检索
            search_method = 'lexical_only'
            merged_results = lexical_results
        elif semantic_results:
            # 仅语义检索
            search_method = 'semantic_only'
            merged_results = semantic_results
        else:
            # 无结果
            search_method = 'none'
            merged_results = []

        # 4. 取候选集（扩大范围用于重排）
        rerank_pool_size = top_k * 5 if (preprocessing_result and preprocessing_result.query_tokens) else top_k
        final_results = merged_results[:rerank_pool_size]

        # 5. 丰富结果信息（从数据库读取完整信息）
        enriched_results = self._enrich_results(final_results)

        # 6. Token 重合度重排（在 enrich 之后，因为需要 filename）
        if preprocessing_result and preprocessing_result.query_tokens:
            enriched_results = self._rerank_by_overlap(
                enriched_results,
                preprocessing_result
            )

        # 7. 取 Top K（注意：如果下游有 entity filter，filter 可能会显著缩减结果数；
        #    此处使用 rerank_pool_size 作为上限而非 top_k，避免过早截断目标文件）
        enriched_results = enriched_results[:rerank_pool_size]

        # 计算耗时
        search_time_ms = (time.time() - start_time) * 1000

        logger.info(
            f"检索完成: 方法={search_method}, "
            f"结果数={len(enriched_results)}, "
            f"耗时={search_time_ms:.2f}ms"
        )

        # 构建返回结果
        result = {
            'results': enriched_results,
            'query': query,
            'total_results': len(merged_results),
            'search_time_ms': search_time_ms,
            'search_method': search_method
        }

        # 添加预处理信息
        if preprocessing_result:
            result['preprocessing'] = {
                'original_query': preprocessing_result.original_query,
                'normalized_query': preprocessing_result.normalized_query,
                'corrected_query': preprocessing_result.corrected_query,
                'expanded_query': preprocessing_result.expanded_query,
                'entities': preprocessing_result.entities,
                'synonym_expansions': preprocessing_result.synonym_expansions,
                'pinyin_corrections': preprocessing_result.pinyin_corrections,
                'has_correction': preprocessing_result.has_correction,
                'query_tokens': preprocessing_result.query_tokens,
                'token_expansions': preprocessing_result.token_expansions,
                'expanded_fulltext_query': preprocessing_result.expanded_fulltext_query
            }

        return result

    def _lexical_search(self, query: str, top_k: int = 200) -> List[Dict]:
        """
        MySQL FULLTEXT 词法检索

        Args:
            query: 查询文本
            top_k: 返回结果数

        Returns:
            [(file_id, score, rank), ...]
        """
        try:
            # 构建FULLTEXT查询
            # 使用 NATURAL LANGUAGE MODE 以支持部分匹配
            # ngram解析器会自动分词
            sql = text("""
                SELECT
                    file_id,
                    MATCH(searchable_text) AGAINST(:query IN NATURAL LANGUAGE MODE) AS score
                FROM docs
                WHERE MATCH(searchable_text) AGAINST(:query IN NATURAL LANGUAGE MODE)
                ORDER BY score DESC
                LIMIT :top_k
            """)

            result = self.db.execute(sql, {'query': query, 'top_k': top_k})
            rows = result.fetchall()

            results = []
            for rank, (file_id, score) in enumerate(rows, start=1):
                results.append({
                    'file_id': file_id,
                    'score': float(score),
                    'rank': rank
                })

            return results

        except Exception as e:
            logger.error(f"词法检索失败: {e}", exc_info=True)
            return []

    def _semantic_search(self, query: str, top_k: int = 200) -> List[Dict]:
        """
        语义检索已在当前迁移阶段禁用。

        Args:
            query: 查询文本
            top_k: 返回结果数

        Returns:
            空列表
        """
        return []

    def _rrf_fusion(
        self,
        lexical_results: List[Dict],
        semantic_results: List[Dict],
        k: int = 60
    ) -> List[Dict]:
        """
        Reciprocal Rank Fusion (RRF) 融合算法

        公式: RRF_score(d) = Σ 1/(k + rank(d))

        Args:
            lexical_results: 词法检索结果
            semantic_results: 语义检索结果
            k: RRF参数（默认60）

        Returns:
            融合后的结果列表
        """
        rrf_scores = defaultdict(lambda: {
            'rrf_score': 0.0,
            'lexical_rank': None,
            'semantic_rank': None,
            'lexical_score': 0.0,
            'semantic_score': 0.0
        })

        # 词法检索贡献
        for item in lexical_results:
            file_id = item['file_id']
            rank = item['rank']
            rrf_scores[file_id]['rrf_score'] += 1.0 / (k + rank)
            rrf_scores[file_id]['lexical_rank'] = rank
            rrf_scores[file_id]['lexical_score'] = item['score']

        # 语义检索贡献
        for item in semantic_results:
            file_id = item['file_id']
            rank = item['rank']
            rrf_scores[file_id]['rrf_score'] += 1.0 / (k + rank)
            rrf_scores[file_id]['semantic_rank'] = rank
            rrf_scores[file_id]['semantic_score'] = item['score']

        # 排序
        sorted_results = sorted(
            rrf_scores.items(),
            key=lambda x: x[1]['rrf_score'],
            reverse=True
        )

        # 格式化输出
        merged_results = []
        for file_id, scores in sorted_results:
            merged_results.append({
                'file_id': file_id,
                'score': scores['rrf_score'],
                'lexical_rank': scores['lexical_rank'],
                'semantic_rank': scores['semantic_rank'],
                'lexical_score': scores['lexical_score'],
                'semantic_score': scores['semantic_score']
            })

        return merged_results

    def _rerank_by_overlap(self, results: List[Dict], preprocessing: QueryResult) -> List[Dict]:
        """Token 重合度重排

        对每个结果计算查询 token 在 filename/doc_types/hierarchy_full 中的命中率，
        结合原始 RRF 分数生成综合排序分数。

        算法：
        - 跳过已被 entities 完整覆盖的 token（token 是 entity 的子串或完全相等）
        - entity 是 token 的真子串时不标记覆盖（保留 token 中未覆盖的关键信息）
        - 对每个未覆盖的 token，检查它或其扩展形式是否在 filename/doc_types/hierarchy_full 中出现
        - fn_overlap = 命中token数 / 总token数
        - combined_score = overlap * 0.7 + normalized_rrf * 0.3
        - 全部 token 都被 entities 覆盖 → 不做重排，保留 RRF 原始排序

        Args:
            results: enrich 后的结果列表（包含 filename）
            preprocessing: 查询预处理结果

        Returns:
            重排后的结果列表
        """
        if not results:
            return results

        query_tokens = preprocessing.query_tokens
        token_expansions = preprocessing.token_expansions

        if not query_tokens:
            return results

        # --- 实体覆盖过滤：跳过已被 entities 覆盖的 token ---
        # 收集所有 entity 值（扁平化）
        entity_values: List[str] = []
        if preprocessing.entities:
            for facet_key, values in preprocessing.entities.items():
                if values:
                    for value in values:
                        normalized = self._normalize_text_for_overlap(value)
                        # 通用文档词不参与覆盖，避免覆盖掉复合未知词里的关键信息
                        if facet_key == 'doc_type' and normalized in self._GENERIC_DOC_TOKENS:
                            continue
                        entity_values.append(value)

        # 判断 token 是否被某个 entity 值覆盖
        # 覆盖条件：token 是 entity 值的子串或完全相等（即 entity 完整包含了 token）
        # 注意：entity 是 token 的子串时（如 entity="三一", token="三一挖掘机"），
        #       token 中包含未被 entity 覆盖的关键信息（如"挖掘机"），不应标记为覆盖
        overlap_tokens = []
        for token in query_tokens:
            token_lower = token.lower()
            covered = False
            for ev in entity_values:
                ev_lower = ev.lower()
                if token_lower in ev_lower:
                    covered = True
                    break
            if not covered:
                overlap_tokens.append(token)

        logger.debug(
            f"实体覆盖过滤: 原始tokens={query_tokens}, "
            f"entity_values={entity_values}, "
            f"overlap_tokens={overlap_tokens}"
        )

        # 全部 token 都被 entities 覆盖 → 跳过重排，保留 RRF 原始排序
        # entity filter（在下游的 doc_search_handler 或 search API 中）会保证精确性
        # 如果此处做重排，会把 entity token 不完全匹配的文件排到 top_k 之外，
        # 导致下游 entity filter 无法触及这些文件
        if not overlap_tokens:
            logger.info("所有 token 均被 entities 覆盖，跳过 overlap 重排，保留 RRF 原始排序")
            return results

        for result in results:
            filename = result.get('filename', '')
            filename_lower = filename.lower()

            # 构建可检索文本：filename + doc_types + hierarchy_full
            searchable_parts = [filename_lower]

            doc_types = result.get('doc_types') or []
            if isinstance(doc_types, list):
                searchable_parts.extend(
                    dt.lower() for dt in doc_types if isinstance(dt, str)
                )

            hierarchy = result.get('hierarchy_full') or ''
            if hierarchy:
                searchable_parts.append(hierarchy.lower())

            searchable_text = ' '.join(searchable_parts)

            # 计算命中率（仅用未被 entity 覆盖的 token）
            matched = 0
            for token in overlap_tokens:
                # 原始 token 或任意扩展形式命中即算 1 分
                variants = token_expansions.get(token, [token])
                if any(v.lower() in searchable_text for v in variants):
                    matched += 1

            fn_overlap = matched / len(overlap_tokens)

            # 保存中间分数
            result['overlap_score'] = fn_overlap
            result['original_rrf_score'] = result['score']

        # 归一化 RRF 分数到 0-1
        max_rrf = max((r['original_rrf_score'] for r in results), default=1)
        if max_rrf > 0:
            for r in results:
                normalized_rrf = r['original_rrf_score'] / max_rrf
                # 综合分数：重合度为主(0.7)，RRF为辅(0.3)，打破重合度相同时的平局
                r['score'] = r['overlap_score'] * 0.7 + normalized_rrf * 0.3

        results.sort(key=lambda x: x['score'], reverse=True)

        # 不做硬过滤：overlap_score=0 的结果靠低分自然沉底即可，
        # 精确性由下游 hard_constraint_validator / entity_filter 保证。
        # 硬删除会导致复合词（如"上装控制器电路图"）因 DimensionService 扩展
        # 偏移而误杀正确的平台文档。

        # 调试日志：输出重排后 top5
        for i, r in enumerate(results[:5]):
            logger.debug(
                f"重排#{i+1}: {r.get('filename', '')[:40]} | "
                f"overlap={r.get('overlap_score', 0):.2f} | "
                f"rrf={r.get('original_rrf_score', 0):.4f} | "
                f"combined={r['score']:.4f}"
            )

        return results

    def _normalize_text_for_overlap(self, value: object) -> str:
        """归一化文本用于 overlap 覆盖判定。"""
        if value is None:
            return ""
        text = str(value).strip().lower()
        if not text:
            return ""
        return ''.join(ch for ch in text if ch.isalnum() or '\u4e00' <= ch <= '\u9fff')

    def _enrich_results(self, results: List[Dict]) -> List[Dict]:
        """
        丰富结果信息（从数据库读取完整文档信息）

        Args:
            results: 基础结果列表

        Returns:
            丰富后的结果列表
        """
        if not results:
            return []

        # 提取file_id列表
        file_ids = [r['file_id'] for r in results]

        # 批量查询数据库
        docs = self.db.query(Doc, PhysicalFile).join(
            PhysicalFile, Doc.file_id == PhysicalFile.file_id
        ).filter(
            Doc.file_id.in_(file_ids)
        ).all()

        # 构建file_id -> doc映射
        doc_map = {doc.file_id: (doc, file) for doc, file in docs}

        # 丰富结果
        enriched_results = []
        for result in results:
            file_id = result['file_id']
            if file_id not in doc_map:
                continue

            doc, physical_file = doc_map[file_id]

            enriched_result = {
                # 基础信息
                'file_id': file_id,
                'filename': physical_file.filename,
                'physical_path': physical_file.physical_path,
                'file_type': physical_file.file_type,
                'file_size': physical_file.file_size,

                # 外部系统关联字段
                'parent_id': physical_file.parent_id,
                'ref_file_id': physical_file.ref_file_id,

                # 层级信息
                'hierarchy_level_1': physical_file.hierarchy_level_1,
                'hierarchy_level_2': physical_file.hierarchy_level_2,
                'hierarchy_level_3': physical_file.hierarchy_level_3,
                'hierarchy_level_4': physical_file.hierarchy_level_4,
                'hierarchy_full': physical_file.hierarchy_full,

                # 实体信息
                'brand': doc.brand,
                'series': doc.series,
                'model': doc.model,
                'model_variants': doc.model_variants,
                'platform_codes': doc.platform_codes,
                'subsystems': doc.subsystems,
                'ecus': doc.ecus,
                'suppliers': doc.suppliers,
                'emissions': doc.emissions,
                'drive_types': doc.drive_types,
                'batches': doc.batches,
                'doc_types': doc.doc_types,
                'eng_codes': doc.eng_codes,

                # 检索分数
                'score': result['score'],
                'lexical_rank': result.get('lexical_rank'),
                'semantic_rank': result.get('semantic_rank'),
                'lexical_score': result.get('lexical_score', 0.0),
                'semantic_score': result.get('semantic_score', 0.0),
            }

            enriched_results.append(enriched_result)

        # 批量查询外部文件URL（pic_folder_url）
        ref_file_ids = [r['ref_file_id'] for r in enriched_results if r.get('ref_file_id')]
        if ref_file_ids:
            external_urls = self.db.query(ExternalFileUrl).filter(
                ExternalFileUrl.id.in_(ref_file_ids)
            ).all()
            url_map = {url.id: url.pic_folder_url for url in external_urls}

            # 添加 pic_folder_url 到结果
            for result in enriched_results:
                ref_id = result.get('ref_file_id')
                if ref_id and ref_id in url_map:
                    result['pic_folder_url'] = url_map[ref_id]
                else:
                    result['pic_folder_url'] = None

        # 去重：按文件名去重，保留分数最高的
        seen = {}  # key: filename, value: result
        for result in enriched_results:
            key = result['filename']
            if key not in seen or result['score'] > seen[key]['score']:
                seen[key] = result

        deduped_results = list(seen.values())
        # 按分数重新排序
        deduped_results.sort(key=lambda x: x['score'], reverse=True)

        if len(deduped_results) < len(enriched_results):
            logger.info(f"去重: {len(enriched_results)} -> {len(deduped_results)} 个结果")

        return deduped_results


# ==================== 便捷函数 ====================

def search_documents(
    query: str,
    db_session: Session,
    top_k: int = 20,
    use_vector: bool = False
) -> Dict:
    """
    搜索文档（便捷函数）

    Args:
        query: 查询文本
        db_session: 数据库会话
        top_k: 返回结果数
        use_vector: 是否使用向量检索

    Returns:
        检索结果
    """
    engine = SearchEngine(db_session)
    return engine.search(query, top_k=top_k, use_vector=use_vector)


# ==================== 测试代码 ====================

if __name__ == "__main__":
    import logging
    from app.legacy.models.database import get_db

    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    print("=" * 80)
    print("混合检索引擎测试")
    print("=" * 80)

    # 获取数据库会话
    db = next(get_db())

    try:
        # 创建检索引擎
        engine = SearchEngine(db)

        # 测试查询列表
        test_queries = [
            "东风天锦康明斯发动机电路图",
            "解放J6重卡线路",
            "重汽豪沃国六后处理系统",
            "博世EDC17控制器诊断",
            "UL2尿素泵"
        ]

        for i, query in enumerate(test_queries, 1):
            print(f"\n{'=' * 80}")
            print(f"测试 {i}/{len(test_queries)}: '{query}'")
            print("=" * 80)

            # 执行检索
            result = engine.search(query, top_k=5)

            # 显示预处理信息
            if 'preprocessing' in result:
                prep = result['preprocessing']
                print(f"\n[预处理]")
                print(f"  规范化: '{prep['normalized_query']}'")
                print(f"  扩展后: '{prep['expanded_query']}'")
                if prep['entities']:
                    non_empty = {k: v for k, v in prep['entities'].items() if v}
                    if non_empty:
                        print(f"  识别实体: {non_empty}")
                if prep['synonym_expansions']:
                    print(f"  同义词扩展: {prep['synonym_expansions']}")

            print(f"\n检索方法: {result['search_method']}")
            print(f"耗时: {result['search_time_ms']:.2f} ms")
            print(f"总结果数: {result['total_results']}")
            print(f"\nTop 5 结果:")

            for idx, doc in enumerate(result['results'], 1):
                print(f"\n{idx}. {doc['filename']}")
                print(f"   路径: {doc['hierarchy_full']}")
                print(f"   品牌: {doc['brand']}, 系列: {doc['series']}, 型号: {doc['model']}")
                if doc['ecus']:
                    print(f"   ECU: {', '.join(doc['ecus'][:3])}")
                print(f"   RRF分数: {doc['score']:.4f}")
                if doc['lexical_rank']:
                    print(f"   词法排名: #{doc['lexical_rank']} (分数: {doc['lexical_score']:.2f})")
                if doc['semantic_rank']:
                    print(f"   语义排名: #{doc['semantic_rank']} (分数: {doc['semantic_score']:.4f})")

    finally:
        db.close()

    print("\n" + "=" * 80)
    print("测试完成")
    print("=" * 80)

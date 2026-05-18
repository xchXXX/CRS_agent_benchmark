"""实体提取服务 - 从文件名和路径提取结构化信息"""

import re
from typing import Dict, List, Set, Optional
import logging

from app.legacy.config.regex_patterns import get_compiled_patterns

logger = logging.getLogger(__name__)


class EntityExtractor:
    """实体提取器"""

    _DOC_TYPE_NORMALIZATION_REPLACEMENTS = [
        ('起动', '启动'),
        ('起動', '启动'),
    ]

    # ==================== 排放标准简写展开配置 ====================
    # 用于处理 "国四_五"、"国四/五" 等简写格式
    # 中文数字映射
    _EMISSION_NUM_MAP = {
        '二': '二', '三': '三', '四': '四', '五': '五', '六': '六',
    }

    # 简写格式正则：匹配 "国X分隔符Y" 格式（分隔符：_/、-）
    # 注意：只匹配中文数字，避免误匹配年份（如 国四_2015）
    _EMISSION_SHORTHAND_PATTERN = re.compile(
        r'国([二三四五六])([_/、\-])([二三四五六])'
    )

    # 三连简写格式正则：匹配 "国X分隔符Y分隔符Z" 格式
    _EMISSION_TRIPLE_PATTERN = re.compile(
        r'国([二三四五六])([_/、\-])([二三四五六])\2([二三四五六])'
    )

    # ==================== doc_type 提取配置 ====================
    # 粗类：用于稳定召回和聚合过滤（保存在 doc_types[0]）
    DOC_TYPE_COARSE_VALUES = {
        '电路图',
        '针脚定义',
        '保险盒定义',
        '维修手册',
        '诊断手册',
        '技术通报',
        '培训资料',
        '数据流',
        '零件目录',
        '用户手册',
    }

    # 细类：用于提升精确度（与粗类同列维护，保存在 doc_types[1:]）
    DOC_TYPE_FINE_TO_COARSE = {
        '整车电路图': '电路图',
        'ECU电路图': '电路图',
        '启动原理图': '电路图',
        'CAN总线图': '电路图',
    }

    # hierarchy level 值 → 细粒度 doc_type 子类型映射
    # 当粗粒度大类命中后，继续检查 level_1/level_2 是否匹配更精确的子类型
    HIERARCHY_FINE_DOC_TYPE = {
        'ECU电路图':     'ECU电路图',
        '整车电路图':     '整车电路图',
        '汽车电器盒':     '保险盒定义',
        '启动原理图':     '启动原理图',
        '起动原理图':     '启动原理图',    # 归一化：起动→启动
        'CAN总线图':      'CAN总线图',
        '整车CAN总线':    'CAN总线图',     # level_2 变体
    }

    # hierarchy_level 值 → doc_type 大类映射
    # 基于数据分析：21个唯一的 hierarchy_level_1 值
    HIERARCHY_TO_DOC_TYPE = {
        # === 一级目录明确是 doc_type（覆盖 77% 文件） ===
        '电路图':           '电路图',
        '整车电路图':       '电路图',
        'CAN总线图':        '电路图',
        '起动原理图':       '电路图',
        '数据流':           '数据流',
        '整车维保':         '维修手册',
        '诊断指导快捷方式': '诊断手册',
        '汽车电器盒':       '保险盒定义',
        '刷写指导':         '技术通报',
        '正时配气':         '维修手册',
        '工具使用':         '培训资料',
        '电路基础':         '培训资料',

        # === 二级目录出现的 doc_type（覆盖复合分区） ===
        '标准数据流':       '数据流',
        '维保资料':         '维修手册',
        '培训讲义':         '培训资料',
        '诊断手册':         '诊断手册',
        '整车CAN总线':      '电路图',
    }

    # 文件名关键词 → doc_type 推断（兜底策略）
    # 按优先级排序，长关键词在前
    FILENAME_DOC_TYPE_KEYWORDS = [
        # 电路图类
        ('整车电路图', '电路图'),
        ('电原理图', '电路图'),
        ('电路图', '电路图'),
        ('原理图', '电路图'),
        ('线束图', '电路图'),
        ('CAN总线', '电路图'),

        # 针脚定义类
        ('针脚定义', '针脚定义'),
        ('引脚定义', '针脚定义'),
        ('PIN定义', '针脚定义'),
        ('接插件定义', '针脚定义'),
        ('针脚', '针脚定义'),

        # 保险盒类
        ('保险盒定义', '保险盒定义'),
        ('保险盒', '保险盒定义'),
        ('熔断丝', '保险盒定义'),
        ('电器盒', '保险盒定义'),

        # 数据流类
        ('数据流', '数据流'),

        # 手册类
        ('维修手册', '维修手册'),
        ('诊断手册', '诊断手册'),
        ('故障诊断', '诊断手册'),
        ('诊断指导', '诊断手册'),
        ('拆装流程', '维修手册'),
        ('拆解图', '维修手册'),

        # 培训类
        ('培训', '培训资料'),
        ('讲义', '培训资料'),
        ('介绍', '培训资料'),
    ]

    # 需要在实体提取前剥离的文件扩展名（不区分大小写）
    _FILE_EXTENSIONS = re.compile(
        r'\.(docx?|xlsx?|pptx?|pdf|txt|csv|jpg|jpeg|png|gif|bmp|tiff?|zip|rar|7z)(?=\s|$)',
        re.IGNORECASE
    )

    def __init__(self):
        """初始化提取器"""
        self.patterns = {
            'brand': get_compiled_patterns('brand'),
            'series': get_compiled_patterns('series'),
            'model': get_compiled_patterns('model'),
            'platform': get_compiled_patterns('platform'),
            'ecu': get_compiled_patterns('ecu'),
            'subsystem': get_compiled_patterns('subsystem'),
            'doc_type': get_compiled_patterns('doc_type'),
            'supplier': get_compiled_patterns('supplier'),
            'emission': get_compiled_patterns('emission'),
            'drive': get_compiled_patterns('drive'),
            'batch': get_compiled_patterns('batch'),
        }
        self._batch_patterns = self.patterns.get('batch', [])

    def _is_batch_code(self, text: str) -> bool:
        if not text:
            return False
        value = self._normalize(text)
        for pat in self._batch_patterns:
            if pat.fullmatch(value):
                return True
        return False

    def _extract_best_model(self, text: str) -> Optional[str]:
        """提取并挑选最合理的“车型型号/平台代号”。

        注意：工程命名中常见的版本号/批次号（如 H210426）不应作为 model。
        """
        candidates: List[tuple[str, int]] = []
        for pattern in self.patterns.get('model', []):
            for m in pattern.finditer(text):
                token = m.group(0)
                token_norm = self._normalize(token)
                if not token_norm:
                    continue
                if self._is_batch_code(token_norm):
                    continue
                candidates.append((token_norm, m.start()))

        if not candidates:
            return None

        def score(token_with_pos: tuple[str, int]) -> tuple[int, int]:
            token, pos = token_with_pos
            base = 0
            if re.match(r"^D\d{3,4}$", token, re.IGNORECASE):
                base = 100
            elif re.match(r"^(KM|KN|KL|KR|VL)\d+$", token, re.IGNORECASE):
                base = 90
            elif re.match(r"^CA\d{4}", token, re.IGNORECASE):
                base = 80
            elif re.match(r"^(ZZ|HW)\d{4}", token, re.IGNORECASE):
                base = 75
            else:
                base = 50
            # 更短更像“平台/型号”；过长通常是公告号/项目号等工程码
            base -= min(len(token), 20)
            # 位置越靠前越可信
            base -= min(pos, 200) // 20
            return base, -pos  # 同分时优先靠前

        best = max(candidates, key=score)[0]
        return best

    def extract(self, text: str, hierarchy_parts: Optional[List[str]] = None,
                hierarchy_level_1: Optional[str] = None,
                hierarchy_level_2: Optional[str] = None) -> Dict:
        """
        从文本中提取实体

        Args:
            text: 文件名或其他文本
            hierarchy_parts: 层级路径列表（可选），用于增强提取
            hierarchy_level_1: 一级目录名（可选），用于 doc_type 提取
            hierarchy_level_2: 二级目录名（可选），用于 doc_type 提取

        Returns:
            提取结果字典：
            {
                'brand': str,              # 品牌（单值，取第一个）
                'series': str,             # 系列（单值）
                'model': str,              # 型号（单值）
                'model_variants': List[str],  # 型号变体（多值）
                'platform_codes': List[str],  # 平台代码
                'subsystems': List[str],      # 子系统
                'doc_types': List[str],       # 文档类型
                'ecus': List[str],            # ECU/控制器
                'suppliers': List[str],       # 供应商
                'emissions': List[str],       # 排放标准
                'drive_types': List[str],     # 驱动类型
                'batches': List[str],         # 批次
            }
        """
        # 剥离文件扩展名后再做匹配（扩展名不参与实体提取）
        text_clean = self._strip_file_extensions(text)

        # 合并文本（文件名 + 层级路径）
        full_text = text_clean
        if hierarchy_parts:
            full_text = text_clean + ' ' + ' '.join(
                self._strip_file_extensions(p) for p in hierarchy_parts
            )

        # 兼容历史调用：未显式传 level_1/2 时，从 hierarchy_parts 推断
        if hierarchy_parts:
            if not hierarchy_level_1 and len(hierarchy_parts) >= 1:
                hierarchy_level_1 = hierarchy_parts[0]
            if not hierarchy_level_2 and len(hierarchy_parts) >= 2:
                hierarchy_level_2 = hierarchy_parts[1]

        # 提取各类实体
        entities = {
            'brand': self._extract_single('brand', full_text, hierarchy_parts),
            'series': self._extract_single('series', full_text, hierarchy_parts),
            'model': self._extract_best_model(full_text),
            'model_variants': self._extract_model_variants(text_clean),
            'platform_codes': self._extract_list('platform', full_text),
            'subsystems': self._extract_list('subsystem', full_text),
            'doc_types': self._extract_doc_types_v2(text_clean, hierarchy_level_1, hierarchy_level_2),
            'ecus': self._extract_list('ecu', full_text),
            'suppliers': self._extract_list('supplier', full_text),
            'emissions': self._extract_emissions(full_text),
            'drive_types': self._extract_list('drive', full_text),
            'batches': self._extract_list('batch', full_text),
        }

        # 防御性清洗：避免批次/版本号被误识别为型号
        if entities.get('model') and self._is_batch_code(entities['model']):
            entities['model'] = None

        if entities.get('model_variants'):
            entities['model_variants'] = [
                v for v in entities['model_variants'] if not self._is_batch_code(v)
            ]

        return entities

    def _extract_single(self, category: str, text: str,
                       hierarchy_parts: Optional[List[str]] = None) -> Optional[str]:
        """
        提取单值实体（取第一个匹配）

        Args:
            category: 实体类别
            text: 待提取文本
            hierarchy_parts: 层级路径（优先从层级提取品牌/系列）

        Returns:
            提取结果（单值）
        """
        # 对于品牌和系列，优先从层级路径提取
        if category in ['brand', 'series'] and hierarchy_parts:
            for level in hierarchy_parts:
                matches = self._match_patterns(category, level)
                if matches:
                    return self._normalize(matches[0])

        # 从完整文本提取
        matches = self._match_patterns(category, text)
        if matches:
            return self._normalize(matches[0])

        return None

    def _extract_list(self, category: str, text: str) -> List[str]:
        """
        提取列表实体（所有匹配）

        Args:
            category: 实体类别
            text: 待提取文本

        Returns:
            提取结果列表（去重）
        """
        matches = self._match_patterns(category, text)
        return [self._normalize(m) for m in matches]

    def _extract_emissions(self, text: str) -> List[str]:
        """
        提取排放标准，并展开简写格式

        处理以下简写格式：
        - 国四_五 → ['国四', '国五']
        - 国四/五 → ['国四', '国五']
        - 国四、五 → ['国四', '国五']
        - 国四-五 → ['国四', '国五']
        - 国四_五_六 → ['国四', '国五', '国六']

        Args:
            text: 待提取文本

        Returns:
            排放标准列表（去重）
        """
        results = set()

        # Step 1: 先处理三连简写格式（如 国四_五_六）
        for match in self._EMISSION_TRIPLE_PATTERN.finditer(text):
            first, _, second, third = match.groups()
            first_norm = self._EMISSION_NUM_MAP.get(first, first)
            second_norm = self._EMISSION_NUM_MAP.get(second, second)
            third_norm = self._EMISSION_NUM_MAP.get(third, third)
            results.add(f'国{first_norm}')
            results.add(f'国{second_norm}')
            results.add(f'国{third_norm}')

        # Step 2: 处理双连简写格式（如 国四_五）
        for match in self._EMISSION_SHORTHAND_PATTERN.finditer(text):
            first, _, second = match.groups()
            first_norm = self._EMISSION_NUM_MAP.get(first, first)
            second_norm = self._EMISSION_NUM_MAP.get(second, second)
            results.add(f'国{first_norm}')
            results.add(f'国{second_norm}')

        # Step 3: 用标准正则提取完整格式（如 国四、国五）
        standard_matches = self._extract_list('emission', text)
        for m in standard_matches:
            results.add(m)

        return list(results)

    def _extract_doc_types(self, text: str) -> List[str]:
        """提取并归一化文档类型（用于避免 起动/启动 等写法差异导致过滤失效）。

        注意：此方法仅作为兜底，优先使用 _extract_doc_types_v2。
        """
        raw = self._extract_list('doc_type', text)
        if not raw:
            return []

        normalized = []
        for item in raw:
            value = item
            for src, dst in self._DOC_TYPE_NORMALIZATION_REPLACEMENTS:
                value = value.replace(src, dst)
            normalized.append(value)

        seen = set()
        deduped = []
        for item in normalized:
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)

        return deduped

    def _extract_doc_types_v2(
        self,
        filename: str,
        hierarchy_level_1: Optional[str] = None,
        hierarchy_level_2: Optional[str] = None
    ) -> List[str]:
        """提取文档类型 - 三层策略

        策略优先级：
        1. hierarchy_level_1 映射（覆盖 77% 文件）
        2. hierarchy_level_2 映射（覆盖复合分区，额外 18%）
        3. 文件名关键词推断（兜底，覆盖剩余 5%）

        Args:
            filename: 文件名（已剥离扩展名）
            hierarchy_level_1: 一级目录名
            hierarchy_level_2: 二级目录名

        Returns:
            doc_type 大类列表（通常只有一个元素）
        """
        # 1) 路径信号优先：先确定粗类，再提取细类
        level_1 = self._normalize_doc_type_value(hierarchy_level_1)
        level_2 = self._normalize_doc_type_value(hierarchy_level_2)

        path_coarse: Optional[str] = None
        for level in [level_1, level_2]:
            if not level:
                continue
            mapped = self.HIERARCHY_TO_DOC_TYPE.get(level)
            if mapped:
                path_coarse = self._normalize_doc_type_value(mapped)
                logger.debug(f"doc_type 从路径提取粗类: {level} → {path_coarse}")
                break

        path_fines: List[str] = []
        for level in [level_1, level_2]:
            if not level:
                continue
            fine = self.HIERARCHY_FINE_DOC_TYPE.get(level)
            if fine:
                fine_norm = self._normalize_doc_type_value(fine)
                if fine_norm:
                    path_fines.append(fine_norm)
                    logger.debug(f"doc_type 从路径提取细类: {level} → {fine_norm}")

        selected_coarse = path_coarse
        selected_fines: List[str] = []
        for fine in path_fines:
            coarse = self._coarse_for_doc_type(fine)
            if selected_coarse is None:
                selected_coarse = coarse
            if coarse and selected_coarse == coarse:
                selected_fines.append(fine)

        # 2) 文件名补充：用于补全路径缺失信息；与路径冲突时路径优先
        filename_signals: List[str] = []
        if filename:
            filename_signals.append(filename)
            filename_signals.extend(self._extract_doc_types(filename))

        for signal in filename_signals:
            canonical = self._canonicalize_doc_type_signal(signal)
            if not canonical:
                continue

            coarse = self._coarse_for_doc_type(canonical)
            if not coarse:
                continue

            is_fine = canonical in self.DOC_TYPE_FINE_TO_COARSE
            if not is_fine:
                # 粗类信号：仅在尚未确定粗类时使用
                if selected_coarse is None:
                    selected_coarse = canonical
                continue

            if selected_coarse is None:
                # 仅文件名提供细类时，反推出粗类
                selected_coarse = coarse
                selected_fines.append(canonical)
                continue

            if coarse != selected_coarse:
                # 路径和文件名冲突时，路径优先（或保留先到先得）
                logger.debug(
                    f"doc_type 冲突，忽略文件名细类: '{canonical}' (coarse={coarse}), "
                    f"selected_coarse={selected_coarse}"
                )
                continue

            if path_fines and canonical not in path_fines:
                # 已有路径细类时，不用文件名不同细类覆盖
                continue

            selected_fines.append(canonical)

        # 3) 组装输出：同一列维护 [粗类, 细类...]
        if not selected_coarse:
            return []

        ordered = [selected_coarse] + selected_fines
        deduped: List[str] = []
        seen = set()
        for item in ordered:
            v = self._normalize_doc_type_value(item)
            if not v:
                continue
            k = v.lower()
            if k in seen:
                continue
            seen.add(k)
            deduped.append(v)

        return deduped

    def _normalize_doc_type_value(self, text: Optional[str]) -> str:
        """归一化 doc_type 文本（处理起动/启动等写法差异）。"""
        if not text:
            return ""
        value = str(text).strip()
        if not value:
            return ""
        for src, dst in self._DOC_TYPE_NORMALIZATION_REPLACEMENTS:
            value = value.replace(src, dst)
        return value

    def _get_allowed_doc_type_values(self) -> Set[str]:
        """获取允许落库的 doc_type 值集合（优先用维度词表）。"""
        # 优先使用维度配置，确保与用户定义保持一致
        try:
            from app.legacy.services.dimension_service import dimension_service
            if dimension_service.is_loaded:
                values = dimension_service.get_all_values('doc_type')
                normalized = {
                    self._normalize_doc_type_value(v) for v in values if v
                }
                normalized.discard("")
                if normalized:
                    return normalized
        except Exception:
            # 维度服务不可用时降级到内置集合
            pass

        return set(self.DOC_TYPE_COARSE_VALUES) | set(self.DOC_TYPE_FINE_TO_COARSE.keys())

    def _ensure_allowed_doc_type(self, value: Optional[str]) -> Optional[str]:
        """只允许已定义的 doc_type 值通过。"""
        normalized = self._normalize_doc_type_value(value)
        if not normalized:
            return None
        return normalized if normalized in self._get_allowed_doc_type_values() else None

    def _coarse_for_doc_type(self, value: str) -> Optional[str]:
        """返回 doc_type 对应的粗类；未知类型返回 None。"""
        normalized = self._normalize_doc_type_value(value)
        if not normalized:
            return None

        if not self._ensure_allowed_doc_type(normalized):
            return None

        if normalized in self.DOC_TYPE_COARSE_VALUES:
            return self._ensure_allowed_doc_type(normalized)

        if normalized in self.DOC_TYPE_FINE_TO_COARSE:
            return self._ensure_allowed_doc_type(self.DOC_TYPE_FINE_TO_COARSE[normalized])

        return None

    def _canonicalize_doc_type_signal(self, text: str) -> Optional[str]:
        """把路径/文件名信号规范到已知 doc_type 值。"""
        signal = self._normalize_doc_type_value(text)
        if not signal:
            return None

        # 优先用维度词典（如果已加载）
        try:
            from app.legacy.services.dimension_service import dimension_service
            if dimension_service.is_loaded:
                matched = dimension_service.find_value_by_pattern(signal)
                if matched and matched[0] == 'doc_type':
                    canonical = self._normalize_doc_type_value(matched[1])
                    if canonical:
                        return self._ensure_allowed_doc_type(canonical)
        except Exception:
            # 维度服务异常不应阻断入库
            pass

        # 路径字典映射
        if signal in self.HIERARCHY_FINE_DOC_TYPE:
            return self._ensure_allowed_doc_type(self.HIERARCHY_FINE_DOC_TYPE[signal])
        if signal in self.HIERARCHY_TO_DOC_TYPE:
            return self._ensure_allowed_doc_type(self.HIERARCHY_TO_DOC_TYPE[signal])

        # 文件名细类兜底识别
        if re.search(r'(?:起动|启动)(?:原理)?(?:电路)?图', signal):
            return self._ensure_allowed_doc_type('启动原理图')
        if 'ecu' in signal.lower() and any(x in signal for x in ('电路图', '线路图', '接线图', '针脚定义')):
            return self._ensure_allowed_doc_type('ECU电路图')
        if 'can' in signal.lower() and '总线' in signal:
            return self._ensure_allowed_doc_type('CAN总线图')
        if '整车' in signal and any(x in signal for x in ('整车图', '整车电路图', '全车电路图', '整车线束图')):
            return self._ensure_allowed_doc_type('整车电路图')

        # 文件名粗类兜底识别
        for keyword, doc_type in self.FILENAME_DOC_TYPE_KEYWORDS:
            if keyword in signal:
                return self._ensure_allowed_doc_type(doc_type)

        # 若恰好是已定义值则直接使用（否则视为未知类型，不入 doc_types）
        if signal in self.DOC_TYPE_COARSE_VALUES or signal in self.DOC_TYPE_FINE_TO_COARSE:
            return self._ensure_allowed_doc_type(signal)

        return None

    def _match_patterns(self, category: str, text: str) -> List[str]:
        """
        使用正则表达式匹配

        Args:
            category: 实体类别
            text: 待匹配文本

        Returns:
            匹配结果列表
        """
        patterns = self.patterns.get(category, [])
        if not patterns or not text:
            return []

        # 记录匹配位置，保证提取顺序稳定（避免 set 导致的随机顺序）
        positioned_matches: List[tuple[int, int, str]] = []
        for pattern in patterns:
            for m in pattern.finditer(text):
                token = m.group(0)
                if not token:
                    continue
                # (起始位置, 负长度, 原文)；同位置优先更长词
                positioned_matches.append((m.start(), -len(token), token))

        if not positioned_matches:
            return []

        positioned_matches.sort(key=lambda x: (x[0], x[1]))

        # 按出现顺序去重（大小写不敏感）
        seen = set()
        ordered = []
        for _, _, token in positioned_matches:
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(token)

        return ordered

    def _extract_model_variants(self, text: str) -> List[str]:
        """
        提取型号变体（特殊处理多型号情况）

        例如："三一_SY55_SY60_SY65" -> ["SY55", "SY60", "SY65"]

        Args:
            text: 文件名

        Returns:
            型号变体列表
        """
        variants = set()

        # 使用型号正则提取
        model_patterns = self.patterns['model']
        for pattern in model_patterns:
            found = pattern.findall(text)
            variants.update(found)

        # 特殊处理：连续的型号代码
        # 例如：KM8N.KM9N 或 SY55_SY60
        variant_pattern = re.compile(
            r'\b([A-Z]{2}\d+[A-Z]?)\b',
            re.IGNORECASE
        )
        found = variant_pattern.findall(text)
        variants.update(found)

        # 标准化并去重
        return sorted([self._normalize(v) for v in variants])

    def _strip_file_extensions(self, text: str) -> str:
        """剥离文件扩展名，避免扩展名干扰实体提取

        例如：'东风多利卡国六.DOCX' → '东风多利卡国六'
        数据库中的文件名不受影响，仅在提取前临时处理。
        """
        return self._FILE_EXTENSIONS.sub('', text)

    @staticmethod
    def _normalize(text: str) -> str:
        """
        标准化文本

        - 去除前后空白
        - 统一大小写（代号大写，中文不变）

        Args:
            text: 待标准化文本

        Returns:
            标准化后的文本
        """
        text = text.strip()

        # 如果全是字母和数字，转大写
        if re.match(r'^[A-Za-z0-9]+$', text):
            return text.upper()

        return text


# ==================== 便捷函数 ====================

_extractor = None


def get_extractor() -> EntityExtractor:
    """获取全局提取器实例（单例模式）"""
    global _extractor
    if _extractor is None:
        _extractor = EntityExtractor()
    return _extractor


def extract_entities(filename: str, hierarchy_parts: Optional[List[str]] = None) -> Dict:
    """
    提取实体（便捷函数）

    Args:
        filename: 文件名
        hierarchy_parts: 层级路径列表

    Returns:
        提取结果字典
    """
    extractor = get_extractor()
    return extractor.extract(filename, hierarchy_parts)


# ==================== 测试代码 ====================

if __name__ == "__main__":
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    print("=" * 80)
    print("实体提取测试")
    print("=" * 80)

    # 测试用例
    test_cases = [
        {
            'filename': '东风天锦_D530.KM8N_整车电路图【UL2尿素泵】【国六】',
            'hierarchy': ['整车电路图', '东风', '天锦', 'KM']
        },
        {
            'filename': '康明斯_ISDE_CM2150_燃油系统诊断测试及技术规范.PDF',
            'hierarchy': ['发动机', '诊断手册', '康明斯', 'ISDe系列']
        },
        {
            'filename': '三一_SY55_SY60_SY65_SY75-9挖掘机_仪表显示器针脚定义',
            'hierarchy': ['电路图', 'ECU电路图', '工程机械', '三一', 'SY55']
        },
        {
            'filename': '上汽_依维柯_红岩_908经典版_潍柴_上柴SC10EF_SC12EF_国四_EDC17_整车启动电路图.PDF',
            'hierarchy': ['起动原理图', '上汽红岩', '红岩']
        },
        {
            'filename': '解放J6P_CA1234_博世EDC17CV44_6x4_国五_整车电路图',
            'hierarchy': ['整车电路图', '解放', 'J6P']
        }
    ]

    extractor = EntityExtractor()

    for i, test in enumerate(test_cases, 1):
        print(f"\n测试用例 {i}:")
        print(f"文件名: {test['filename']}")
        print(f"层级: {' -> '.join(test['hierarchy'])}")

        entities = extractor.extract(test['filename'], test['hierarchy'])

        print("\n提取结果:")
        for key, value in entities.items():
            if value:  # 只显示非空值
                if isinstance(value, list):
                    print(f"  {key}: {', '.join(value)}")
                else:
                    print(f"  {key}: {value}")

        print("-" * 80)

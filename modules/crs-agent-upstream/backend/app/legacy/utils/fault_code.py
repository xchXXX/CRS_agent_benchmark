"""故障码检测工具

统一的故障码正则和检测逻辑，确保全系统一致性。

故障码格式说明：
- 标准 OBD-II：P/C/B/U + 4位十六进制（如 P0251）
- 扩展格式：P/C/B/U + 5-6位十六进制（如 P01F5A）
- P = Powertrain（动力总成）
- C = Chassis（底盘）
- B = Body（车身）
- U = Network（网络通信）
"""

import re
from typing import List, Optional, Tuple


# 统一的故障码正则模式
# 支持 4-6 位十六进制（P0251, P01F5, P01F5A）
FAULT_CODE_PATTERN = re.compile(
    r'(?<![A-Za-z0-9])([PBCU][0-9A-Fa-f]{4,6})(?![A-Za-z0-9])',
    re.IGNORECASE
)

# 简单版本（无边界检查，用于快速检测）
FAULT_CODE_PATTERN_SIMPLE = re.compile(
    r'[PBCU][0-9A-Fa-f]{4,6}',
    re.IGNORECASE
)


def extract_fault_codes(text: str) -> List[str]:
    """从文本中提取故障码

    Args:
        text: 输入文本

    Returns:
        故障码列表（已转大写）
    """
    matches = FAULT_CODE_PATTERN.findall(text)
    return [code.upper() for code in matches]


def has_fault_code(text: str) -> bool:
    """检测文本是否包含故障码

    Args:
        text: 输入文本

    Returns:
        是否包含故障码
    """
    return bool(FAULT_CODE_PATTERN_SIMPLE.search(text))


def is_pure_fault_code(text: str) -> bool:
    """检测文本是否是纯故障码（只有故障码，没有其他内容）

    Args:
        text: 输入文本

    Returns:
        是否为纯故障码
    """
    text = text.strip()
    if not text:
        return False

    # 去掉故障码后看是否还有其他内容
    remaining = FAULT_CODE_PATTERN.sub('', text).strip()
    # 允许有逗号、空格分隔
    remaining = re.sub(r'[,，\s]+', '', remaining)

    return len(remaining) == 0 and has_fault_code(text)


def parse_fault_code(code: str) -> Optional[Tuple[str, str]]:
    """解析故障码

    Args:
        code: 故障码字符串

    Returns:
        (类型前缀, 数字部分) 或 None
    """
    code = code.strip().upper()
    match = re.match(r'^([PBCU])([0-9A-F]{4,6})$', code, re.IGNORECASE)
    if match:
        return match.group(1), match.group(2)
    return None


def get_fault_code_type(code: str) -> Optional[str]:
    """获取故障码类型描述

    Args:
        code: 故障码

    Returns:
        类型描述
    """
    parsed = parse_fault_code(code)
    if not parsed:
        return None

    type_map = {
        'P': '动力总成 (Powertrain)',
        'C': '底盘 (Chassis)',
        'B': '车身 (Body)',
        'U': '网络通信 (Network)'
    }

    return type_map.get(parsed[0])

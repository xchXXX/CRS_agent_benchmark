"""正则表达式配置 - 用于实体提取"""

import re
from typing import Dict, List

# ==================== 品牌正则表达式 ====================

BRAND_PATTERNS = [
    # 一汽系列
    r'一汽(?:解放)?',
    r'解放',
    r'FAW',

    # 东风系列
    r'东风(?:柳汽|柳州)?',
    r'东风',
    r'DFAC',
    r'DFL',

    # 重汽系列
    r'(?:中国)?重汽',
    r'CNHTC',
    r'SINOTRUK',

    # 陕汽
    r'陕汽',
    r'陕西汽车',

    # 福田
    r'北汽福田',
    r'福田',
    r'FOTON',

    # 上汽系列
    r'上汽(?:红岩)?',
    r'依维柯',
    r'IVECO',

    # 其他品牌
    r'江淮',
    r'JAC',
    r'江铃',
    r'JMC',
    r'北奔',
    r'华菱',
    r'大运',
    r'宇通',
    r'金龙',
    r'联合',
    r'柳工',
    r'徐工',
    r'三一',
    r'SANY',
    r'沃尔沃',
    r'VOLVO',
    r'卡特',
    r'奔驰',
    r'日野',
    r'五十铃',
]

# ==================== 系列正则表达式 ====================

SERIES_PATTERNS = [
    # 解放系列
    r'J6[PLM]?',
    r'J5',
    r'J7',
    r'JH6',
    r'悍V',
    r'赛龙',

    # 东风系列
    r'天锦(?:旗舰)?(?:KR|VR)?',  # 支持天锦KR、天锦VR
    r'天龙(?:旗舰)?(?:KL|KF|VL)?',  # 支持天龙KL、天龙KF、天龙VL
    r'启航版',
    r'大力神',
    r'凯普特',
    r'多利卡',
    r'华神',
    r'小霸王',
    r'乘龙(?:L\d|H\d|M\d)?',  # 乘龙L2、H5等

    # 重汽系列
    r'豪沃(?:T[57]G?H?)?',
    r'豪瀚',
    r'豪曼',
    r'豪运',
    r'汕德卡',
    r'斯太尔',
    r'HOWO',

    # 陕汽系列
    r'德龙(?:X\d{4})?',
    r'奥龙',
    r'轩德',

    # 福田系列
    r'欧曼',
    r'奥铃',
    r'GTL',
    r'EST',
    r'ETX',

    # 江淮系列
    r'格尔发',
    r'骏铃',
    r'帅铃',
    r'凯运',
    r'威铃',

    # 红岩系列
    r'杰狮',
    r'红岩',
]

# ==================== 型号正则表达式 ====================

MODEL_PATTERNS = [
    # 东风型号
    # 注意：使用 \d+ 而非 \d*，要求至少一位数字
    # 这样避免单独的 KR/KM/KN/KL/VL 被误识别为型号（它们通常是系列变体名的一部分）
    r'KM\d+',
    r'KN\d+',
    r'KL\d+',
    r'KR\d+',
    r'VL\d+',
    r'DFL\d{4}',

    # 解放型号
    r'CA\d{4}[A-Z]?\d*[A-Z]?\d*',

    # 重汽型号
    r'ZZ\d{4}',
    r'HW\d{4}',

    # 三一型号
    r'SY\d+C?\d*',

    # 通用型号
    r'[A-Z]{1,3}\d{3,4}[A-Z]?\d*',
]

# ==================== 平台代码正则表达式 ====================

PLATFORM_PATTERNS = [
    r'D\d{2,4}',              # D6, D12, D34, D530, D560（放宽为2-4位）
    r'K[FLMNR][A-Z0-9]{0,4}', # KF, KL, KM, KN, KR, KM8N, KL1N（支持无后缀）
    r'VL\d*',                 # VL, VL123
    r'VR\d*',                 # VR, VR123
    r'CA\d{4}[A-Z]?\d+[A-Z]?\d*',  # CA1234
    r'[A-Z]{2}\d{4}',         # ZZ1234
]

# ==================== ECU/控制器正则表达式 ====================

ECU_PATTERNS = [
    # 康明斯系列
    r'CM\d{3,4}',             # CM2150, CM2880
    r'ISBe\d*',
    r'ISLe\d*',
    r'ISDe\d*',
    r'ISM\d*',
    r'ISX\d*',

    # 博世系列
    r'EDC\d{1,2}[A-Z]{2,4}\d{0,3}',  # EDC17CV44, EDC7UC31

    # 电装系列
    r'MD1(?:[A-Z]{2}\d*)?',   # MD1, MD1CE, MD1CE108, MD1CS089
    r'DCM[\d.]+',

    # 其他ECU
    r'Econtrol',
    r'FEUP',
    r'FCRI',
    r'YCGCU',
    r'YCECU',
    r'DCi\d*',
    r'DDi\d+',
    r'WISE\d+[A-Z]?',

    # BCU系列
    r'NanoBCU',
    r'CBCU',
    r'SmartBCU',
    r'MINI控制器',
    r'迷你控制器',
]

# ==================== 子系统/模块正则表达式 ====================

SUBSYSTEM_PATTERNS = [
    # 系统类/部件类（不包含资料类型）
    r'仪表(?:显示器)?',
    r'显示器',

    # 车身控制
    r'BCM',
    r'BBM',
    r'VCU',
    r'HCU',
    r'TRU',

    # 底盘系统
    r'ABS',
    r'EBS',
    r'ECAS',
    r'AMT',
    r'空气悬架',

    # 后处理系统
    r'尿素泵',
    r'后处理(?:泵)?',
    r'UL2(?:泵)?',
    r'SCR(?:泵|催化器)?',
    r'DPF(?:控制器)?',
    r'DOC',
    r'ASC',
    r'POC',
    r'颗粒(?:捕集器|过滤器)',
    r'碳罐',

    # 后处理DCU
    r'DCU',
]


# ==================== 文档类型/资料类型正则表达式 ====================

DOC_TYPE_PATTERNS = [
    # 电路图/原理图类
    r'整车(?:线束)?电(?:原理|路)图',
    r'整车图',                # 简写
    r'全车电(?:原理|路)图',
    r'ECU电路图',
    r'(?:起动|启动)(?:原理)?(?:电路)?图',
    r'CAN总线图',
    r'线束图(?:解)?',         # 线束图、线束图解
    r'汽车线束图',
    r'线路图',
    r'接线图',
    r'电原理图',
    r'电路图',
    r'仪表电路图',
    r'(?:上装)?控制器电路图',

    # 接插件/针脚类
    r'针脚(?:定义|图)?',
    r'引脚(?:定义|图)?',
    r'PIN定义',
    r'接插件(?:定义)?',

    # 保险盒类
    r'保险盒(?:定义)?',
    r'保险丝盒',
    r'熔断丝(?:定义)?',
    r'继电器盒',
    r'电器盒',

    # 手册/说明/培训类
    r'诊断(?:手册|指导)',
    r'维(?:修|保)(?:手册|资料)',
    r'使用说明',
    r'培训(?:教材|讲义)',
    r'故障(?:代码|码|诊断)',
    r'数据流',
    r'标准数据流',
    r'正时配气',
]

# ==================== 供应商正则表达式 ====================

SUPPLIER_PATTERNS = [
    # 外资品牌
    r'博世',
    r'Bosch',
    r'BOSCH',
    r'康明斯',
    r'Cummins',
    r'CUMMINS',
    r'电装',
    r'DENSO',
    r'德尔福',
    r'Delphi',
    r'伍德沃德',
    r'Woodward',

    # 国内供应商
    r'威孚(?:力达)?',
    r'潍柴',
    r'玉柴',
    r'锡柴',
    r'上柴',
    r'云内',
    r'全柴',
    r'朝柴',
    r'扬柴',

    # 后处理供应商
    r'依米泰克',
    r'EMITEC',
    r'天纳克',
    r'Tenneco',
    r'凯龙',
    r'三立',
    r'秦泰',
    r'依科菲特',
    r'Ecofit',
    r'艾可蓝',
    r'恒和',
    r'添蓝',
    r'凯德斯',
]

# ==================== 排放标准正则表达式 ====================

EMISSION_PATTERNS = [
    r'国[二三四五六2-6]',      # 支持中文和阿拉伯数字
    r'CHINA[2-6]',
    r'CN[2-6]',
    r'欧[2-6]',
    r'Euro[2-6]',
    r'EGR',
]

# ==================== 驱动类型正则表达式 ====================

DRIVE_PATTERNS = [
    r'[4-8]x[2-8]',
]

# ==================== 批次正则表达式 ====================

BATCH_PATTERNS = [
    r'H\d{4,6}',
]

# ==================== 编译正则表达式 ====================

def compile_patterns(pattern_list: List[str]) -> List[re.Pattern]:
    """编译正则表达式列表"""
    return [re.compile(pattern, re.IGNORECASE) for pattern in pattern_list]


# 编译后的正则表达式
COMPILED_PATTERNS = {
    'brand': compile_patterns(BRAND_PATTERNS),
    'series': compile_patterns(SERIES_PATTERNS),
    'model': compile_patterns(MODEL_PATTERNS),
    'platform': compile_patterns(PLATFORM_PATTERNS),
    'ecu': compile_patterns(ECU_PATTERNS),
    'subsystem': compile_patterns(SUBSYSTEM_PATTERNS),
    'doc_type': compile_patterns(DOC_TYPE_PATTERNS),
    'supplier': compile_patterns(SUPPLIER_PATTERNS),
    'emission': compile_patterns(EMISSION_PATTERNS),
    'drive': compile_patterns(DRIVE_PATTERNS),
    'batch': compile_patterns(BATCH_PATTERNS),
}


# ==================== 辅助函数 ====================

def get_compiled_patterns(category: str) -> List[re.Pattern]:
    """获取编译后的正则表达式"""
    return COMPILED_PATTERNS.get(category, [])

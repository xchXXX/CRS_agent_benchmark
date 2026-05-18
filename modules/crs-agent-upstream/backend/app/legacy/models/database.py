"""数据库模型定义（SQLAlchemy ORM）"""

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import (
    JSON,
    TIMESTAMP,
    BigInteger,
    Boolean,
    Column,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker
from sqlalchemy.sql import func

from app.core.config import settings


Base = declarative_base()


class PhysicalFile(Base):
    """物理文件表 - 存储文件系统扫描结果"""

    __tablename__ = "physical_files"

    file_id = Column(String(64), primary_key=True, comment="文件唯一ID (hash)")
    physical_path = Column(String(512), unique=True, nullable=False, comment="实际文件路径")
    filename = Column(String(256), nullable=False, comment="文件名（不含扩展名）")
    file_size = Column(BigInteger, comment="文件大小（字节）")
    file_type = Column(String(16), comment="文件类型（txt/pdf/wps）")
    parent_id = Column(BigInteger, comment="父节点ID（外部系统）")
    ref_file_id = Column(BigInteger, comment="关联文件ID（外部系统，用于显示）")
    hierarchy_level_1 = Column(String(64), comment="层级1（如：整车电路图）")
    hierarchy_level_2 = Column(String(64), comment="层级2（如：东风）")
    hierarchy_level_3 = Column(String(64), comment="层级3（如：天锦）")
    hierarchy_level_4 = Column(String(64), comment="层级4（如：KM）")
    hierarchy_full = Column(Text, comment="完整层级路径（用->分隔）")
    discovered_at = Column(TIMESTAMP, server_default=func.now(), comment="发现时间")
    modified_at = Column(TIMESTAMP, comment="文件修改时间")

    docs = relationship("Doc", back_populates="file", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_hierarchy_1", "hierarchy_level_1"),
        Index("idx_hierarchy_2", "hierarchy_level_2"),
        Index("idx_hierarchy_3", "hierarchy_level_3"),
        Index("idx_filename", "filename"),
        Index("idx_ref_file_id", "ref_file_id"),
        Index("idx_parent_id", "parent_id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )


class Doc(Base):
    """文档索引表 - 检索用（支持多虚拟条目）"""

    __tablename__ = "docs"

    doc_id = Column(String(64), primary_key=True, comment="文档唯一ID")
    file_id = Column(
        String(64),
        ForeignKey("physical_files.file_id", ondelete="CASCADE"),
        nullable=False,
        comment="关联物理文件",
    )
    brand = Column(String(64), comment="品牌（东风/解放/重汽等）")
    series = Column(String(64), comment="系列（天锦/J6P/豪沃等）")
    model = Column(String(64), comment="型号（KM/KN/KL等）")
    model_variants = Column(JSON, comment="型号变体列表（KM8N/KM9N等）")
    platform_codes = Column(JSON, comment="平台代码列表")
    subsystems = Column(JSON, comment="子系统列表")
    ecus = Column(JSON, comment="ECU/控制器列表")
    suppliers = Column(JSON, comment="供应商列表")
    emissions = Column(JSON, comment="排放标准列表")
    drive_types = Column(JSON, comment="驱动类型列表")
    batches = Column(JSON, comment="批次列表")
    doc_types = Column(JSON, comment="文档类型列表")
    eng_codes = Column(JSON, comment="工程命名编码集合（如 KR,3700001,KT1B0,H210426,D34 等）")
    searchable_text = Column(Text, nullable=False, comment="可检索文本（含同义词/拼音）")
    path_depth = Column(Integer, default=0, comment="路径深度")
    created_at = Column(TIMESTAMP, server_default=func.now(), comment="创建时间")

    file = relationship("PhysicalFile", back_populates="docs")

    __table_args__ = (
        Index("idx_file_id", "file_id"),
        Index("idx_brand", "brand"),
        Index("idx_series", "series"),
        Index("idx_model", "model"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )


class ExternalFileUrl(Base):
    """外部文件 URL 映射表"""

    __tablename__ = "external_file_urls"

    id = Column(Integer, primary_key=True, comment="关联文件ID (ref_file_id)")
    name = Column(String(500), comment="文件名")
    pic_folder_url = Column(String(1000), comment="图片文件夹URL（用于生成安全访问链接）")
    download_url = Column(String(1000), comment="下载URL")
    created_at = Column(TIMESTAMP, server_default=func.now(), comment="创建时间")
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now(), comment="更新时间")

    __table_args__ = (
        Index("idx_external_id", "id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )


class Synonym(Base):
    """同义词表 - 一组同义词共享同一个 group_id"""

    __tablename__ = "synonyms"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="记录ID")
    group_id = Column(String(64), nullable=False, comment="同义词组ID")
    term = Column(String(128), nullable=False, comment="词条")
    category = Column(
        String(32),
        nullable=False,
        comment="分类（brand/series/subsystem/supplier/emissions/doc_type）",
    )
    is_primary = Column(Boolean, default=False, comment="是否为主要词条（用于显示）")
    created_at = Column(TIMESTAMP, server_default=func.now(), comment="创建时间")

    __table_args__ = (
        Index("idx_group_id", "group_id"),
        Index("idx_term", "term"),
        Index("idx_category", "category"),
        Index("uk_group_term", "group_id", "term", unique=True),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )


class EntityPinyin(Base):
    """实体拼音索引表 - 用于语音纠错和拼音搜索"""

    __tablename__ = "entity_pinyin"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="主键ID")
    entity_type = Column(
        String(32),
        nullable=False,
        comment="实体类型: brand/series/ecu/supplier/emissions/subsystem",
    )
    entity_value = Column(String(128), nullable=False, comment="实体原文: 天锦")
    pinyin = Column(String(256), nullable=False, comment="完整拼音(无声调): tianjin")
    pinyin_tone = Column(String(256), comment="带声调拼音: tiān jǐn")
    pinyin_abbr = Column(String(64), nullable=False, comment="拼音首字母: tj")
    frequency = Column(Integer, default=0, comment="出现频次(用于排序)")
    is_standard = Column(Boolean, default=True, comment="是否为标准写法")
    created_at = Column(TIMESTAMP, server_default=func.now(), comment="创建时间")
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now(), comment="更新时间")

    __table_args__ = (
        Index("idx_pinyin", "pinyin"),
        Index("idx_pinyin_abbr", "pinyin_abbr"),
        Index("idx_entity_type", "entity_type"),
        Index("idx_frequency", "frequency"),
        Index("uk_entity", "entity_type", "entity_value", unique=True),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )


class ChatLog(Base):
    """对话日志表 - 记录用户问答和系统响应"""

    __tablename__ = "chat_logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="日志ID")
    request_id = Column(String(36), nullable=False, comment="请求唯一标识(UUID)")
    session_id = Column(String(64), nullable=False, comment="会话ID")
    user_id = Column(BigInteger, comment="用户ID（从 app token 解析）")

    user_message = Column(Text, nullable=False, comment="用户消息(完整内容)")
    client_type = Column(String(20), default="web", comment="客户端类型")
    request_mode = Column(String(20), default="auto", comment="请求模式")

    intent_type = Column(String(30), comment="识别的意图类型")
    intent_confidence = Column(Numeric(4, 3), comment="意图置信度(0.000-1.000)")
    intent_rule = Column(String(50), comment="匹配的规则名")
    intent_source = Column(String(20), comment="意图来源")

    response_type = Column(String(30), nullable=False, comment="响应类型")
    response_content = Column(Text, comment="响应内容(文本或JSON格式)")
    response_preview = Column(String(500), comment="响应预览")
    clarify_facet = Column(String(50), comment="澄清维度")
    clarify_options = Column(JSON, comment="澄清选项列表(JSON数组)")
    has_suggestions = Column(Boolean, default=False, comment="是否有推荐问题")

    elapsed_ms = Column(Integer, comment="总处理耗时(毫秒)")
    intent_elapsed_ms = Column(Integer, comment="意图识别耗时(毫秒)")

    error_type = Column(String(50), comment="错误类型")
    error_message = Column(Text, comment="错误详情")
    report_url = Column(String(500), comment="诊断报告链接")

    created_at = Column(TIMESTAMP, server_default=func.now(), comment="创建时间")

    __table_args__ = (
        Index("idx_chat_logs_session_id", "session_id"),
        Index("idx_chat_logs_created_at", "created_at"),
        Index("idx_chat_logs_intent_type", "intent_type"),
        Index("idx_chat_logs_response_type", "response_type"),
        Index("idx_chat_logs_request_id", "request_id"),
        Index("idx_chat_logs_user_id", "user_id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )


class UserFeedback(Base):
    """用户反馈表 - 收集用户对业务交互的满意度反馈"""

    __tablename__ = "user_feedback"

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="反馈记录ID")
    request_id = Column(String(36), nullable=False, unique=True, comment="关联请求ID")
    session_id = Column(String(64), nullable=False, comment="会话ID")
    rating = Column(SmallInteger, nullable=False, comment="评分1-10，对应半星")
    business_type = Column(String(30), nullable=False, comment="业务类型")
    tags = Column(JSON, comment="快捷标签JSON数组")
    comment = Column(Text, comment="文本反馈(可选)")
    created_at = Column(TIMESTAMP, server_default=func.now(), comment="创建时间")

    __table_args__ = (
        Index("idx_user_feedback_session_id", "session_id"),
        Index("idx_user_feedback_business_type", "business_type"),
        Index("idx_user_feedback_rating", "rating"),
        Index("idx_user_feedback_created_at", "created_at"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_unicode_ci",
        },
    )


class ChatTaskLog(Base):
    """Loop 任务级日志：一次问题从提出到最终收敛的完整闭环。"""

    __tablename__ = "chat_task_logs"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="主键ID")
    task_id = Column(String(36), nullable=False, unique=True, comment="任务唯一标识")
    session_id = Column(String(64), nullable=False, comment="会话ID")
    user_id = Column(BigInteger, comment="用户ID")
    client_type = Column(String(20), default="web", comment="客户端类型")

    root_question = Column(Text, nullable=False, default="", comment="任务首个问题")
    latest_user_message = Column(Text, comment="最近一次用户输入")
    business_type = Column(String(30), comment="业务场景")

    task_status = Column(String(30), nullable=False, default="completed", comment="任务状态")
    end_reason = Column(String(50), comment="结束原因")
    convergence_mode = Column(String(50), comment="收敛模式")
    final_response_type = Column(String(30), comment="最终响应类型")
    final_response_preview = Column(String(500), comment="最终响应摘要")
    final_response_payload = Column(JSON, comment="最终响应完整结构")

    latest_ask_user_question = Column(Text, comment="最近一次 ask_user 问题")
    latest_missing_fields = Column(JSON, comment="最近一次缺失字段")
    ask_user_triggered = Column(Boolean, default=False, comment="是否触发过 ask_user")
    ask_user_count = Column(Integer, default=0, comment="ask_user 总次数")
    run_count = Column(Integer, default=0, comment="执行次数")
    tool_call_count = Column(Integer, default=0, comment="工具调用总数")
    external_tool_call_count = Column(Integer, default=0, comment="外部工具调用总数")
    main_tool_names = Column(JSON, comment="高频工具名称")

    has_error = Column(Boolean, default=False, comment="是否发生错误")
    error_type = Column(String(50), comment="错误类型")
    error_message = Column(Text, comment="错误摘要")

    first_request_id = Column(String(36), comment="首个请求ID")
    last_request_id = Column(String(36), comment="最近请求ID")
    replaces_task_id = Column(String(36), comment="替代的上一个任务ID")
    replaced_by_task_id = Column(String(36), comment="被哪个新任务替代")

    total_elapsed_ms = Column(Integer, comment="累计耗时(毫秒)")
    started_at = Column(TIMESTAMP, server_default=func.now(), comment="任务开始时间")
    finished_at = Column(TIMESTAMP, comment="任务结束时间")
    created_at = Column(TIMESTAMP, server_default=func.now(), comment="创建时间")
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now(), comment="更新时间")

    __table_args__ = (
        Index("idx_chat_task_logs_task_id", "task_id"),
        Index("idx_chat_task_logs_session_id", "session_id"),
        Index("idx_chat_task_logs_user_id", "user_id"),
        Index("idx_chat_task_logs_business_type", "business_type"),
        Index("idx_chat_task_logs_status", "task_status"),
        Index("idx_chat_task_logs_created_at", "created_at"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )


class ChatRunLog(Base):
    """Loop 运行级日志：一次真实后端执行。"""

    __tablename__ = "chat_run_logs"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="主键ID")
    run_id = Column(String(36), nullable=False, unique=True, comment="运行唯一标识")
    task_id = Column(String(36), nullable=False, comment="所属任务ID")
    session_id = Column(String(64), nullable=False, comment="会话ID")
    request_id = Column(String(36), nullable=False, unique=True, comment="请求ID")
    user_id = Column(BigInteger, comment="用户ID")
    client_type = Column(String(20), default="web", comment="客户端类型")
    request_mode = Column(String(20), default="auto", comment="请求模式")
    transport = Column(String(20), default="http", comment="调用方式")
    sequence_no = Column(Integer, default=1, comment="任务内执行序号")
    trigger_type = Column(String(30), comment="触发方式")

    input_message = Column(Text, comment="本次输入问题")
    ask_user_answer_summary = Column(Text, comment="本次补充信息摘要")
    business_type = Column(String(30), comment="业务场景")

    run_status = Column(String(30), nullable=False, default="completed", comment="运行状态")
    end_reason = Column(String(50), comment="结束原因")
    convergence_mode = Column(String(50), comment="收敛模式")
    guard_error_code = Column(String(50), comment="Guard 错误码")
    response_type = Column(String(30), comment="响应类型")
    response_preview = Column(String(500), comment="响应摘要")
    response_payload = Column(JSON, comment="响应完整结构")
    response_metadata = Column(JSON, comment="响应元数据")

    ask_user_question = Column(Text, comment="本次 ask_user 问题")
    missing_fields = Column(JSON, comment="本次缺失字段")
    ask_user_count = Column(Integer, default=0, comment="本次 ask_user 次数")
    tool_call_count = Column(Integer, default=0, comment="本次工具调用数")
    external_tool_call_count = Column(Integer, default=0, comment="本次外部工具调用数")
    tool_names = Column(JSON, comment="本次工具名列表")

    model_provider = Column(String(80), comment="LLM Provider")
    model_name = Column(String(160), comment="LLM 模型名")
    llm_call_count = Column(Integer, default=0, comment="本次 LLM 调用次数")
    llm_elapsed_ms = Column(Integer, comment="LLM 累计耗时(毫秒)")
    llm_first_response_ms = Column(Integer, comment="首个 LLM 响应耗时(毫秒)")
    llm_request_count = Column(Integer, default=0, comment="模型 API 请求次数")
    input_token_count = Column(Integer, default=0, comment="输入 token 数")
    output_token_count = Column(Integer, default=0, comment="输出 token 数")
    total_token_count = Column(Integer, default=0, comment="总 token 数")
    reasoning_token_count = Column(Integer, default=0, comment="推理 token 数")
    estimated_cost_usd = Column(Numeric(12, 8), comment="预估模型费用(USD)")

    has_error = Column(Boolean, default=False, comment="是否错误")
    error_type = Column(String(50), comment="错误类型")
    error_message = Column(Text, comment="错误详情")

    elapsed_ms = Column(Integer, comment="本次耗时(毫秒)")
    started_at = Column(TIMESTAMP, server_default=func.now(), comment="开始时间")
    finished_at = Column(TIMESTAMP, comment="结束时间")
    created_at = Column(TIMESTAMP, server_default=func.now(), comment="创建时间")

    __table_args__ = (
        Index("idx_chat_run_logs_run_id", "run_id"),
        Index("idx_chat_run_logs_task_id", "task_id"),
        Index("idx_chat_run_logs_session_id", "session_id"),
        Index("idx_chat_run_logs_request_id", "request_id"),
        Index("idx_chat_run_logs_business_type", "business_type"),
        Index("idx_chat_run_logs_status", "run_status"),
        Index("idx_chat_run_logs_model_provider", "model_provider"),
        Index("idx_chat_run_logs_model_name", "model_name"),
        Index("idx_chat_run_logs_created_at", "created_at"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )


class ChatRunEventLog(Base):
    """Loop 事件级日志：一次运行内部的关键步骤。"""

    __tablename__ = "chat_run_event_logs"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="主键ID")
    event_id = Column(String(36), nullable=False, unique=True, comment="事件唯一标识")
    task_id = Column(String(36), nullable=False, comment="所属任务ID")
    run_id = Column(String(36), nullable=False, comment="所属运行ID")
    request_id = Column(String(36), nullable=False, comment="请求ID")
    session_id = Column(String(64), nullable=False, comment="会话ID")
    sequence_no = Column(Integer, default=1, comment="运行内序号")

    event_type = Column(String(80), nullable=False, comment="事件类型")
    phase = Column(String(30), comment="事件阶段")
    tool_name = Column(String(100), comment="关联工具名")
    summary = Column(String(500), comment="事件摘要")
    detail = Column(Text, comment="事件详情")
    payload = Column(JSON, comment="事件载荷")
    created_at = Column(TIMESTAMP, server_default=func.now(), comment="创建时间")

    __table_args__ = (
        Index("idx_chat_run_event_logs_task_id", "task_id"),
        Index("idx_chat_run_event_logs_run_id", "run_id"),
        Index("idx_chat_run_event_logs_request_id", "request_id"),
        Index("idx_chat_run_event_logs_session_id", "session_id"),
        Index("idx_chat_run_event_logs_event_type", "event_type"),
        Index("idx_chat_run_event_logs_tool_name", "tool_name"),
        Index("idx_chat_run_event_logs_created_at", "created_at"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )


class ParamKnowledgeSource(Base):
    """参数知识源镜像表"""

    __tablename__ = "param_knowledge_source"

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="本地主键ID")
    source_knowledge_id = Column(BigInteger, nullable=False, unique=True, comment="外部 ai_knowledge.id")
    source_type_id = Column(BigInteger, nullable=False, comment="外部 ai_knowledge.type_id")
    source_type_code = Column(String(50), nullable=False, comment="外部知识类型编码")
    title = Column(String(500), nullable=False, comment="外部知识标题")
    title_normalized = Column(String(500), nullable=False, default="", comment="标题归一化结果")
    ecu_name = Column(String(120), comment="解析出的 ECU 名称")
    ecu_name_normalized = Column(String(120), comment="ECU 名称归一化结果")
    system_voltage = Column(SmallInteger, comment="系统电压")
    pin_doc_kind = Column(String(32), nullable=False, default="unknown", comment="针脚文档类型")
    content_format = Column(String(10), nullable=False, default="md", comment="内容格式")
    content_summary = Column(String(500), comment="外部内容摘要")
    raw_content = Column(Text, comment="外部正文快照")
    content_hash = Column(String(64), comment="正文 SHA-256")
    source_status = Column(Boolean, nullable=False, default=True, comment="外部状态")
    source_is_deleted = Column(Boolean, nullable=False, default=False, comment="外部删除标记")
    source_latest_version = Column(Integer, nullable=False, default=1, comment="外部 latest_version")
    source_published_version = Column(Integer, comment="外部 published_version")
    source_update_time = Column(TIMESTAMP, nullable=False, comment="外部更新时间")
    parser_version = Column(String(32), nullable=False, comment="本地解析器版本")
    parse_status = Column(String(20), nullable=False, default="pending", comment="解析状态")
    parse_error = Column(String(1000), comment="最近一次解析错误")
    parsed_row_count = Column(Integer, nullable=False, default=0, comment="解析出的结构化行数")
    last_synced_at = Column(TIMESTAMP, comment="最近一次同步完成时间")
    created_at = Column(TIMESTAMP, server_default=func.now(), comment="创建时间")
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now(), comment="更新时间")

    __table_args__ = (
        Index("idx_param_knowledge_source_type_status", "source_type_code", "source_status", "source_is_deleted"),
        Index("idx_param_knowledge_source_ecu", "ecu_name_normalized"),
        Index("idx_param_knowledge_source_voltage", "system_voltage"),
        Index("idx_param_knowledge_source_update_time", "source_update_time"),
        Index("idx_param_knowledge_source_parse_status", "parse_status"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_unicode_ci",
        },
    )


class ParamPinRow(Base):
    """参数针脚结构化行表"""

    __tablename__ = "param_pin_row"

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="本地主键ID")
    source_knowledge_id = Column(BigInteger, nullable=False, comment="逻辑关联源知识ID")
    source_title = Column(String(500), nullable=False, comment="来源知识标题")
    ecu_name = Column(String(120), comment="标准 ECU 名称")
    ecu_name_normalized = Column(String(120), comment="ECU 名称归一化结果")
    system_voltage = Column(SmallInteger, comment="系统电压")
    row_no = Column(Integer, nullable=False, comment="原始 Markdown 表格行号")
    component_name = Column(String(200), comment="零部件名称")
    component_name_normalized = Column(String(200), comment="零部件归一化名称")
    ecu_pin_no = Column(String(100), comment="ECU 针脚编号")
    ecu_pin_no_normalized = Column(String(100), comment="ECU 针脚编号归一化结果")
    pin_definition = Column(String(200), comment="针脚定义")
    pin_definition_normalized = Column(String(200), comment="针脚定义归一化结果")
    connector_pin_no = Column(String(100), comment="接插件针脚号")
    open_voltage_text = Column(String(100), comment="开路电压原文")
    open_voltage_min = Column(Numeric(10, 3), comment="开路电压下限")
    open_voltage_max = Column(Numeric(10, 3), comment="开路电压上限")
    static_voltage_text = Column(String(100), comment="静态电压原文")
    static_voltage_min = Column(Numeric(10, 3), comment="静态电压下限")
    static_voltage_max = Column(Numeric(10, 3), comment="静态电压上限")
    idle_voltage_text = Column(String(100), comment="低怠速电压原文")
    idle_voltage_min = Column(Numeric(10, 3), comment="低怠速电压下限")
    idle_voltage_max = Column(Numeric(10, 3), comment="低怠速电压上限")
    remark = Column(String(1000), comment="备注")
    raw_row_json = Column(JSON, comment="原始表格行 JSON")
    search_text = Column(Text, comment="检索拼接文本")
    created_at = Column(TIMESTAMP, server_default=func.now(), comment="创建时间")
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now(), comment="更新时间")

    __table_args__ = (
        UniqueConstraint("source_knowledge_id", "row_no", name="uk_param_pin_row_source_row"),
        Index("idx_param_pin_row_source", "source_knowledge_id"),
        Index("idx_param_pin_row_ecu_pin", "ecu_name_normalized", "ecu_pin_no_normalized"),
        Index("idx_param_pin_row_ecu_component", "ecu_name_normalized", "component_name_normalized"),
        Index("idx_param_pin_row_ecu_definition", "ecu_name_normalized", "pin_definition_normalized"),
        Index("idx_param_pin_row_voltage", "system_voltage"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_unicode_ci",
        },
    )


class ParamAlias(Base):
    """参数别名映射表"""

    __tablename__ = "param_alias"

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="本地主键ID")
    entity_type = Column(String(32), nullable=False, comment="实体类型")
    canonical_value = Column(String(200), nullable=False, comment="标准值原文")
    canonical_value_normalized = Column(String(200), nullable=False, comment="标准值归一化结果")
    alias_value = Column(String(200), nullable=False, comment="别名原文")
    alias_value_normalized = Column(String(200), nullable=False, comment="别名归一化结果")
    source_scope = Column(String(32), nullable=False, default="system", comment="别名来源")
    source_knowledge_id = Column(BigInteger, comment="来源知识ID")
    priority = Column(Integer, nullable=False, default=100, comment="匹配优先级")
    is_active = Column(Boolean, nullable=False, default=True, comment="是否有效")
    remark = Column(String(500), comment="备注")
    created_at = Column(TIMESTAMP, server_default=func.now(), comment="创建时间")
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now(), comment="更新时间")

    __table_args__ = (
        UniqueConstraint(
            "entity_type",
            "canonical_value_normalized",
            "alias_value_normalized",
            name="uk_param_alias_unique",
        ),
        Index("idx_param_alias_lookup", "entity_type", "alias_value_normalized", "is_active"),
        Index("idx_param_alias_canonical", "entity_type", "canonical_value_normalized"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_unicode_ci",
        },
    )


class ParamSyncJob(Base):
    """参数同步作业日志表"""

    __tablename__ = "param_sync_job"

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="本地主键ID")
    job_type = Column(String(32), nullable=False, comment="任务类型")
    sync_scope = Column(String(64), nullable=False, comment="同步范围")
    parser_version = Column(String(32), nullable=False, comment="解析器版本")
    status = Column(String(20), nullable=False, comment="任务状态")
    total_source_count = Column(Integer, nullable=False, default=0, comment="源记录总数")
    new_source_count = Column(Integer, nullable=False, default=0, comment="新增源记录数")
    updated_source_count = Column(Integer, nullable=False, default=0, comment="更新源记录数")
    disabled_source_count = Column(Integer, nullable=False, default=0, comment="失效源记录数")
    failed_source_count = Column(Integer, nullable=False, default=0, comment="失败源记录数")
    details_json = Column(JSON, comment="任务明细")
    error_message = Column(Text, comment="错误摘要")
    started_at = Column(TIMESTAMP, nullable=False, comment="任务开始时间")
    finished_at = Column(TIMESTAMP, comment="任务结束时间")
    created_at = Column(TIMESTAMP, server_default=func.now(), comment="创建时间")

    __table_args__ = (
        Index("idx_param_sync_job_type_status", "job_type", "status"),
        Index("idx_param_sync_job_started_at", "started_at"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_unicode_ci",
        },
    )


class OssImageDeleteJob(Base):
    """用户端 OSS 图片异步删除任务。"""

    __tablename__ = "oss_image_delete_jobs"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True, comment="任务ID")
    object_key = Column(String(512), nullable=False, unique=True, comment="OSS对象key")
    session_id = Column(String(128), comment="前端会话ID")
    user_id = Column(BigInteger, comment="用户ID")
    reason = Column(String(50), nullable=False, default="new_search", comment="删除原因")
    status = Column(String(20), nullable=False, default="pending", comment="pending/running/confirmed/failed")
    delete_token_hash = Column(String(64), comment="删除凭证SHA-256")
    attempt_count = Column(Integer, nullable=False, default=0, comment="已尝试次数")
    next_retry_at = Column(TIMESTAMP, server_default=func.now(), comment="下次重试时间")
    last_error = Column(Text, comment="最近一次错误")
    confirmed_at = Column(TIMESTAMP, comment="确认删除时间")
    created_at = Column(TIMESTAMP, server_default=func.now(), comment="创建时间")
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now(), comment="更新时间")

    __table_args__ = (
        Index("idx_oss_image_delete_jobs_status_retry", "status", "next_retry_at"),
        Index("idx_oss_image_delete_jobs_session_id", "session_id"),
        Index("idx_oss_image_delete_jobs_user_id", "user_id"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_unicode_ci",
        },
    )


class DimFacet(Base):
    """维度定义表 - 定义系统中的澄清维度（品牌、系列、ECU等）"""

    __tablename__ = "dim_facets"

    facet_key = Column(String(50), primary_key=True, comment="维度标识：brand, series, ecu...")
    facet_name = Column(String(50), nullable=False, comment="显示名称：品牌, 系列...")
    question = Column(String(200), comment="澄清问题模板：请选择品牌：")
    priority = Column(Integer, default=0, comment="澄清优先级（小=优先）")
    db_field = Column(String(50), comment="docs表中对应字段名：brand, series, ecus...")
    parent_facet_key = Column(String(50), comment="父维度key（series→brand）")
    match_mode = Column(String(20), default="dict", comment="匹配模式：dict=字典匹配, regex=正则匹配")
    specificity = Column(Integer, default=0, comment="具体度（大=更具体，用于冲突推荐）")
    is_active = Column(Boolean, default=True, comment="是否启用")
    created_at = Column(TIMESTAMP, server_default=func.now(), comment="创建时间")
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now(), comment="更新时间")

    __table_args__ = ({"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},)


class DimValue(Base):
    """维度值表 - 存储每个维度的可选值及其匹配模式"""

    __tablename__ = "dim_values"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="主键ID")
    facet_key = Column(String(50), nullable=False, comment="所属维度：brand, series...")
    value = Column(String(100), nullable=False, comment="主值（用于显示和过滤）：东风, 天锦...")
    match_patterns = Column(String(500), comment="匹配模式，逗号分隔：东风,dongfeng,df,DFAC")
    parent_value_id = Column(Integer, comment="父值ID（天锦→东风的id）")
    is_active = Column(Boolean, default=True, comment="是否启用")
    sort_order = Column(Integer, default=0, comment="排序权重（大=优先）")
    created_at = Column(TIMESTAMP, server_default=func.now(), comment="创建时间")
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now(), comment="更新时间")

    __table_args__ = (
        Index("idx_dim_facet_key", "facet_key"),
        Index("idx_dim_parent_value_id", "parent_value_id"),
        UniqueConstraint("facet_key", "value", name="uk_dim_facet_value"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )


@lru_cache(maxsize=1)
def get_engine():
    """创建数据库引擎"""

    return create_engine(
        settings.mysql_url,
        echo=False,
        pool_pre_ping=True,
        pool_recycle=3600,
        max_overflow=10,
        pool_size=5,
    )


@lru_cache(maxsize=1)
def get_session_local():
    """创建会话工厂"""

    return sessionmaker(autocommit=False, autoflush=False, bind=get_engine(), class_=Session)


def get_db() -> Generator[Session, None, None]:
    """获取数据库会话（依赖注入用）"""

    session_local = get_session_local()
    db = session_local()
    try:
        yield db
    finally:
        db.close()

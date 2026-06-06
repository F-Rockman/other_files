from __future__ import annotations
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Union
class ErrorLevel(str, Enum):
    """
    错误等级。
    """
    # 信息提示，例如空结果、降级提示
    INFO = "info"
    # 警告，一般是用户输入、业务规则、语义理解问题
    WARNING = "warning"
    # 错误，一般是系统、服务、执行链路异常
    ERROR = "error"
class ErrorStage(str, Enum):
    """
    异常发生阶段。
    """
    # 系统基础服务阶段
    SYSTEM = "system"
    # 意图识别阶段
    INTENT = "intent"
    # 值检索 / 实体解析阶段
    VALUE_RETRIEVAL = "value_retrieval"
    # SQL 生成阶段
    SQL_GENERATION = "sql_generation"
    # 查询执行阶段
    QUERY_EXECUTION = "query_execution"
    # 结果处理阶段
    RESULT_PROCESSING = "result_processing"
    # 结果解释阶段
    RESULT_EXPLANATION = "result_explanation"
    # 图表推荐阶段
    CHART_RECOMMEND = "chart_recommend"
    # 上下文理解阶段
    CONTEXT = "context"
    # 推荐问题模块异常
    RECOMMENDATION = "recommendation"
@dataclass(frozen=True)
class ErrorInfo:
    """
    对外返回的统一错误结构。
    """
    key: str
    level: str
    stage: str
    message: str
    def to_dict(self) -> Dict[str, Any]:
        """将共享错误信息转换为可序列化字典。"""
        return asdict(self)
ErrorCodeLike = Union["ErrorCode", str]
class ErrorCode(str, Enum):
    """
    查询系统统一错误码。
    设计特点：
        1. 枚举名是静态变量，业务代码可直接引用；
        2. 枚举值是稳定 key，便于日志、监控、前端识别；
        3. level / stage / message 与 key 绑定在一起；
        4. 不再需要单独维护 error_definitions.py；
        5. 不包含 recommendQuestions，不耦合推荐模块。
    """
    def __new__(
        cls,
        key: str,
        level: ErrorLevel,
        stage: ErrorStage,
        message: str,
    ):
        """创建绑定稳定 key、等级、阶段和默认文案的错误码成员。"""
        obj = str.__new__(cls, key)
        obj._value_ = key
        obj.level = level.value
        obj.stage = stage.value
        obj.message = message
        return obj
    # ============================================================
    # 1. 系统异常
    # ============================================================
    # 模型服务不可用，例如模型服务宕机、接口不可达
    SYSTEM_MODEL_SERVICE_UNAVAILABLE = (
        "system_model_service_unavailable",
        ErrorLevel.ERROR,
        ErrorStage.SYSTEM,
        "智能服务暂时不可用，本次查询未完成，请稍后再试。",
    )
    # 模型服务调用超时
    SYSTEM_MODEL_SERVICE_TIMEOUT = (
        "system_model_service_timeout",
        ErrorLevel.ERROR,
        ErrorStage.SYSTEM,
        "智能理解耗时较长，建议简化问题后重新查询。",
    )
    # 模型返回内容格式不符合系统要求
    SYSTEM_MODEL_RESPONSE_FORMAT_INVALID = (
        "system_model_response_format_invalid",
        ErrorLevel.ERROR,
        ErrorStage.SYSTEM,
        "智能服务返回结果异常，请重新提问，系统会重新分析。",
    )
    # 配置服务不可用
    SYSTEM_CONFIG_SERVICE_UNAVAILABLE = (
        "system_config_service_unavailable",
        ErrorLevel.ERROR,
        ErrorStage.SYSTEM,
        "系统配置读取异常，暂时无法完成本次查询。",
    )
    # 数据库连接失败
    SYSTEM_DATABASE_CONNECTION_FAILED = (
        "system_database_connection_failed",
        ErrorLevel.ERROR,
        ErrorStage.SYSTEM,
        "业务数据源连接失败，请稍后重试或联系管理员。",
    )
    # 网络异常
    SYSTEM_NETWORK_UNAVAILABLE = (
        "system_network_unavailable",
        ErrorLevel.ERROR,
        ErrorStage.SYSTEM,
        "当前网络连接异常，请稍后重新发起查询。",
    )
    # 并发阻塞异常
    SYSTEM_CONCURRENCY_BLOCKED = (
        "system_concurrency_blocked",
        ErrorLevel.ERROR,
        ErrorStage.SYSTEM,
        "当前查询请求较多，系统繁忙，请稍后再试。",
    )
    # 依赖服务不可用
    SYSTEM_DEPENDENCY_SERVICE_UNAVAILABLE = (
        "system_dependency_service_unavailable",
        ErrorLevel.ERROR,
        ErrorStage.SYSTEM,
        "查询依赖的基础服务异常，暂时无法继续处理。",
    )
    # 系统资源不足
    SYSTEM_RESOURCE_INSUFFICIENT = (
        "system_resource_insufficient",
        ErrorLevel.ERROR,
        ErrorStage.SYSTEM,
        "当前系统资源紧张，建议稍后或缩小范围后重试。",
    )
    # 请求被限流
    SYSTEM_REQUEST_RATE_LIMITED = (
        "system_request_rate_limited",
        ErrorLevel.WARNING,
        ErrorStage.SYSTEM,
        "当前请求过于频繁，系统已保护性限流，请稍后再试。",
    )
    # 权限校验失败
    SYSTEM_AUTH_PERMISSION_CHECK_FAILED = (
        "system_auth_permission_check_failed",
        ErrorLevel.ERROR,
        ErrorStage.SYSTEM,
        "权限校验未通过，请确认账号或数据访问权限。",
    )
    # 未知系统异常
    SYSTEM_UNKNOWN_ERROR = (
        "system_unknown_error",
        ErrorLevel.ERROR,
        ErrorStage.SYSTEM,
        "系统遇到未知异常，本次查询未完成，请稍后重试。",
    )
    # ============================================================
    # 2. 意图识别 - 拒答类
    # ============================================================
    # 非数据查询类意图
    INTENT_REJECT_NON_QUERY_INTENT = (
        "intent_reject_non_query_intent",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "当前问题不是数据查询类问题，请换成明确的业务查询。",
    )
    # 超出系统支持范围
    INTENT_REJECT_OUT_OF_SCOPE_QUERY = (
        "intent_reject_out_of_scope_query",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "当前问题超出系统支持范围，建议围绕设备、告警、指标等内容提问。",
    )
    # 多意图查询
    INTENT_REJECT_MULTI_INTENT_QUERY = (
        "intent_reject_multi_intent_query",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "当前问题包含多个查询意图，请拆成多个问题分别查询。",
    )
    # 不支持的分析方式
    INTENT_REJECT_UNSUPPORTED_ANALYSIS_METHOD = (
        "intent_reject_unsupported_analysis_method",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "当前分析方式暂不支持，可尝试明细、统计、趋势或 TopN 查询。",
    )
    # 安全风险拦截
    INTENT_REJECT_SECURITY_RISK = (
        "intent_reject_security_risk",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "当前问题存在安全风险，系统无法继续处理，请调整后再试。",
    )
    # 非业务数据查询
    INTENT_REJECT_NON_BUSINESS_DATA_QUERY = (
        "intent_reject_non_business_data_query",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "当前问题不属于业务数据查询范围，请换成具体业务对象提问。",
    )
    # 闲聊或解释类问题
    INTENT_REJECT_CHAT_OR_EXPLANATION_QUERY = (
        "intent_reject_chat_or_explanation_query",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "当前系统主要支持业务问数，请输入需要查询的数据问题。",
    )
    # 不支持内容生成任务
    INTENT_REJECT_GENERATION_TASK_UNSUPPORTED = (
        "intent_reject_generation_task_unsupported",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "当前不支持内容生成类任务，请改为明确的数据查询问题。",
    )
    # 不支持操作类指令
    INTENT_REJECT_OPERATION_COMMAND_UNSUPPORTED = (
        "intent_reject_operation_command_unsupported",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "当前系统仅支持查询分析，不支持修改、删除或下发操作。",
    )
    # 查询范围过大
    INTENT_REJECT_QUERY_SCOPE_TOO_LARGE = (
        "intent_reject_query_scope_too_large",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "当前查询范围较大，建议增加时间、对象或区域等过滤条件。",
    )
    # ============================================================
    # 3. 意图识别 - 引导类
    # ============================================================
    # 跨域查询
    INTENT_GUIDE_CROSS_DOMAIN_QUERY = (
        "intent_guide_cross_domain_query",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "当前问题涉及多个业务域，建议先选择一个业务域查询。",
    )
    # 设备不存在
    INTENT_GUIDE_DEVICE_NOT_FOUND = (
        "intent_guide_device_not_found",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "未找到匹配设备，请确认设备名称、IP 或设备编码是否正确。",
    )
    # 多设备类型不一致
    INTENT_GUIDE_DEVICE_TYPE_INCONSISTENT = (
        "intent_guide_device_type_inconsistent",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "当前涉及多个设备类型，建议明确要查询的具体设备类型。",
    )
    # 不支持子网关联指标
    INTENT_GUIDE_UNSUPPORTED_SUBNET_METRIC_QUERY = (
        "intent_guide_unsupported_subnet_metric_query",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "暂不支持子网直接关联指标，建议先查询子网下设备。",
    )
    # 不支持子网关联告警
    INTENT_GUIDE_UNSUPPORTED_SUBNET_ALARM_QUERY = (
        "intent_guide_unsupported_subnet_alarm_query",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "暂不支持子网直接关联告警，建议先定位设备范围。",
    )
    # 表检索失败
    INTENT_GUIDE_TABLE_RETRIEVAL_FAILED = (
        "intent_guide_table_retrieval_failed",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "未找到合适的数据表承接查询，请使用更明确的业务对象。",
    )
    # 字段检索失败
    INTENT_GUIDE_FIELD_RETRIEVAL_FAILED = (
        "intent_guide_field_retrieval_failed",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "未找到匹配字段，请换用更标准的属性或指标名称。",
    )
    # 指标不存在
    INTENT_GUIDE_METRIC_NOT_FOUND = (
        "intent_guide_metric_not_found",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "未找到对应指标，请确认指标名称或换用标准指标表达。",
    )
    # 枚举值不存在
    INTENT_GUIDE_ENUM_VALUE_NOT_FOUND = (
        "intent_guide_enum_value_not_found",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "当前取值未匹配到标准枚举，请确认状态、类型或级别。",
    )
    # 关联关系不存在
    INTENT_GUIDE_RELATION_NOT_FOUND = (
        "intent_guide_relation_not_found",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "当前对象之间缺少可用关联关系，建议拆分查询。",
    )
    # 业务规则不支持
    INTENT_GUIDE_BUSINESS_RULE_UNSUPPORTED = (
        "intent_guide_business_rule_unsupported",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "当前业务规则暂未开放，建议换成已支持的查询方式。",
    )
    # 查询对象超出业务范围
    INTENT_GUIDE_OBJECT_OUT_OF_BUSINESS_SCOPE = (
        "intent_guide_object_out_of_business_scope",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "当前对象不在已支持范围内，请选择已治理的业务对象。",
    )
    # ============================================================
    # 4. 意图识别 - 追问类
    # ============================================================
    # 查询对象缺失
    INTENT_CLARIFY_QUERY_OBJECT_MISSING = (
        "intent_clarify_query_object_missing",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "还不确定你想查什么对象，请明确是设备、告警、接口还是指标。",
    )
    # 查询意图模糊
    INTENT_CLARIFY_QUERY_INTENT_AMBIGUOUS = (
        "intent_clarify_query_intent_ambiguous",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "还无法判断你想查明细、总数、趋势还是聚合统计。",
    )
    # 指标缺失
    INTENT_CLARIFY_METRIC_MISSING = (
        "intent_clarify_metric_missing",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "当前问题缺少查询指标，请补充 CPU、内存、流量等指标。",
    )
    # 时间范围缺失
    INTENT_CLARIFY_TIME_RANGE_MISSING = (
        "intent_clarify_time_range_missing",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "当前查询需要时间范围，请补充今天、最近一周或指定时间段。",
    )
    # 过滤条件不完整
    INTENT_CLARIFY_FILTER_CONDITION_INCOMPLETE = (
        "intent_clarify_filter_condition_incomplete",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "当前过滤条件不完整，请补充具体状态、阈值或取值。",
    )
    # 查询对象存在歧义
    INTENT_CLARIFY_OBJECT_AMBIGUOUS = (
        "intent_clarify_object_ambiguous",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "当前对象名称可能对应多个对象，请进一步说明查询对象。",
    )
    # 查询指标存在歧义
    INTENT_CLARIFY_METRIC_AMBIGUOUS = (
        "intent_clarify_metric_ambiguous",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "当前指标存在多个含义，请确认要查询的具体指标。",
    )
    # 排序或 TopN 信息缺失
    INTENT_CLARIFY_TOPN_SORT_MISSING = (
        "intent_clarify_topn_sort_missing",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "当前问题涉及排名，请补充排序指标和需要的数量。",
    )
    # 指标含义不清楚
    INTENT_CLARIFY_METRIC_MEANING_UNCLEAR = (
        "intent_clarify_metric_meaning_unclear",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "当前指标表达不够清晰，请换用更具体的指标名称。",
    )
    # 属性含义不清楚
    INTENT_CLARIFY_ATTRIBUTE_MEANING_UNCLEAR = (
        "intent_clarify_attribute_meaning_unclear",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "当前属性表达不够明确，请补充你想查看的具体字段。",
    )
    # 设备标识不完整
    INTENT_CLARIFY_DEVICE_IDENTIFIER_INCOMPLETE = (
        "intent_clarify_device_identifier_incomplete",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "当前设备信息不足，请补充设备名称、IP 或设备编码。",
    )
    # 时间表达不明确
    INTENT_CLARIFY_TIME_EXPRESSION_AMBIGUOUS = (
        "intent_clarify_time_expression_ambiguous",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "当前时间表达不明确，请补充更清晰的时间范围。",
    )
    # 聚合方式缺失
    INTENT_CLARIFY_AGGREGATION_MISSING = (
        "intent_clarify_aggregation_missing",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "当前问题像是统计查询，请补充平均、最大、最小或总数。",
    )
    # 分组维度缺失
    INTENT_CLARIFY_GROUP_BY_MISSING = (
        "intent_clarify_group_by_missing",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "当前需要分组统计，请补充按区域、类型、级别或时间分组。",
    )
    # 单位不明确
    INTENT_CLARIFY_UNIT_AMBIGUOUS = (
        "intent_clarify_unit_ambiguous",
        ErrorLevel.WARNING,
        ErrorStage.INTENT,
        "当前数值单位不明确，请补充百分比、GB、Mbps 或毫秒等单位。",
    )
    # ============================================================
    # 5. 值检索 / 实体解析
    # ============================================================
    VALUE_RETRIEVAL_IP_NOT_FOUND = (
        "value_retrieval_ip_not_found",
        ErrorLevel.WARNING,
        ErrorStage.VALUE_RETRIEVAL,
        "未找到该 IP 对应的业务对象，请确认 IP 是否正确。",
    )
    VALUE_RETRIEVAL_NAME_NOT_FOUND = (
        "value_retrieval_name_not_found",
        ErrorLevel.WARNING,
        ErrorStage.VALUE_RETRIEVAL,
        "未找到该名称对应的对象，请尝试提供完整名称或 IP。",
    )
    VALUE_RETRIEVAL_NAME_MULTIPLE_CANDIDATES = (
        "value_retrieval_name_multiple_candidates",
        ErrorLevel.WARNING,
        ErrorStage.VALUE_RETRIEVAL,
        "当前名称匹配到多个对象，请确认要查询的具体对象。",
    )
    VALUE_RETRIEVAL_IP_MULTIPLE_CANDIDATES = (
        "value_retrieval_ip_multiple_candidates",
        ErrorLevel.WARNING,
        ErrorStage.VALUE_RETRIEVAL,
        "当前 IP 匹配到多个对象，请补充设备类型或业务域。",
    )
    VALUE_RETRIEVAL_KPI_NOT_FOUND = (
        "value_retrieval_kpi_not_found",
        ErrorLevel.WARNING,
        ErrorStage.VALUE_RETRIEVAL,
        "未找到匹配指标，请确认 KPI 名称或换用标准指标。",
    )
    VALUE_RETRIEVAL_KPI_MULTIPLE_CANDIDATES = (
        "value_retrieval_kpi_multiple_candidates",
        ErrorLevel.WARNING,
        ErrorStage.VALUE_RETRIEVAL,
        "当前 KPI 匹配到多个候选，请确认具体指标含义。",
    )
    VALUE_RETRIEVAL_ENUM_NOT_FOUND = (
        "value_retrieval_enum_not_found",
        ErrorLevel.WARNING,
        ErrorStage.VALUE_RETRIEVAL,
        "当前取值未匹配到标准枚举，请确认业务取值。",
    )
    VALUE_RETRIEVAL_ALIAS_NORMALIZATION_FAILED = (
        "value_retrieval_alias_normalization_failed",
        ErrorLevel.WARNING,
        ErrorStage.VALUE_RETRIEVAL,
        "当前表达无法归一为标准名称，请换用更规范的说法。",
    )
    VALUE_RETRIEVAL_VALUE_SEMANTIC_AMBIGUOUS = (
        "value_retrieval_value_semantic_ambiguous",
        ErrorLevel.WARNING,
        ErrorStage.VALUE_RETRIEVAL,
        "当前关键词含义不唯一，请说明它是对象、指标还是属性。",
    )
    VALUE_RETRIEVAL_MASTER_DATA_MISSING = (
        "value_retrieval_master_data_missing",
        ErrorLevel.WARNING,
        ErrorStage.VALUE_RETRIEVAL,
        "当前依赖的主数据不完整，系统无法准确定位对象。",
    )
    # ============================================================
    # 6. SQL 生成阶段
    # ============================================================
    SQL_GENERATION_FAILED = (
        "sql_generation_failed",
        ErrorLevel.ERROR,
        ErrorStage.SQL_GENERATION,
        "查询语句生成失败，请换一种更明确的问法重试。",
    )
    SQL_GENERATION_TIMEOUT = (
        "sql_generation_timeout",
        ErrorLevel.ERROR,
        ErrorStage.SQL_GENERATION,
        "查询语句生成耗时过长，建议简化条件后重试。",
    )
    SQL_GENERATION_PYTHON_EXECUTION_FAILED = (
        "sql_generation_python_execution_failed",
        ErrorLevel.ERROR,
        ErrorStage.SQL_GENERATION,
        "查询生成过程中的辅助计算失败，请稍后重新查询。",
    )
    SQL_GENERATION_SQL_FORMAT_INVALID = (
        "sql_generation_sql_format_invalid",
        ErrorLevel.ERROR,
        ErrorStage.SQL_GENERATION,
        "生成的查询语句格式异常，系统无法继续执行。",
    )
    SQL_GENERATION_SQL_SAFETY_CHECK_FAILED = (
        "sql_generation_sql_safety_check_failed",
        ErrorLevel.WARNING,
        ErrorStage.SQL_GENERATION,
        "查询语句未通过安全校验，系统已停止执行。",
    )
    SQL_GENERATION_SCHEMA_MAPPING_FAILED = (
        "sql_generation_schema_mapping_failed",
        ErrorLevel.WARNING,
        ErrorStage.SQL_GENERATION,
        "问题无法稳定映射到表字段，请明确对象或指标。",
    )
    SQL_GENERATION_JOIN_PATH_FAILED = (
        "sql_generation_join_path_failed",
        ErrorLevel.WARNING,
        ErrorStage.SQL_GENERATION,
        "对象之间关联路径不清晰，建议拆分或明确主对象。",
    )
    SQL_GENERATION_DEFAULT_FIELDS_COMPLETION_FAILED = (
        "sql_generation_default_fields_completion_failed",
        ErrorLevel.WARNING,
        ErrorStage.SQL_GENERATION,
        "当前对象缺少默认展示字段，暂时无法生成完整查询。",
    )
    SQL_GENERATION_DIALECT_ADAPTATION_FAILED = (
        "sql_generation_dialect_adaptation_failed",
        ErrorLevel.ERROR,
        ErrorStage.SQL_GENERATION,
        "查询语法无法适配目标数据源，请稍后重试。",
    )
    SQL_GENERATION_UNSUPPORTED_SQL_FEATURE = (
        "sql_generation_unsupported_sql_feature",
        ErrorLevel.WARNING,
        ErrorStage.SQL_GENERATION,
        "当前查询需要的 SQL 能力暂不支持，请简化问题。",
    )
    # ============================================================
    # 7. 查询执行阶段
    # ============================================================
    QUERY_EXECUTION_FAILED = (
        "query_execution_failed",
        ErrorLevel.ERROR,
        ErrorStage.QUERY_EXECUTION,
        "查询执行失败，请稍后重试或调整查询条件。",
    )
    QUERY_EXECUTION_TIMEOUT = (
        "query_execution_timeout",
        ErrorLevel.ERROR,
        ErrorStage.QUERY_EXECUTION,
        "查询执行超时，建议缩小时间范围或增加过滤条件。",
    )
    QUERY_EXECUTION_DATASOURCE_UNAVAILABLE = (
        "query_execution_datasource_unavailable",
        ErrorLevel.ERROR,
        ErrorStage.QUERY_EXECUTION,
        "当前数据源暂时不可用，请稍后重新查询。",
    )
    QUERY_EXECUTION_ENGINE_ERROR = (
        "query_execution_engine_error",
        ErrorLevel.ERROR,
        ErrorStage.QUERY_EXECUTION,
        "查询引擎返回异常，本次查询无法完成。",
    )
    QUERY_RESULT_EMPTY = (
        "query_result_empty",
        ErrorLevel.INFO,
        ErrorStage.QUERY_EXECUTION,
        "未查询到符合条件的数据，建议放宽条件后再试。",
    )
    QUERY_RESULT_TOO_LARGE = (
        "query_result_too_large",
        ErrorLevel.WARNING,
        ErrorStage.QUERY_EXECUTION,
        "查询结果较多，建议增加时间、区域或对象过滤。",
    )
    QUERY_EXECUTION_FIELD_NOT_FOUND = (
        "query_execution_field_not_found",
        ErrorLevel.ERROR,
        ErrorStage.QUERY_EXECUTION,
        "查询依赖字段不存在或已变更，请检查字段配置。",
    )
    QUERY_EXECUTION_TABLE_NOT_FOUND = (
        "query_execution_table_not_found",
        ErrorLevel.ERROR,
        ErrorStage.QUERY_EXECUTION,
        "查询依赖表不存在或已变更，请检查数据表配置。",
    )
    QUERY_EXECUTION_DATA_PERMISSION_DENIED = (
        "query_execution_data_permission_denied",
        ErrorLevel.WARNING,
        ErrorStage.QUERY_EXECUTION,
        "当前账号暂无该数据访问权限，请调整范围或申请权限。",
    )
    QUERY_EXECUTION_PARTITION_FILTER_MISSING = (
        "query_execution_partition_filter_missing",
        ErrorLevel.WARNING,
        ErrorStage.QUERY_EXECUTION,
        "当前查询缺少必要时间条件，请补充时间范围。",
    )
    QUERY_EXECUTION_DATA_QUALITY_ISSUE = (
        "query_execution_data_quality_issue",
        ErrorLevel.WARNING,
        ErrorStage.QUERY_EXECUTION,
        "查询结果存在数据质量异常，请谨慎参考结果。",
    )
    # ============================================================
    # 8. 结果处理 / 结果解释
    # ============================================================
    RESULT_PROCESSING_PARSE_FAILED = (
        "result_processing_parse_failed",
        ErrorLevel.ERROR,
        ErrorStage.RESULT_PROCESSING,
        "查询已完成，但结果解析失败，暂时无法展示。",
    )
    RESULT_PROCESSING_REQUIRED_FIELD_MISSING = (
        "result_processing_required_field_missing",
        ErrorLevel.WARNING,
        ErrorStage.RESULT_PROCESSING,
        "查询结果缺少必要字段，无法完整展示结果。",
    )
    RESULT_PROCESSING_FORMAT_UNSUPPORTED = (
        "result_processing_format_unsupported",
        ErrorLevel.WARNING,
        ErrorStage.RESULT_PROCESSING,
        "当前结果结构暂不支持展示，建议调整查询方式。",
    )
    RESULT_PROCESSING_UNIT_CONVERSION_FAILED = (
        "result_processing_unit_conversion_failed",
        ErrorLevel.WARNING,
        ErrorStage.RESULT_PROCESSING,
        "指标单位换算失败，系统已尽量保留原始结果。",
    )
    RESULT_PROCESSING_ENUM_MAPPING_FAILED = (
        "result_processing_enum_mapping_failed",
        ErrorLevel.WARNING,
        ErrorStage.RESULT_PROCESSING,
        "部分枚举值未找到业务含义，结果解释可能不完整。",
    )
    RESULT_PROCESSING_DESENSITIZATION_FAILED = (
        "result_processing_desensitization_failed",
        ErrorLevel.WARNING,
        ErrorStage.RESULT_PROCESSING,
        "当前结果未完成安全脱敏，系统暂不展示敏感数据。",
    )
    RESULT_EXPLANATION_GENERATION_FAILED = (
        "result_explanation_generation_failed",
        ErrorLevel.WARNING,
        ErrorStage.RESULT_EXPLANATION,
        "查询结果已返回，但智能解读暂时生成失败。",
    )
    RESULT_EXPLANATION_LOW_CONFIDENCE = (
        "result_explanation_low_confidence",
        ErrorLevel.WARNING,
        ErrorStage.RESULT_EXPLANATION,
        "当前解读置信度较低，建议结合原始数据判断。",
    )
    # ============================================================
    # 9. 图表推荐
    # ============================================================
    CHART_RECOMMEND_NO_MATCHED_INTENT = (
        "chart_recommend_no_matched_intent",
        ErrorLevel.INFO,
        ErrorStage.CHART_RECOMMEND,
        "暂未匹配到合适图表，系统将优先展示表格结果。",
    )
    CHART_RECOMMEND_DATA_SHAPE_UNSUPPORTED = (
        "chart_recommend_data_shape_unsupported",
        ErrorLevel.INFO,
        ErrorStage.CHART_RECOMMEND,
        "当前数据结构不适合绘图，建议查看表格结果。",
    )
    CHART_RECOMMEND_DIMENSION_MISSING = (
        "chart_recommend_dimension_missing",
        ErrorLevel.WARNING,
        ErrorStage.CHART_RECOMMEND,
        "当前结果缺少维度字段，暂时无法生成图表。",
    )
    CHART_RECOMMEND_MEASURE_MISSING = (
        "chart_recommend_measure_missing",
        ErrorLevel.WARNING,
        ErrorStage.CHART_RECOMMEND,
        "当前结果缺少数值指标，暂时无法生成图表。",
    )
    CHART_RECOMMEND_TIME_FIELD_MISSING = (
        "chart_recommend_time_field_missing",
        ErrorLevel.WARNING,
        ErrorStage.CHART_RECOMMEND,
        "当前结果缺少时间字段，无法生成趋势图。",
    )
    CHART_RECOMMEND_CHART_TYPE_UNSUPPORTED = (
        "chart_recommend_chart_type_unsupported",
        ErrorLevel.WARNING,
        ErrorStage.CHART_RECOMMEND,
        "当前图表类型暂不支持，系统将尝试降级展示。",
    )
    CHART_RECOMMEND_CONFIG_GENERATION_FAILED = (
        "chart_recommend_config_generation_failed",
        ErrorLevel.WARNING,
        ErrorStage.CHART_RECOMMEND,
        "图表配置生成失败，系统将优先返回数据结果。",
    )
    CHART_RENDER_FAILED = (
        "chart_render_failed",
        ErrorLevel.WARNING,
        ErrorStage.CHART_RECOMMEND,
        "图表渲染失败，你可以先查看表格数据。",
    )
    # ============================================================
    # 10. 上下文 / 多轮对话
    # ============================================================
    CONTEXT_MISSING = (
        "context_missing",
        ErrorLevel.WARNING,
        ErrorStage.CONTEXT,
        "当前问题依赖上文，但未找到可用上下文，请补充完整问题。",
    )
    CONTEXT_REFERENCE_AMBIGUOUS = (
        "context_reference_ambiguous",
        ErrorLevel.WARNING,
        ErrorStage.CONTEXT,
        "当前指代不够明确，请说明“它”或“这个”具体指什么。",
    )
    CONTEXT_CONFLICT = (
        "context_conflict",
        ErrorLevel.WARNING,
        ErrorStage.CONTEXT,
        "当前问题与上文信息存在冲突，请确认是否延续上次查询。",
    )
    CONTEXT_EXPIRED = (
        "context_expired",
        ErrorLevel.WARNING,
        ErrorStage.CONTEXT,
        "上次查询上下文已失效，请重新提供查询条件。",
    )
    CONTEXT_HISTORY_TOO_LONG = (
        "context_history_too_long",
        ErrorLevel.WARNING,
        ErrorStage.CONTEXT,
        "当前对话历史较长，建议重新描述完整查询问题。",
    )
    # ============================================================
    # 11. 推荐问题模块异常
    # ============================================================
    RECOMMENDATION_GENERATION_FAILED = (
        "recommendation_generation_failed",
        ErrorLevel.WARNING,
        ErrorStage.RECOMMENDATION,
        "推荐问题生成失败，你可以直接输入新的查询问题。",
    )
    RECOMMENDATION_NO_MATCHED_QUESTION = (
        "recommendation_no_matched_question",
        ErrorLevel.INFO,
        ErrorStage.RECOMMENDATION,
        "暂未找到合适推荐问题，建议补充对象、指标或时间。",
    )
    RECOMMENDATION_LOW_CONFIDENCE = (
        "recommendation_low_confidence",
        ErrorLevel.INFO,
        ErrorStage.RECOMMENDATION,
        "推荐问题匹配度不高，建议进一步明确查询条件。",
    )
    RECOMMENDATION_PARAMETER_MISSING = (
        "recommendation_parameter_missing",
        ErrorLevel.WARNING,
        ErrorStage.RECOMMENDATION,
        "推荐问题缺少必要参数，请补充设备、指标或时间范围。",
    )
    @property
    def key(self) -> str:
        """返回供跨模块分类和监控使用的稳定错误 key。"""
        return self.value
    def to_info(self, *, message: Optional[str] = None) -> ErrorInfo:
        """
        转换为 ErrorInfo。
        """
        return ErrorInfo(
            key=self.key,
            level=self.level,
            stage=self.stage,
            message=message or self.message,
        )
    def to_dict(self, *, message: Optional[str] = None) -> Dict[str, Any]:
        """
        转换为 dict。
        """
        return self.to_info(message=message).to_dict()
    @classmethod
    def get_error(
        cls,
        key: ErrorCodeLike,
        *,
        message: Optional[str] = None,
    ) -> ErrorInfo:
        """
        根据 ErrorCode 或字符串 key 获取错误信息。
        """
        error_code = cls.resolve(key)
        return error_code.to_info(message=message)
    @classmethod
    def get_error_dict(
        cls,
        key: ErrorCodeLike,
        *,
        message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        根据 ErrorCode 或字符串 key 获取 dict 错误信息。
        """
        return cls.get_error(key, message=message).to_dict()
    @classmethod
    def resolve(cls, key: ErrorCodeLike) -> "ErrorCode":
        """
        将 ErrorCode 或字符串 key 转换为 ErrorCode。
        如果 key 不存在，兜底返回 SYSTEM_UNKNOWN_ERROR。
        """
        if isinstance(key, cls):
            return key
        return cls._value2member_map_.get(str(key), cls.SYSTEM_UNKNOWN_ERROR)
    @classmethod
    def exists(cls, key: ErrorCodeLike) -> bool:
        """
        判断错误码是否存在。
        """
        if isinstance(key, cls):
            return True
        return str(key) in cls._value2member_map_
    @classmethod
    def list_by_stage(cls, stage: Union[ErrorStage, str]) -> List["ErrorCode"]:
        """
        按阶段查询错误码。
        """
        stage_value = stage.value if isinstance(stage, ErrorStage) else str(stage)
        return [item for item in cls if item.stage == stage_value]
    @classmethod
    def list_by_level(cls, level: Union[ErrorLevel, str]) -> List["ErrorCode"]:
        """
        按等级查询错误码。
        """
        level_value = level.value if isinstance(level, ErrorLevel) else str(level)
        return [item for item in cls if item.level == level_value]
    @classmethod
    def to_dict_map(cls) -> Dict[str, Dict[str, Any]]:
        """
        导出全部错误码定义。
        """
        return {item.key: item.to_dict() for item in cls}
    @classmethod
    def build_response(
        cls,
        key: ErrorCodeLike,
        *,
        success: bool = False,
        trace_id: Optional[str] = None,
        message: Optional[str] = None,
        data: Any = None,
    ) -> Dict[str, Any]:
        """
        构建统一错误响应。
        """
        return {
            "success": success,
            "traceId": trace_id,
            "error": cls.get_error_dict(key, message=message),
            "data": data,
        }

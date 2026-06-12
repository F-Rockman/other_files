"""将稳定查询错误码映射为推荐模块恢复策略。"""

from dataclasses import dataclass
from typing import Dict


BASIC = "basic"
CLARIFY = "clarify"
DISAMBIGUATE = "disambiguate"
REMOVE_INVALID = "remove_invalid"
SIMPLIFY = "simplify"
ADJUST_SCOPE = "adjust_scope"

VALID_RECOVERY_STRATEGIES = {
    BASIC,
    CLARIFY,
    DISAMBIGUATE,
    REMOVE_INVALID,
    SIMPLIFY,
    ADJUST_SCOPE,
}

ALL_DEVICE_IDENTIFIERS = "all_device_identifiers"
IP_IDENTIFIERS = "ip_identifiers"
NAME_IDENTIFIERS = "name_identifiers"
ALL_KPIS = "all_kpis"


@dataclass(frozen=True)
class RefusalRecoveryRule:
    """一个稳定错误码对应的推荐恢复策略和结构化失效规则。"""

    strategy: str
    invalidation: str = ""


REFUSAL_RECOVERY_RULES: Dict[str, RefusalRecoveryRule] = {
    # 第 3 类：意图引导。
    "intent_guide_cross_domain_query": RefusalRecoveryRule(DISAMBIGUATE),
    "intent_guide_device_type_inconsistent": RefusalRecoveryRule(DISAMBIGUATE),
    "intent_guide_device_not_found": RefusalRecoveryRule(
        REMOVE_INVALID, ALL_DEVICE_IDENTIFIERS
    ),
    "intent_guide_metric_not_found": RefusalRecoveryRule(REMOVE_INVALID, ALL_KPIS),
    "intent_guide_unsupported_subnet_metric_query": RefusalRecoveryRule(BASIC),
    "intent_guide_unsupported_subnet_alarm_query": RefusalRecoveryRule(BASIC),
    "intent_guide_relation_not_found": RefusalRecoveryRule(BASIC),
    "intent_guide_field_retrieval_failed": RefusalRecoveryRule(BASIC),
    # 第 4 类：意图追问。
    "intent_clarify_query_object_missing": RefusalRecoveryRule(CLARIFY),
    "intent_clarify_query_intent_ambiguous": RefusalRecoveryRule(CLARIFY),
    "intent_clarify_metric_missing": RefusalRecoveryRule(CLARIFY),
    "intent_clarify_time_range_missing": RefusalRecoveryRule(CLARIFY),
    "intent_clarify_filter_condition_incomplete": RefusalRecoveryRule(CLARIFY),
    "intent_clarify_object_ambiguous": RefusalRecoveryRule(DISAMBIGUATE),
    "intent_clarify_metric_ambiguous": RefusalRecoveryRule(DISAMBIGUATE),
    "intent_clarify_topn_sort_missing": RefusalRecoveryRule(CLARIFY),
    "intent_clarify_metric_meaning_unclear": RefusalRecoveryRule(DISAMBIGUATE),
    "intent_clarify_attribute_meaning_unclear": RefusalRecoveryRule(DISAMBIGUATE),
    "intent_clarify_device_identifier_incomplete": RefusalRecoveryRule(CLARIFY),
    "intent_clarify_time_expression_ambiguous": RefusalRecoveryRule(DISAMBIGUATE),
    "intent_clarify_aggregation_missing": RefusalRecoveryRule(CLARIFY),
    "intent_clarify_group_by_missing": RefusalRecoveryRule(CLARIFY),
    "intent_clarify_unit_ambiguous": RefusalRecoveryRule(CLARIFY),
    # 第 5 类：值检索与实体解析。
    "value_retrieval_ip_not_found": RefusalRecoveryRule(
        REMOVE_INVALID, IP_IDENTIFIERS
    ),
    "value_retrieval_name_not_found": RefusalRecoveryRule(
        REMOVE_INVALID, NAME_IDENTIFIERS
    ),
    "value_retrieval_kpi_not_found": RefusalRecoveryRule(REMOVE_INVALID, ALL_KPIS),
    "value_retrieval_name_multiple_candidates": RefusalRecoveryRule(DISAMBIGUATE),
    "value_retrieval_ip_multiple_candidates": RefusalRecoveryRule(DISAMBIGUATE),
    "value_retrieval_kpi_multiple_candidates": RefusalRecoveryRule(DISAMBIGUATE),
    "value_retrieval_value_semantic_ambiguous": RefusalRecoveryRule(DISAMBIGUATE),
    "value_retrieval_alias_normalization_failed": RefusalRecoveryRule(BASIC),
    # 第 6 类：SQL 生成。
    "sql_generation_schema_mapping_failed": RefusalRecoveryRule(BASIC),
    "sql_generation_join_path_failed": RefusalRecoveryRule(BASIC),
    "sql_generation_unsupported_sql_feature": RefusalRecoveryRule(BASIC),
    "sql_generation_failed": RefusalRecoveryRule(SIMPLIFY),
    "sql_generation_timeout": RefusalRecoveryRule(ADJUST_SCOPE),
    # 第 7 类：查询执行。
    "query_execution_engine_error": RefusalRecoveryRule(SIMPLIFY),
}


def get_refusal_recovery_rule(error_key: str) -> RefusalRecoveryRule:
    """
    根据稳定错误码返回恢复规则。

    所有第 2 类 ``intent_reject_*`` 以及未配置错误码统一返回基础推荐策略。
    未单独配置的第 4 类 ``intent_clarify_*`` 默认使用追问补全策略。
    """
    normalized_key = str(error_key or "")
    if normalized_key.startswith("intent_reject_"):
        return RefusalRecoveryRule(BASIC)
    if normalized_key.startswith("intent_clarify_"):
        return REFUSAL_RECOVERY_RULES.get(normalized_key, RefusalRecoveryRule(CLARIFY))
    return REFUSAL_RECOVERY_RULES.get(normalized_key, RefusalRecoveryRule(BASIC))

"""能力召回模块共享的查询骨架常量。"""

from .refusal_rules import CLARIFY, DISAMBIGUATE


DEVICE_INFO = "device_info"
DEVICE_COUNT = "device_count"
DEVICE_METRIC = "device_metric"
SUBCOMPONENT_INFO = "subcomponent_info"
SUBCOMPONENT_COUNT = "subcomponent_count"
SUBCOMPONENT_METRIC = "subcomponent_metric"

ALARM_QUERY = "alarm_query"
LINK_QUERY = "link_query"
RESOURCE_QUERY = "resource_query"
RELATION_QUERY = "relation_query"

COUNT_AGGREGATIONS = {"count", "count_distinct"}
KPI_RELAXING_RECOVERY_STRATEGIES = {CLARIFY, DISAMBIGUATE}
SPECIAL_CAPABILITY_TYPES = {
    ALARM_QUERY,
    LINK_QUERY,
    RESOURCE_QUERY,
    RELATION_QUERY,
}

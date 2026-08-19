"""根据标准推荐上下文解析主查询骨架。"""

from .capability_constants import (
    ALARM_QUERY,
    COUNT_AGGREGATIONS,
    DEVICE_COUNT,
    DEVICE_INFO,
    DEVICE_METRIC,
    LINK_QUERY,
    RESOURCE_QUERY,
    SUBCOMPONENT_COUNT,
    SUBCOMPONENT_INFO,
    SUBCOMPONENT_METRIC,
)
from .capability_matching import is_subnet_context
from .models import RecommendationContext


def resolve_primary_capability_type(context: RecommendationContext) -> str:
    """根据意图、子部件和 count 聚合确定主查询骨架。"""
    special_type = _special_primary_capability_type(context)
    if special_type:
        return special_type
    if context.intention == "查指标":
        if context.subcomponent_types:
            return SUBCOMPONENT_METRIC
        return DEVICE_METRIC
    if context.intention == "查信息":
        return _information_primary_capability_type(context)
    return ""


def _special_primary_capability_type(context: RecommendationContext) -> str:
    """解析告警、链路和子网资源等特殊主能力。"""
    if context.intention == "查告警":
        return ALARM_QUERY
    if context.intention == "查链路":
        return LINK_QUERY
    if context.intention == "查信息" and is_subnet_context(context):
        return RESOURCE_QUERY
    return ""


def _information_primary_capability_type(context: RecommendationContext) -> str:
    """解析信息意图下的设备或子部件信息、数量骨架。"""
    is_count = bool(COUNT_AGGREGATIONS.intersection(context.aggregations))
    if context.subcomponent_types:
        if is_count:
            return SUBCOMPONENT_COUNT
        return SUBCOMPONENT_INFO
    if is_count:
        return DEVICE_COUNT
    return DEVICE_INFO

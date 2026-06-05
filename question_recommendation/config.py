"""
问数推荐配置常量。
"""

# ============ 输出字段 ============

RECOMMENDS_FIELD = "recommends"
EXPLAIN_FIELD = "explain"

# ============ 输出约束 ============

DEFAULT_RECOMMENDATION_COUNT = 3
MAX_EXPLAIN_LENGTH = 80

# ============ 场景 ============

SCENE_ERROR = "error"
SCENE_NORMAL = "normal"
VALID_SCENE_TYPES = {SCENE_ERROR, SCENE_NORMAL, "failed"}

# ============ 意图与模板类型 ============

VALID_INTENT_TYPES = {"查信息", "查告警", "查指标", "查链路"}

BASIC_TEMPLATE_TYPES = {
    "列表",
    "数量",
    "基础信息",
    "详情",
    "概览",
    "定位",
}

ALARM_TEMPLATE_TYPES = {"告警", "告警列表", "告警数量", "告警分布"}
METRIC_TEMPLATE_TYPES = {"指标", "性能指标", "趋势", "TopN", "TOPN", "排行"}
LINK_TEMPLATE_TYPES = {"链路", "链路关系", "对端设备"}

# ============ 错误与兜底 ============

LLM_CHAT_CALL_ERROR_REASON = "LLM Chat调用异常"
LLM_OUTPUT_FORMAT_ERROR_REASON = "LLM输出格式异常"

DEFAULT_FALLBACK_EXPLAIN = "当前问题不适合直接查询，建议先从同业务域的基础问题开始定位。"
EMPTY_FALLBACK_EXPLAIN = "当前缺少可用推荐能力，暂不生成具体推荐问题。"

# ============ 插槽 ============

GENERIC_SLOT_LABELS = {
    "IP地址",
    "设备名称",
    "MAC地址",
    "接口名称",
    "端口名称",
    "告警名称",
    "时间范围",
    "指标",
    "属性",
    "业务域",
}

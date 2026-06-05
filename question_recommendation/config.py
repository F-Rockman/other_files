"""
问数推荐配置常量。
"""

# ============ 输出字段 ============

RECOMMENDS_FIELD = "recommends"
EXPLAIN_FIELD = "explain"

# ============ 场景 ============

SCENE_ERROR = "error"
SCENE_NORMAL = "normal"
VALID_SCENE_TYPES = {SCENE_ERROR, SCENE_NORMAL, "failed"}

# ============ 错误 ============

LLM_CHAT_CALL_ERROR_REASON = "LLM Chat调用异常"

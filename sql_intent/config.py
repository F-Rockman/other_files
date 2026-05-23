"""
SQL 意图判断配置常量

可通过修改此文件调整默认拒答消息和错误处理行为。
"""

# ============ 默认拒答配置 ============

DEFAULT_REJECT_INTENTION = "reject"
DEFAULT_ACCEPT_INTENTION = "accept"
DEFAULT_EMPTY_REASON = ""

# ============ 错误处理 ============

LLM_OUTPUT_FORMAT_ERROR_REASON = "LLM输出格式异常"
LLM_CALL_ERROR_REASON = "LLM调用异常"

# ============ 输出字段 ============

INTENTION_FIELD = "intention"
REASON_FIELD = "reason"

# ============ 合法意图值 ============

VALID_INTENTIONS = {"accept", "reject"}
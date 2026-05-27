"""
黑化改写配置常量

可通过修改此文件调整默认类型标签和错误处理行为。
"""

# ============ 默认类型标签 ============

DEFAULT_SLANG_TYPE = "slang"
DEFAULT_COMPOUND_TYPE = "compound"
DEFAULT_LITERAL_TYPE = "literal"
DEFAULT_SUBSTRING_TYPE = "substring"

# ============ 错误处理 ============

LLM_OUTPUT_FORMAT_ERROR_REASON = "LLM输出格式异常"
LLM_CALL_ERROR_REASON = "LLM调用异常"
LLM_CHAT_CALL_ERROR_REASON = "LLM Chat调用异常"

# ============ 输出字段 ============

TEXT_FIELD = "text"
MATCHES_FIELD = "matches"
UNRESOLVED_FIELD = "unresolved"
TYPE_FIELD = "type"
CONFIDENCE_FIELD = "confidence"
REASONING_FIELD = "reasoning"
ORIGINAL_FIELD = "original"
REPLACEMENT_FIELD = "replacement"
START_FIELD = "start"
END_FIELD = "end"

# ============ 合法类型值 ============

VALID_TYPES = {"slang", "literal", "substring"}
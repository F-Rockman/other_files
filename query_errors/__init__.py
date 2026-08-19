"""查询系统共享错误类型与稳定错误码。"""

from .errors import ErrorCode, ErrorCodeLike, ErrorInfo, ErrorLevel, ErrorStage

__all__ = [
    "ErrorLevel",
    "ErrorStage",
    "ErrorInfo",
    "ErrorCodeLike",
    "ErrorCode",
]

"""
SQL 生成前置意图判断模块

核心功能：
- classify_intent: 判断用户输入是否应进入 SQL 生成链路
- 使用 LLM 进行意图判断，返回结构化 JSON 结果

LLM 客户端为通用 Callable[[str], str]，不绑定任何特定 LLM SDK。
"""

import json
from typing import Callable, Optional

from .prompt import SQL_INTENT_JUDGMENT_PROMPT
from .config import (
    DEFAULT_REJECT_INTENTION,
    DEFAULT_EMPTY_REASON,
    LLM_OUTPUT_FORMAT_ERROR_REASON,
    LLM_CALL_ERROR_REASON,
    INTENTION_FIELD,
    REASON_FIELD,
    VALID_INTENTIONS,
)


class SQLIntentError(Exception):
    """SQL 意图判断异常"""
    pass


def classify_intent(user_input: str, llm_client: Callable[[str], str]) -> dict:
    """
    判断用户输入是否应进入 SQL 生成链路

    参数:
        user_input: 用户自然语言查询文本
        llm_client: LLM 客户端，Callable[[str], str]，接收 prompt 字符串，返回 LLM 响应字符串

    返回:
        dict: {"intention": "accept" | "reject", "reason": str}
        - intention 为 "accept" 时 reason 为空字符串
        - intention 为 "reject" 时 reason 为拒答原因

    异常:
        SQLIntentError: LLM 调用失败时抛出
    """
    # 组合 prompt 与用户输入
    full_prompt = f"{SQL_INTENT_JUDGMENT_PROMPT}\n\n用户输入：{user_input}"

    # 调用 LLM
    try:
        llm_response = llm_client(full_prompt)
    except Exception as e:
        raise SQLIntentError(f"{LLM_CALL_ERROR_REASON}: {e}")

    # 解析 JSON 响应
    return _parse_llm_response(llm_response)


def _parse_llm_response(llm_response: str) -> dict:
    """
    解析 LLM 返回的 JSON 响应

    处理策略：
    1. 直接 JSON 解析
    2. 提取 JSON 块（LLM 可能输出 markdown 包裹的 JSON）
    3. 解析失败时返回 reject + 格式异常原因
    """
    # 尝试直接解析
    try:
        result = json.loads(llm_response)
        return _validate_result(result)
    except json.JSONDecodeError:
        pass

    # 尝试提取 markdown 代码块中的 JSON
    json_block = _extract_json_block(llm_response)
    if json_block:
        try:
            result = json.loads(json_block)
            return _validate_result(result)
        except json.JSONDecodeError:
            pass

    # 所有解析尝试失败，返回格式异常拒答
    return {
        INTENTION_FIELD: DEFAULT_REJECT_INTENTION,
        REASON_FIELD: LLM_OUTPUT_FORMAT_ERROR_REASON,
    }


def _extract_json_block(text: str) -> Optional[str]:
    """
    从 LLM 响应中提取 JSON 代码块

    支持 ```json ... ``` 和 ``` ... ``` 格式
    """
    import re
    # 匹配 ```json ... ``` 或 ``` ... ``` 中的内容
    patterns = [
        r'```json\s*\n?(.*?)\n?\s*```',
        r'```\s*\n?(.*?)\n?\s*```',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()

    # 尞试找到 { ... } 结构
    match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
    if match:
        return match.group(0)

    return None


def _validate_result(result: dict) -> dict:
    """
    验证并规范化 LLM 返回的结果

    确保 intention 字段合法，reason 字段存在
    """
    intention = result.get(INTENTION_FIELD, DEFAULT_REJECT_INTENTION)
    reason = result.get(REASON_FIELD, DEFAULT_EMPTY_REASON)

    # 验证 intention 值
    if intention not in VALID_INTENTIONS:
        intention = DEFAULT_REJECT_INTENTION
        reason = LLM_OUTPUT_FORMAT_ERROR_REASON

    # accept 时 reason 应为空字符串
    if intention == "accept":
        reason = DEFAULT_EMPTY_REASON

    return {
        INTENTION_FIELD: intention,
        REASON_FIELD: reason,
    }
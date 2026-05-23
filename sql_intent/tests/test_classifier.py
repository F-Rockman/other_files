"""
SQL 意图判断单元测试

测试覆盖：
- Prompt 常量存在且非空
- JSON 解析：有效 LLM 响应
- JSON 解析：markdown 包裹的 JSON
- JSON 解析错误处理
- LLM 调用异常处理
- classify_intent 函数签名
- 结果验证与规范化
"""

import json
import pytest
from unittest.mock import MagicMock

from sql_intent import classify_intent, SQLIntentError, SQL_INTENT_JUDGMENT_PROMPT
from sql_intent.classifier import _parse_llm_response, _validate_result
from sql_intent.config import (
    DEFAULT_REJECT_INTENTION,
    DEFAULT_ACCEPT_INTENTION,
    DEFAULT_EMPTY_REASON,
    LLM_OUTPUT_FORMAT_ERROR_REASON,
    LLM_CALL_ERROR_REASON,
    INTENTION_FIELD,
    REASON_FIELD,
    VALID_INTENTIONS,
)


# ============ 测试：Prompt 常量 ============

class TestPromptConstant:
    def test_prompt_exists(self):
        assert SQL_INTENT_JUDGMENT_PROMPT is not None

    def test_prompt_non_empty(self):
        assert len(SQL_INTENT_JUDGMENT_PROMPT) > 0

    def test_prompt_contains_key_sections(self):
        assert "SQL 生成前置意图判断器" in SQL_INTENT_JUDGMENT_PROMPT
        assert "accept" in SQL_INTENT_JUDGMENT_PROMPT
        assert "reject" in SQL_INTENT_JUDGMENT_PROMPT
        assert "输出格式" in SQL_INTENT_JUDGMENT_PROMPT
        assert "R1" in SQL_INTENT_JUDGMENT_PROMPT
        assert "R2" in SQL_INTENT_JUDGMENT_PROMPT
        assert "R3" in SQL_INTENT_JUDGMENT_PROMPT
        assert "R4" in SQL_INTENT_JUDGMENT_PROMPT
        assert "R5" in SQL_INTENT_JUDGMENT_PROMPT


# ============ 测试：JSON 解析有效响应 ============

class TestJSONParsing:
    def test_parse_valid_accept_json(self):
        response = json.dumps({"intention": "accept", "reason": ""})
        result = _parse_llm_response(response)
        assert result[INTENTION_FIELD] == DEFAULT_ACCEPT_INTENTION
        assert result[REASON_FIELD] == DEFAULT_EMPTY_REASON

    def test_parse_valid_reject_json(self):
        response = json.dumps({"intention": "reject", "reason": "非问数场景"})
        result = _parse_llm_response(response)
        assert result[INTENTION_FIELD] == DEFAULT_REJECT_INTENTION
        assert result[REASON_FIELD] == "非问数场景"

    def test_parse_json_with_markdown_wrapper(self):
        response = '```json\n{"intention": "accept", "reason": ""}\n```'
        result = _parse_llm_response(response)
        assert result[INTENTION_FIELD] == DEFAULT_ACCEPT_INTENTION
        assert result[REASON_FIELD] == DEFAULT_EMPTY_REASON

    def test_parse_json_with_plain_code_block(self):
        response = '```\n{"intention": "reject", "reason": "多意图组合"}\n```'
        result = _parse_llm_response(response)
        assert result[INTENTION_FIELD] == DEFAULT_REJECT_INTENTION
        assert result[REASON_FIELD] == "多意图组合"

    def test_parse_json_with_surrounding_text(self):
        response = '根据分析，结果如下：\n{"intention": "reject", "reason": "条件不完整"}\n以上是判断结果。'
        result = _parse_llm_response(response)
        assert result[INTENTION_FIELD] == DEFAULT_REJECT_INTENTION
        assert result[REASON_FIELD] == "条件不完整"


# ============ 测试：JSON 解析错误处理 ============

class TestJSONParsingError:
    def test_invalid_json_returns_format_error(self):
        response = "这不是JSON格式"
        result = _parse_llm_response(response)
        assert result[INTENTION_FIELD] == DEFAULT_REJECT_INTENTION
        assert result[REASON_FIELD] == LLM_OUTPUT_FORMAT_ERROR_REASON

    def test_empty_response_returns_format_error(self):
        result = _parse_llm_response("")
        assert result[INTENTION_FIELD] == DEFAULT_REJECT_INTENTION
        assert result[REASON_FIELD] == LLM_OUTPUT_FORMAT_ERROR_REASON

    def test_partial_json_returns_format_error(self):
        response = '{"intention": "accept"'
        result = _parse_llm_response(response)
        assert result[INTENTION_FIELD] == DEFAULT_REJECT_INTENTION
        assert result[REASON_FIELD] == LLM_OUTPUT_FORMAT_ERROR_REASON

    def test_invalid_intention_value_normalized(self):
        response = json.dumps({"intention": "maybe", "reason": "不确定"})
        result = _parse_llm_response(response)
        assert result[INTENTION_FIELD] == DEFAULT_REJECT_INTENTION
        assert result[REASON_FIELD] == LLM_OUTPUT_FORMAT_ERROR_REASON

    def test_missing_intention_field(self):
        response = json.dumps({"reason": "缺少意图字段"})
        result = _parse_llm_response(response)
        assert result[INTENTION_FIELD] == DEFAULT_REJECT_INTENTION


# ============ 测试：classify_intent 函数签名与行为 ============

class TestClassifyIntent:
    def test_function_signature_accepts_callable(self):
        """classify_intent 接受字符串和 Callable 参数"""
        mock_client = MagicMock(return_value=json.dumps({"intention": "accept", "reason": ""}))
        result = classify_intent("各省份的销售额", mock_client)
        assert result[INTENTION_FIELD] == DEFAULT_ACCEPT_INTENTION
        assert mock_client.called

    def test_prompt_includes_user_input(self):
        """LLM 收到的 prompt 包含用户输入"""
        user_input = "查询华东区域的订单数量"
        mock_client = MagicMock(return_value=json.dumps({"intention": "accept", "reason": ""}))
        classify_intent(user_input, mock_client)
        call_args = mock_client.call_args[0][0]
        assert user_input in call_args
        assert "SQL 生成前置意图判断器" in call_args

    def test_reject_result_with_reason(self):
        """拒答时返回原因"""
        mock_client = MagicMock(return_value=json.dumps({"intention": "reject", "reason": "非问数场景"}))
        result = classify_intent("帮我分析一下销量下滑的原因", mock_client)
        assert result[INTENTION_FIELD] == DEFAULT_REJECT_INTENTION
        assert result[REASON_FIELD] == "非问数场景"

    def test_accept_result_with_empty_reason(self):
        """通过时 reason 为空字符串"""
        mock_client = MagicMock(return_value=json.dumps({"intention": "accept", "reason": ""}))
        result = classify_intent("各省份的销售额", mock_client)
        assert result[INTENTION_FIELD] == DEFAULT_ACCEPT_INTENTION
        assert result[REASON_FIELD] == DEFAULT_EMPTY_REASON

    def test_llm_call_exception_raises_sql_intent_error(self):
        """LLM 调用异常时抛出 SQLIntentError"""
        mock_client = MagicMock(side_effect=RuntimeError("连接超时"))
        with pytest.raises(SQLIntentError) as exc_info:
            classify_intent("测试输入", mock_client)
        assert LLM_CALL_ERROR_REASON in str(exc_info.value)

    def test_llm_format_error_returns_reject(self):
        """LLM 返回格式异常时返回 reject（不抛异常）"""
        mock_client = MagicMock(return_value="无法解析的文本")
        result = classify_intent("测试输入", mock_client)
        assert result[INTENTION_FIELD] == DEFAULT_REJECT_INTENTION
        assert result[REASON_FIELD] == LLM_OUTPUT_FORMAT_ERROR_REASON


# ============ 测试：结果验证与规范化 ============

class TestResultValidation:
    def test_accept_reason_normalized_to_empty(self):
        """accept 时 reason 强制为空字符串"""
        result = _validate_result({"intention": "accept", "reason": "不应该有原因"})
        assert result[REASON_FIELD] == DEFAULT_EMPTY_REASON

    def test_reject_reason_preserved(self):
        """reject 时 reason 保持原值"""
        result = _validate_result({"intention": "reject", "reason": "条件不完整"})
        assert result[REASON_FIELD] == "条件不完整"

    def test_invalid_intention_rejected(self):
        """非法 intention 值被替换为 reject"""
        result = _validate_result({"intention": "unknown", "reason": ""})
        assert result[INTENTION_FIELD] == DEFAULT_REJECT_INTENTION
        assert result[REASON_FIELD] == LLM_OUTPUT_FORMAT_ERROR_REASON

    def test_config_constants(self):
        """验证配置常量值"""
        assert DEFAULT_REJECT_INTENTION == "reject"
        assert DEFAULT_ACCEPT_INTENTION == "accept"
        assert DEFAULT_EMPTY_REASON == ""
        assert LLM_OUTPUT_FORMAT_ERROR_REASON == "LLM输出格式异常"
        assert LLM_CALL_ERROR_REASON == "LLM调用异常"
        assert VALID_INTENTIONS == {"accept", "reject"}
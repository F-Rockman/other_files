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

from sql_intent import classify_intent, classify_intent_chat, SQLIntentError, SQL_INTENT_JUDGMENT_PROMPT, SQL_INTENT_SYSTEM_PROMPT, SQL_INTENT_USER_TEMPLATE
from sql_intent.classifier import _parse_llm_response, _validate_result
from sql_intent.config import (
    DEFAULT_REJECT_INTENTION,
    DEFAULT_ACCEPT_INTENTION,
    DEFAULT_EMPTY_REASON,
    LLM_OUTPUT_FORMAT_ERROR_REASON,
    LLM_CALL_ERROR_REASON,
    LLM_CHAT_CALL_ERROR_REASON,
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
        # SSC qualifying condition
        assert "限定条件" in SQL_INTENT_SYSTEM_PROMPT
        # New R4 sub-rule
        assert "不同SQL结构类型" in SQL_INTENT_SYSTEM_PROMPT
        # Specific example in R4
        assert "标量聚合 vs 时序多行" in SQL_INTENT_SYSTEM_PROMPT
        # Trend clarification
        assert "不覆盖额外独立请求" in SQL_INTENT_SYSTEM_PROMPT
        # Future full-period clarification
        assert "当前系统时间" in SQL_INTENT_SYSTEM_PROMPT
        assert "完整自然年" in SQL_INTENT_SYSTEM_PROMPT
        assert "2026年的销售额" in SQL_INTENT_SYSTEM_PROMPT
        assert "2027年的销售额" in SQL_INTENT_SYSTEM_PROMPT

    def test_ssc_qualifying_condition_present(self):
        assert "限定条件：多个指标必须在同一 GROUP BY 结构下可并列输出为 SELECT 的多列" in SQL_INTENT_SYSTEM_PROMPT
        assert "结果行粒度一致" in SQL_INTENT_SYSTEM_PROMPT
        assert "标量 vs 多行时序/明细" in SQL_INTENT_SYSTEM_PROMPT

    def test_multi_intent_sql_structure_rule_present(self):
        assert "同一对象+不同SQL结构类型" in SQL_INTENT_SYSTEM_PROMPT
        assert "无法在单一 SELECT 中并列输出" in SQL_INTENT_SYSTEM_PROMPT
        assert "IP为A的设备数量及性能趋势" in SQL_INTENT_SYSTEM_PROMPT
        assert "GROUP BY day vs month" in SQL_INTENT_SYSTEM_PROMPT

    def test_counter_example_interception_zone_present(self):
        """反例拦截区存在且包含关键反例"""
        assert "看似单意图，实际是多意图" in SQL_INTENT_SYSTEM_PROMPT
        assert "反例拦截" in SQL_INTENT_SYSTEM_PROMPT
        assert "IP为A的设备的数量及设备A的性能趋势" in SQL_INTENT_SYSTEM_PROMPT
        assert "设备A的告警数量及告警趋势" in SQL_INTENT_SYSTEM_PROMPT
        assert "最近一周每天的订单量及总订单数" in SQL_INTENT_SYSTEM_PROMPT
        assert "查询最近一天的CPU利用率" in SQL_INTENT_SYSTEM_PROMPT
        assert "核心判断方法" in SQL_INTENT_SYSTEM_PROMPT
        assert "标量/明细行/时序行/排名行/对比行" in SQL_INTENT_SYSTEM_PROMPT

    def test_ambiguous_intent_rules_present(self):
        """R3和R5中的条件不完整和模糊意图规则存在"""
        # R3: 指标缺少所属对象/实体
        assert "指标缺少所属对象/实体" in SQL_INTENT_SYSTEM_PROMPT
        assert "CPU利用率是设备的指标，但未指定哪台设备" in SQL_INTENT_SYSTEM_PROMPT
        # R5: 指标+对象但未指定展示形式
        assert "指标+对象但未指定展示形式" in SQL_INTENT_SYSTEM_PROMPT

    def test_display_form_priority_principle_present(self):
        """展示形式优先原则和排名查询规则存在"""
        assert "展示形式优先原则" in SQL_INTENT_SYSTEM_PROMPT
        assert "不应从指标名称额外推断隐含的其他展示形式" in SQL_INTENT_SYSTEM_PROMPT
        assert "设备A的Top3 CPU利用率" in SQL_INTENT_SYSTEM_PROMPT
        assert "排名查询" in SQL_INTENT_SYSTEM_PROMPT
        assert "Top3明确指定了排名展示形式" in SQL_INTENT_SYSTEM_PROMPT


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
        classify_intent(user_input, mock_client, current_time="date=2026-05-24")
        call_args = mock_client.call_args[0][0]
        assert user_input in call_args
        assert "SQL 生成前置意图判断器" in call_args
        assert "当前系统时间：date=2026-05-24" in call_args

    def test_prompt_includes_current_time_for_future_period_judgment(self):
        """完整年份销售额判断需要当前系统时间上下文"""
        user_input = "2026年的销售额"
        mock_client = MagicMock(return_value=json.dumps({"intention": "reject", "reason": "未来数据"}))
        classify_intent(user_input, mock_client, current_time="date=2026-05-24")
        call_args = mock_client.call_args[0][0]
        assert "当前系统时间：date=2026-05-24" in call_args
        assert "2026年的销售额" in call_args
        assert "完整自然年" in call_args
        assert "2027年的销售额" in call_args

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
        assert LLM_CHAT_CALL_ERROR_REASON == "LLM Chat调用异常"
        assert VALID_INTENTIONS == {"accept", "reject"}


# ============ 测试：拆分 Prompt 常量 ============

class TestSplitPrompts:
    def test_system_prompt_exists(self):
        """SQL_INTENT_SYSTEM_PROMPT 存在且非空"""
        assert SQL_INTENT_SYSTEM_PROMPT is not None
        assert len(SQL_INTENT_SYSTEM_PROMPT) > 0

    def test_system_prompt_contains_key_rule_sections(self):
        """SQL_INTENT_SYSTEM_PROMPT 包含 R1-R5、accept 条件、决策优先级"""
        assert "SQL 生成前置意图判断器" in SQL_INTENT_SYSTEM_PROMPT
        assert "R1" in SQL_INTENT_SYSTEM_PROMPT
        assert "R2" in SQL_INTENT_SYSTEM_PROMPT
        assert "R3" in SQL_INTENT_SYSTEM_PROMPT
        assert "R4" in SQL_INTENT_SYSTEM_PROMPT
        assert "R5" in SQL_INTENT_SYSTEM_PROMPT
        assert "accept" in SQL_INTENT_SYSTEM_PROMPT
        assert "reject" in SQL_INTENT_SYSTEM_PROMPT
        assert "决策优先级" in SQL_INTENT_SYSTEM_PROMPT
        assert "判断原则" in SQL_INTENT_SYSTEM_PROMPT

    def test_user_template_exists(self):
        """SQL_INTENT_USER_TEMPLATE 存在且包含 {user_input} 占位符"""
        assert SQL_INTENT_USER_TEMPLATE is not None
        assert "{user_input}" in SQL_INTENT_USER_TEMPLATE
        assert "{current_time}" in SQL_INTENT_USER_TEMPLATE

    def test_user_template_format_produces_expected_string(self):
        """SQL_INTENT_USER_TEMPLATE 格式化后生成正确字符串"""
        formatted = SQL_INTENT_USER_TEMPLATE.format(
            current_time="date=2026-05-24, datetime=2026-05-24T12:00:00+08:00, timezone=Asia/Shanghai",
            user_input="测试输入",
        )
        assert "当前系统时间：date=2026-05-24" in formatted
        assert "用户输入：测试输入" in formatted

    def test_backward_compat_judgment_prompt_equals_concatenation(self):
        """SQL_INTENT_JUDGMENT_PROMPT 等于 SYSTEM_PROMPT + 分隔符 + USER_TEMPLATE"""
        assert SQL_INTENT_JUDGMENT_PROMPT == SQL_INTENT_SYSTEM_PROMPT + "\n\n" + SQL_INTENT_USER_TEMPLATE

    def test_backward_compat_formatted_equals_old_style(self):
        """格式化后的拼接结果与旧式 f-string 结果一致"""
        test_input = "各省份的销售额"
        current_time = "date=2026-05-24, datetime=2026-05-24T12:00:00+08:00, timezone=Asia/Shanghai"
        new_style = SQL_INTENT_SYSTEM_PROMPT + "\n\n" + SQL_INTENT_USER_TEMPLATE.format(
            current_time=current_time,
            user_input=test_input,
        )
        old_style = f"{SQL_INTENT_SYSTEM_PROMPT}\n\n当前系统时间：{current_time}\n用户输入：{test_input}"
        assert new_style == old_style


# ============ 测试：classify_intent_chat 函数 ============

class TestClassifyIntentChat:
    def test_function_signature_accepts_callable(self):
        """classify_intent_chat 接受字符串和 Callable 参数"""
        mock_client = MagicMock(return_value=json.dumps({"intention": "accept", "reason": ""}))
        result = classify_intent_chat("各省份的销售额", mock_client)
        assert result[INTENTION_FIELD] == DEFAULT_ACCEPT_INTENTION
        assert mock_client.called

    def test_sends_correct_system_and_user_messages(self):
        """llm_chat_client 收到正确的 system 和 user 消息列表"""
        user_input = "查询华东区域的订单数量"
        mock_client = MagicMock(return_value=json.dumps({"intention": "accept", "reason": ""}))
        classify_intent_chat(user_input, mock_client, current_time="date=2026-05-24")
        messages = mock_client.call_args[0][0]
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == SQL_INTENT_SYSTEM_PROMPT
        assert messages[1]["role"] == "user"
        assert "当前系统时间：date=2026-05-24" in messages[1]["content"]
        assert user_input in messages[1]["content"]

    def test_reject_result_with_reason(self):
        """拒答时返回原因"""
        mock_client = MagicMock(return_value=json.dumps({"intention": "reject", "reason": "非问数场景"}))
        result = classify_intent_chat("帮我分析一下销量下滑的原因", mock_client)
        assert result[INTENTION_FIELD] == DEFAULT_REJECT_INTENTION
        assert result[REASON_FIELD] == "非问数场景"

    def test_accept_result_with_empty_reason(self):
        """通过时 reason 为空字符串"""
        mock_client = MagicMock(return_value=json.dumps({"intention": "accept", "reason": ""}))
        result = classify_intent_chat("各省份的销售额", mock_client)
        assert result[INTENTION_FIELD] == DEFAULT_ACCEPT_INTENTION
        assert result[REASON_FIELD] == DEFAULT_EMPTY_REASON

    def test_llm_chat_call_exception_raises_sql_intent_error(self):
        """LLM Chat 调用异常时抛出 SQLIntentError"""
        mock_client = MagicMock(side_effect=RuntimeError("连接超时"))
        with pytest.raises(SQLIntentError) as exc_info:
            classify_intent_chat("测试输入", mock_client)
        assert LLM_CHAT_CALL_ERROR_REASON in str(exc_info.value)

    def test_llm_format_error_returns_reject(self):
        """LLM 返回格式异常时返回 reject（不抛异常）"""
        mock_client = MagicMock(return_value="无法解析的文本")
        result = classify_intent_chat("测试输入", mock_client)
        assert result[INTENTION_FIELD] == DEFAULT_REJECT_INTENTION
        assert result[REASON_FIELD] == LLM_OUTPUT_FORMAT_ERROR_REASON

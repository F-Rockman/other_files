"""
结构化模板问数推荐单元测试。
"""

import json
from unittest.mock import MagicMock

from question_recommendation import (
    QUESTION_RECOMMENDATION_SYSTEM_PROMPT,
    QUESTION_RECOMMENDATION_USER_TEMPLATE,
    MetadataColumn,
    RecognizedIntent,
    StructuredTemplate,
    recommend_questions_chat,
)
from question_recommendation.recommender import _group_metadata_by_table, _parse_llm_response


def _network_interface_intent():
    return RecognizedIntent(
        intent_type="查信息",
        domain_info="网络",
        device_info={"name": "网络设备"},
        sub_component_info={"name": "接口"},
    )


def _network_templates():
    return [
        StructuredTemplate(
            template_id="network_interface_list",
            template_text="查询网络设备接口列表",
            intent_tags=["查信息"],
            domain_tags=["网络"],
            object_tags=["网络设备", "接口"],
            parent_object="网络设备",
            child_object="接口",
            template_type="列表",
            priority=30,
        ),
        StructuredTemplate(
            template_id="network_interface_info",
            template_text="查询网络设备接口基础信息",
            intent_tags=["查信息"],
            domain_tags=["网络"],
            object_tags=["网络设备", "接口"],
            parent_object="网络设备",
            child_object="接口",
            template_type="基础信息",
            priority=20,
        ),
        StructuredTemplate(
            template_id="network_interface_count",
            template_text="查询网络设备接口数量",
            intent_tags=["查信息"],
            domain_tags=["网络"],
            object_tags=["网络设备", "接口"],
            parent_object="网络设备",
            child_object="接口",
            template_type="数量",
            priority=10,
        ),
        StructuredTemplate(
            template_id="server_fan_list",
            template_text="查询服务器风扇列表",
            intent_tags=["查信息"],
            domain_tags=["服务器"],
            object_tags=["服务器", "风扇"],
            parent_object="服务器",
            child_object="风扇",
            template_type="列表",
            priority=999,
        ),
    ]


def test_prompt_contains_structured_template_rules():
    assert "结构化意图" in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "模板标签" in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "失败恢复" in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "输出前自检" in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert '"recommends"' in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "{recognized_intent_json}" in QUESTION_RECOMMENDATION_USER_TEMPLATE
    assert "{candidate_templates_json}" in QUESTION_RECOMMENDATION_USER_TEMPLATE


def test_parse_valid_json():
    response = json.dumps(
        {
            "recommends": ["查询网络设备接口列表", "查询网络设备接口数量", "查询网络设备接口基础信息"],
            "explain": "建议先从接口基础问题定位。",
        },
        ensure_ascii=False,
    )
    result = _parse_llm_response(response)
    assert result["recommends"] == ["查询网络设备接口列表", "查询网络设备接口数量", "查询网络设备接口基础信息"]
    assert result["explain"] == "建议先从接口基础问题定位。"


def test_parse_markdown_wrapped_json():
    response = """```json
{"recommends": ["A", "B", "C"], "explain": "ok"}
```"""
    result = _parse_llm_response(response)
    assert result["recommends"] == ["A", "B", "C"]
    assert result["explain"] == "ok"


def test_parse_old_recommendations_shape():
    response = json.dumps(
        {
            "recommendations": [
                {"question": "查询网络设备接口列表"},
                {"question": "查询网络设备接口数量"},
            ],
            "explain": "兼容旧结构",
        },
        ensure_ascii=False,
    )
    result = _parse_llm_response(response)
    assert result["recommends"] == ["查询网络设备接口列表", "查询网络设备接口数量"]


def test_chat_recommend_questions_includes_grouped_multi_table_metadata():
    llm_chat_client = MagicMock(
        return_value=json.dumps(
            {
                "recommends": ["查询网络设备接口列表", "查询网络设备接口数量", "查询网络设备接口基础信息"],
                "explain": "建议先围绕网络设备接口继续查询。",
            },
            ensure_ascii=False,
        )
    )

    result = recommend_questions_chat(
        "查询网络设备接口",
        llm_chat_client,
        scene_type="normal",
        recognized_intent=_network_interface_intent(),
        candidate_templates=_network_templates(),
        metadata_columns=[
            MetadataColumn(
                table_name="network_device",
                table_description="网络设备",
                column_name="device_name",
                column_description="设备名称",
            ),
            MetadataColumn(
                table_name="network_interface",
                table_description="网络设备接口",
                column_name="interface_name",
                column_description="接口名称",
            ),
            MetadataColumn(
                table_name="network_interface",
                table_description="网络设备接口",
                column_name="status",
                column_description="接口状态",
            ),
        ],
    )

    assert result["recommends"] == ["查询网络设备接口列表", "查询网络设备接口数量", "查询网络设备接口基础信息"]
    assert result["explain"] == "建议先围绕网络设备接口继续查询。"
    messages = llm_chat_client.call_args[0][0]
    user_prompt = messages[1]["content"]
    assert "network_interface_list" in user_prompt
    assert "结构化意图识别结果 recognized_intent" in user_prompt
    assert "按表组织的表列元数据 metadata_tables" in user_prompt
    assert user_prompt.count('"table_name": "network_interface"') == 1
    assert '"column_description": "接口状态"' in user_prompt


def test_chat_recommend_questions_success():
    llm_chat_client = MagicMock(
        return_value=json.dumps(
            {
                "recommends": ["查询网络设备接口列表", "查询网络设备接口数量", "查询网络设备接口基础信息"],
                "explain": "建议继续查看接口基础信息。",
            },
            ensure_ascii=False,
        )
    )

    result = recommend_questions_chat(
        "查询网络设备接口",
        llm_chat_client,
        recognized_intent=_network_interface_intent(),
        candidate_templates=_network_templates(),
    )

    assert len(result["recommends"]) == 3
    messages = llm_chat_client.call_args[0][0]
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "candidate_templates" in messages[1]["content"]


def test_invalid_json_uses_same_domain_same_object_fallback():
    llm_chat_client = MagicMock(return_value="not json")

    result = recommend_questions_chat(
        "查询网络设备接口",
        llm_chat_client,
        scene_type="error",
        intercept_reason="当前问题暂时无法转换为可执行查询",
        recognized_intent=_network_interface_intent(),
        candidate_templates=_network_templates(),
    )

    assert len(result["recommends"]) == 3
    assert all("网络设备接口" in item for item in result["recommends"])
    assert all("服务器" not in item for item in result["recommends"])


def test_invalid_slot_is_removed_and_filled_by_fallback():
    llm_chat_client = MagicMock(
        return_value=json.dumps(
            {
                "recommends": [
                    "查询 IP 为 1.1.1.1 的网络设备接口列表",
                    "查询网络设备接口列表",
                ],
                "explain": "建议先放宽范围定位接口。",
            },
            ensure_ascii=False,
        )
    )

    result = recommend_questions_chat(
        "查询 IP 为 1.1.1.1 的网络设备接口",
        llm_chat_client,
        scene_type="error",
        intercept_reason="未找到 IP 为 1.1.1.1 的设备",
        recognized_intent=_network_interface_intent(),
        candidate_templates=_network_templates(),
    )

    assert len(result["recommends"]) == 3
    assert all("1.1.1.1" not in item for item in result["recommends"])


def test_enum_template_is_naturalized_in_fallback():
    enum_template = StructuredTemplate(
        template_id="network_interface_enum",
        template_text="查询 IP地址/设备名称 的 接口/端口/单板/光模块 列表/数量/TOPN",
        intent_tags=["查信息"],
        domain_tags=["网络"],
        object_tags=["网络设备", "接口"],
        parent_object="网络设备",
        child_object="接口",
        template_type="列表",
        slots=["device_ip_or_name"],
        priority=100,
    )
    llm_chat_client = MagicMock(return_value="not json")

    result = recommend_questions_chat(
        "查询网络设备接口",
        llm_chat_client,
        scene_type="error",
        recognized_intent=_network_interface_intent(),
        candidate_templates=[enum_template],
    )

    assert result["recommends"]
    assert all("/" not in item for item in result["recommends"])
    assert any("IP 为“IP地址”" in item for item in result["recommends"])


def test_metadata_columns_group_by_table():
    grouped = _group_metadata_by_table(
        [
            MetadataColumn("device", "设备", "name", "设备名称"),
            MetadataColumn("device", "设备", "ip", "设备IP地址"),
            MetadataColumn("metric", "设备性能指标", "cpu_usage", "CPU利用率"),
        ]
    )

    assert len(grouped) == 2
    assert grouped[0]["table_name"] == "device"
    assert len(grouped[0]["columns"]) == 2
    assert grouped[1]["columns"] == [
        {"column_name": "cpu_usage", "column_description": "CPU利用率"}
    ]


def test_metadata_column_keeps_only_four_supported_fields():
    metadata = MetadataColumn.from_dict(
        {
            "table_name": "device",
            "table_description": "设备",
            "column_name": "name",
            "column_description": "设备名称",
            "data_type": "string",
            "enum_meanings": {"a": "A"},
        }
    )

    assert metadata.to_dict() == {
        "table_name": "device",
        "table_description": "设备",
        "column_name": "name",
        "column_description": "设备名称",
    }

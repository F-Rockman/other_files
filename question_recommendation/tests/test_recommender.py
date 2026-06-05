"""
结构化模板问数推荐单元测试。
"""

import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

from question_recommendation import (
    QUESTION_RECOMMENDATION_SYSTEM_PROMPT,
    QUESTION_RECOMMENDATION_USER_TEMPLATE,
    LogicalMetadataError,
    MetadataColumn,
    RecognizedIntent,
    StructuredTemplate,
    load_logical_metadata,
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


def test_parse_old_recommendations_shape_is_rejected():
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
    assert result is None


def test_chat_recommend_questions_loads_and_groups_multi_table_metadata(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "yaml", SimpleNamespace(safe_load=lambda stream: json.load(stream)))
    (tmp_path / "network_device.logical.yaml").write_text(
        json.dumps(
            {
                "name": "network_device",
                "description_cn": "网络设备",
                "schema": {
                    "fields": [
                        {"name": "device_name", "description_cn": "设备名称"},
                    ]
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "network_interface.logical.yaml").write_text(
        json.dumps(
            {
                "name": "network_interface",
                "description_cn": "网络设备接口",
                "schema": {
                    "fields": [
                        {"name": "interface_name", "description_cn": "接口名称"},
                        {"name": "status", "description_cn": "接口状态"},
                    ]
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
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
        recognized_intent=RecognizedIntent(
            intent_type="查信息",
            domain_info="网络",
            device_info={"name": "网络设备"},
            sub_component_info={"name": "接口"},
            tables=["network_device", "network_interface"],
        ),
        candidate_templates=_network_templates(),
        logical_model_path_provider=lambda: tmp_path,
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


def test_invalid_json_returns_empty_structure():
    llm_chat_client = MagicMock(return_value="not json")

    result = recommend_questions_chat(
        "查询网络设备接口",
        llm_chat_client,
        scene_type="error",
        intercept_reason="当前问题暂时无法转换为可执行查询",
        recognized_intent=_network_interface_intent(),
        candidate_templates=_network_templates(),
    )

    assert result == {"recommends": [], "explain": ""}


def test_structurally_valid_result_is_returned_without_content_filtering():
    llm_chat_client = MagicMock(
        return_value=json.dumps(
            {
                "recommends": [
                    "查询 IP 为 1.1.1.1 的网络设备接口列表",
                    "查询 IP地址/设备名称 的 接口/端口 列表/数量",
                    "查询 IP 为 1.1.1.1 的网络设备接口列表",
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

    assert result["recommends"] == [
        "查询 IP 为 1.1.1.1 的网络设备接口列表",
        "查询 IP地址/设备名称 的 接口/端口 列表/数量",
        "查询 IP 为 1.1.1.1 的网络设备接口列表",
    ]


def test_one_recommendation_is_returned_without_filling_to_three():
    llm_chat_client = MagicMock(
        return_value=json.dumps(
            {"recommends": ["查询网络设备接口列表"], "explain": "只找到一条合适推荐"},
            ensure_ascii=False,
        )
    )

    result = recommend_questions_chat(
        "查询网络设备接口",
        llm_chat_client,
        scene_type="error",
        recognized_intent=_network_interface_intent(),
        candidate_templates=_network_templates(),
    )

    assert result == {
        "recommends": ["查询网络设备接口列表"],
        "explain": "只找到一条合适推荐",
    }


def test_invalid_recommendation_item_type_returns_empty_structure():
    llm_chat_client = MagicMock(
        return_value=json.dumps(
            {"recommends": [{"question": "查询网络设备接口列表"}], "explain": "invalid"},
            ensure_ascii=False,
        )
    )

    result = recommend_questions_chat(
        "查询网络设备接口",
        llm_chat_client,
        recognized_intent=_network_interface_intent(),
        candidate_templates=_network_templates(),
    )

    assert result == {"recommends": [], "explain": ""}


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


def test_load_logical_metadata_skips_missing_and_unsafe_tables(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "yaml", SimpleNamespace(safe_load=lambda stream: json.load(stream)))
    (tmp_path / "device.logical.yaml").write_text(
        json.dumps(
            {
                "name": "device",
                "description_cn": "设备",
                "schema": {
                    "fields": [
                        {"name": "ip", "description_cn": "设备IP地址"},
                        {"name": "", "description_cn": "无效列"},
                    ]
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    metadata = load_logical_metadata(
        ["device", "missing", "../unsafe", "device"],
        lambda: tmp_path,
    )

    assert [item.to_dict() for item in metadata] == [
        {
            "table_name": "device",
            "table_description": "设备",
            "column_name": "ip",
            "column_description": "设备IP地址",
        }
    ]


def test_load_logical_metadata_rejects_invalid_directory(tmp_path):
    missing = tmp_path / "missing"
    try:
        load_logical_metadata(["device"], lambda: missing)
    except LogicalMetadataError as exc:
        assert "不存在或不是目录" in str(exc)
    else:
        raise AssertionError("expected LogicalMetadataError")


def test_recognized_intent_normalizes_tables():
    intent = RecognizedIntent.from_dict({"intent": "查信息", "tables": "network_device"})
    assert intent.tables == ["network_device"]

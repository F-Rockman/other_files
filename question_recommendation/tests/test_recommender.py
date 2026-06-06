"""最小化上下文和能力卡推荐单元测试。"""

import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

from question_recommendation import (
    QUESTION_RECOMMENDATION_SYSTEM_PROMPT,
    QUESTION_RECOMMENDATION_USER_TEMPLATE,
    CapabilityCard,
    LogicalMetadataError,
    MetadataColumn,
    RecommendationContext,
    build_recommendation_context,
    load_capability_cards,
    load_logical_metadata,
    recommend_capabilities,
    recommend_questions_chat,
)
from question_recommendation.recommender import _group_metadata_by_table, _parse_llm_response


def _network_interface_context(**overrides):
    data = {
        "intention": "查信息",
        "question": "查询网络设备接口",
        "device_types": ["网络设备"],
        "subcomponent_types": ["接口"],
    }
    data.update(overrides)
    return RecommendationContext.from_dict(data)


def test_build_context_keeps_only_consumed_fields():
    context = build_recommendation_context(
        {
            "intention": "查指标",
            "question": "查询 IP 以 10.1 开头的网络设备平均 CPU 利用率",
            "tenant": "租户A",
            "subnet": {"path": "/园区", "name": "生产网"},
            "devices": [
                {
                    "device_id": "10.1",
                    "id_type": "IP",
                    "match_mode": "PREFIX",
                    "device_type": "网络设备",
                }
            ],
            "subcomponents": [],
            "properties": [],
            "kpis": ["CPU利用率"],
            "time": "",
            "alarm": None,
            "link_relation": "AND",
            "agg": ["avg", "count(distinct)", "topN"],
            "tables": ["network_device", "network_device_metric"],
            "unknown": "ignored",
        },
        failure_reason="匹配到多个设备",
    )

    assert context.intention == "查指标"
    assert context.device_types == ["网络设备"]
    assert context.identifiers[0].to_dict() == {
        "value": "10.1",
        "id_type": "IP",
        "match_mode": "PREFIX",
    }
    assert context.aggregations == ["avg", "count_distinct", "top_n"]
    assert context.failure_type == "匹配到多设备"
    serialized = context.to_dict()
    assert "tenant" not in serialized
    assert "subnet" not in serialized
    assert "link_relation" not in serialized
    assert "unknown" not in serialized


def test_build_context_ignores_subcomponent_name():
    context = build_recommendation_context(
        {
            "intention": "查信息",
            "subcomponents": [
                {"subcomponent_name": "光模块-01", "subcomponent_type": "光模块"}
            ],
        }
    )
    assert context.subcomponent_types == ["光模块"]
    assert "光模块-01" not in context.to_json()


def test_context_json_round_trip():
    original = _network_interface_context(
        identifiers=[{"value": "10.0.0.1", "id_type": "IP", "match_mode": "EXACT"}],
        aggregations=["count"],
    )
    restored = RecommendationContext.from_json(original.to_json())
    assert restored.to_dict() == original.to_dict()


def test_invalid_device_identifier_is_removed():
    context = build_recommendation_context(
        {
            "intention": "查信息",
            "devices": [
                {
                    "device_id": "1.1.1.1",
                    "id_type": "IP",
                    "match_mode": "EXACT",
                    "device_type": "网络设备",
                }
            ],
        },
        failure_reason="未找到设备 IP 为 1.1.1.1",
    )
    assert context.failure_type == "对象定位失败"
    assert context.invalid_values == ["1.1.1.1"]
    assert context.identifiers == []


def test_object_location_failure_removes_identifier_even_if_reason_omits_value():
    context = build_recommendation_context(
        {
            "devices": [
                {
                    "device_id": "device-a",
                    "id_type": "NAME",
                    "match_mode": "EXACT",
                    "device_type": "网络设备",
                }
            ]
        },
        failure_reason="设备不存在",
    )
    assert context.invalid_values == ["device-a"]
    assert context.identifiers == []


def test_multi_device_prefix_is_not_invalidated():
    context = build_recommendation_context(
        {
            "devices": [
                {
                    "device_id": "10.1",
                    "id_type": "IP",
                    "match_mode": "PREFIX",
                    "device_type": "网络设备",
                }
            ]
        },
        failure_reason="IP 前缀匹配到多个设备",
    )
    assert context.failure_type == "匹配到多设备"
    assert context.invalid_values == []
    assert context.identifiers[0].value == "10.1"


def test_capability_configuration_is_valid():
    cards = load_capability_cards()
    ids = [card.capability_id for card in cards]
    assert len(cards) >= 25
    assert len(ids) == len(set(ids))
    assert all(card.domain and card.intent_type and card.objects for card in cards)
    assert all("/" not in question for card in cards for question in card.golden_questions)
    assert all(
        card.metric_policy.get("mode") in {"none", "allow", "dynamic", "dynamic_inherit"}
        for card in cards
    )
    assert all(
        card.attribute_policy.get("mode") in {"none", "allow", "dynamic", "dynamic_inherit"}
        for card in cards
    )
    assert not any("诊断" in question for card in cards for question in card.golden_questions)


def test_explicit_device_type_hard_filters_other_domains():
    context = RecommendationContext(
        intention="查信息",
        device_types=["网络设备"],
        subcomponent_types=["光模块"],
    )
    ranked = recommend_capabilities(context)
    ids = [item.card.capability_id for item in ranked]
    assert "network_optical_module_information" in ids
    assert "server_optical_module_information" not in ids


def test_missing_device_type_does_not_hard_filter_domain():
    context = RecommendationContext(
        intention="查信息",
        subcomponent_types=["光模块"],
    )
    ids = [item.card.capability_id for item in recommend_capabilities(context)]
    assert "network_optical_module_information" in ids
    assert "server_optical_module_information" in ids


def test_ambiguous_optical_module_keeps_supported_metric_and_other_domain_information():
    context = RecommendationContext(
        intention="查指标",
        question="查询光模块接收功率",
        subcomponent_types=["光模块"],
        kpis=["接收功率"],
        failure_type="业务域不明确",
    )
    ranked = recommend_capabilities(context)
    ids = [item.card.capability_id for item in ranked]
    assert "network_optical_module_metric" in ids
    assert "network_optical_module_information" in ids
    assert "server_optical_module_information" in ids
    assert all("interface" not in item for item in ids)


def test_unsupported_metric_filters_metric_card():
    context = RecommendationContext(
        intention="查指标",
        device_types=["网络设备"],
        subcomponent_types=["光模块"],
        kpis=["CPU利用率"],
    )
    ids = [item.card.capability_id for item in recommend_capabilities(context)]
    assert "network_optical_module_metric" not in ids


def test_metric_not_supported_failure_keeps_information_recovery_only():
    context = RecommendationContext(
        intention="查指标",
        device_types=["网络设备"],
        subcomponent_types=["光模块"],
        kpis=["CPU利用率"],
        failure_type="指标不支持",
        invalid_values=["CPU利用率"],
    )
    ranked = recommend_capabilities(context)
    assert ranked
    assert all(item.card.intent_type == "查信息" for item in ranked)


def test_tables_affect_score_but_not_hard_filter():
    context = RecommendationContext(
        intention="查信息",
        subcomponent_types=["光模块"],
        tables=["server_optical_module"],
    )
    metadata = [
        MetadataColumn(
            "server_optical_module",
            "服务器光模块",
            "name",
            "光模块名称",
        )
    ]
    ranked = recommend_capabilities(context, metadata=metadata)
    ids = [item.card.capability_id for item in ranked]
    assert "network_optical_module_information" in ids
    assert "server_optical_module_information" in ids
    server = next(item for item in ranked if item.card.capability_id == "server_optical_module_information")
    network = next(item for item in ranked if item.card.capability_id == "network_optical_module_information")
    assert server.match_score > network.match_score


def test_table_names_can_affect_score_without_loaded_metadata():
    context = RecommendationContext(
        intention="查信息",
        subcomponent_types=["光模块"],
        tables=["server_optical_module"],
    )
    ranked = recommend_capabilities(context)
    server = next(item for item in ranked if item.card.capability_id == "server_optical_module_information")
    network = next(item for item in ranked if item.card.capability_id == "network_optical_module_information")
    assert server.match_score > network.match_score


def test_top_twelve_selection_is_stable():
    context = RecommendationContext(intention="查信息")
    first = [item.card.capability_id for item in recommend_capabilities(context, limit=12)]
    second = [item.card.capability_id for item in recommend_capabilities(context, limit=12)]
    assert first == second
    assert len(first) == 12


def test_prompt_contains_minimal_context_and_ambiguity_rules():
    assert "recommendation_context" in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "candidate_capabilities" in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "业务域不明确" in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "invalid_values" in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "{recommendation_context_json}" in QUESTION_RECOMMENDATION_USER_TEMPLATE


def test_chat_recommendation_auto_loads_capabilities_and_metadata(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "yaml", SimpleNamespace(safe_load=lambda stream: json.load(stream)))
    (tmp_path / "network_interface.logical.yaml").write_text(
        json.dumps(
            {
                "name": "network_interface",
                "description_cn": "网络设备接口",
                "schema": {
                    "fields": [
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
                "recommends": ["查询网络设备接口列表"],
                "explain": "建议先查看接口列表。",
            },
            ensure_ascii=False,
        )
    )
    result = recommend_questions_chat(
        _network_interface_context(tables=["network_interface"]),
        llm_chat_client,
        logical_model_path_provider=lambda: tmp_path,
    )

    assert result["recommends"] == ["查询网络设备接口列表"]
    prompt = llm_chat_client.call_args[0][0][1]["content"]
    assert "network_interface_information" in prompt
    assert '"table_name": "network_interface"' in prompt
    assert "candidate_templates" not in prompt


def test_structurally_valid_llm_result_is_returned_without_content_filtering():
    llm_chat_client = MagicMock(
        return_value=json.dumps(
            {
                "recommends": ["重复问题", "重复问题"],
                "explain": "原样返回",
            },
            ensure_ascii=False,
        )
    )
    result = recommend_questions_chat(_network_interface_context(), llm_chat_client)
    assert result == {"recommends": ["重复问题", "重复问题"], "explain": "原样返回"}


def test_invalid_json_returns_empty_structure():
    result = recommend_questions_chat(
        _network_interface_context(),
        MagicMock(return_value="not json"),
    )
    assert result == {"recommends": [], "explain": ""}


def test_parse_markdown_wrapped_json():
    result = _parse_llm_response(
        '```json\n{"recommends": ["A"], "explain": "ok"}\n```'
    )
    assert result == {"recommends": ["A"], "explain": "ok"}


def test_metadata_columns_group_by_table():
    grouped = _group_metadata_by_table(
        [
            MetadataColumn("device", "设备", "name", "设备名称"),
            MetadataColumn("device", "设备", "ip", "设备IP地址"),
            MetadataColumn("metric", "设备性能指标", "cpu_usage", "CPU利用率"),
        ]
    )
    assert len(grouped) == 2
    assert len(grouped[0]["columns"]) == 2


def test_load_logical_metadata_skips_missing_and_unsafe_tables(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "yaml", SimpleNamespace(safe_load=lambda stream: json.load(stream)))
    (tmp_path / "device.logical.yaml").write_text(
        json.dumps(
            {
                "name": "device",
                "description_cn": "设备",
                "schema": {"fields": [{"name": "ip", "description_cn": "设备IP地址"}]},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    metadata = load_logical_metadata(["device", "missing", "../unsafe", "device"], lambda: tmp_path)
    assert [item.column_name for item in metadata] == ["ip"]


def test_load_logical_metadata_rejects_invalid_directory(tmp_path):
    try:
        load_logical_metadata(["device"], lambda: tmp_path / "missing")
    except LogicalMetadataError as exc:
        assert "不存在或不是目录" in str(exc)
    else:
        raise AssertionError("expected LogicalMetadataError")

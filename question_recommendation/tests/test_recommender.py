"""最小化上下文和能力卡推荐单元测试。"""

import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from query_errors import ErrorCode, ErrorInfo, ErrorLevel, ErrorStage

from question_recommendation import (
    QUESTION_RECOMMENDATION_SYSTEM_PROMPT,
    QUESTION_RECOMMENDATION_USER_TEMPLATE,
    CapabilityCard,
    LogicalMetadataError,
    MetadataColumn,
    MetadataTable,
    RecommendationContext,
    build_recommendation_context,
    load_capability_cards,
    load_logical_metadata,
    recommend_capabilities,
    recommend_questions_chat,
)
from question_recommendation.recommender import _parse_llm_response
from question_recommendation.refusal_rules import get_refusal_recovery_rule


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
        refuse_info=ErrorCode.VALUE_RETRIEVAL_IP_MULTIPLE_CANDIDATES.to_info(),
        llm_refuse_message="IP 前缀匹配到多个设备",
    )

    assert context.intention == "查指标"
    assert context.device_types == ["网络设备"]
    assert context.identifiers[0].to_dict() == {
        "value": "10.1",
        "id_type": "IP",
        "match_mode": "PREFIX",
    }
    assert context.aggregations == ["avg", "count_distinct", "top_n"]
    assert context.recovery_strategy == "disambiguate"
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
                },
                {"device_id": "device-a", "id_type": "NAME"},
                {"device_id": "00:11:22:33:44:55", "id_type": "MAC"},
            ],
        },
        refuse_info=ErrorCode.INTENT_GUIDE_DEVICE_NOT_FOUND.to_info(),
        llm_refuse_message="未找到设备 IP 为 1.1.1.1",
    )
    assert context.recovery_strategy == "remove_invalid"
    assert context.invalid_values == [
        "1.1.1.1",
        "device-a",
        "00:11:22:33:44:55",
    ]
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
        refuse_info=ErrorCode.INTENT_GUIDE_DEVICE_NOT_FOUND.to_info(),
        llm_refuse_message="设备不存在",
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
        refuse_info=ErrorCode.VALUE_RETRIEVAL_IP_MULTIPLE_CANDIDATES.to_info(),
        llm_refuse_message="IP 前缀匹配到多个设备",
    )
    assert context.recovery_strategy == "disambiguate"
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
    assert all(card.recovery_strategies for card in cards)
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


def test_disambiguation_still_respects_uniquely_confirmed_device_domain():
    context = RecommendationContext(
        intention="查指标",
        device_types=["网络设备"],
        subcomponent_types=["光模块"],
        kpis=["接收功率"],
        recovery_strategy="disambiguate",
    )
    ids = [item.card.capability_id for item in recommend_capabilities(context)]
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
        recovery_strategy="disambiguate",
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


def test_metric_not_found_removes_kpi_from_context():
    context = build_recommendation_context(
        {
            "intention": "查指标",
            "devices": [{"device_type": "网络设备"}],
            "subcomponents": [{"subcomponent_type": "光模块"}],
            "kpis": ["CPU利用率"],
        },
        refuse_info=ErrorCode.VALUE_RETRIEVAL_KPI_NOT_FOUND.to_info(),
    )
    assert context.kpis == []
    assert context.invalid_values == ["CPU利用率"]
    ranked = recommend_capabilities(context)
    assert ranked


def test_tables_affect_score_but_not_hard_filter():
    context = RecommendationContext(
        intention="查信息",
        subcomponent_types=["光模块"],
        tables=["server_optical_module"],
    )
    metadata = [
        MetadataTable(
            table_name="server_optical_module",
            table_description="服务器光模块",
            columns=[
                MetadataColumn(
                    column_name="name",
                    column_description="光模块名称",
                )
            ],
        )
    ]
    ranked = recommend_capabilities(context, metadata_tables=metadata)
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
    assert "disambiguate" in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "invalid_values" in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "{recommendation_context_json}" in QUESTION_RECOMMENDATION_USER_TEMPLATE


def test_prompt_requires_user_friendly_actionable_explanation():
    assert "直接展示给用户的下一步建议" in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "不责备用户" in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "不暴露错误码" in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "不包含 invalid_values 中的值" in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "50 个中文字符以内" in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    for strategy in (
        "basic",
        "clarify",
        "disambiguate",
        "remove_invalid",
        "reframe",
        "adjust_scope",
    ):
        assert f"- {strategy}：" in QUESTION_RECOMMENDATION_SYSTEM_PROMPT


def test_refuse_info_requires_shared_error_info():
    try:
        build_recommendation_context({}, refuse_info={"key": "intent_reject_non_query_intent"})
    except TypeError as exc:
        assert "query_errors.ErrorInfo" in str(exc)
    else:
        raise AssertionError("expected TypeError")


def test_llm_refuse_message_requires_string():
    try:
        build_recommendation_context({}, llm_refuse_message={"message": "detail"})
    except TypeError as exc:
        assert "字符串" in str(exc)
    else:
        raise AssertionError("expected TypeError")


def test_refuse_key_decides_strategy_not_messages():
    context = build_recommendation_context(
        {"devices": [{"device_id": "device-a", "id_type": "NAME"}]},
        refuse_info=ErrorInfo(
            key="value_retrieval_name_multiple_candidates",
            level=ErrorLevel.WARNING.value,
            stage=ErrorStage.VALUE_RETRIEVAL.value,
            message="随意标准说明",
        ),
        llm_refuse_message="未找到设备，这段文本不应改变分类",
    )
    assert context.recovery_strategy == "disambiguate"
    assert context.identifiers[0].value == "device-a"
    assert context.invalid_values == []


@pytest.mark.parametrize(
    ("error_code", "expected_strategy"),
    [
        (ErrorCode.INTENT_GUIDE_CROSS_DOMAIN_QUERY, "disambiguate"),
        (ErrorCode.INTENT_GUIDE_UNSUPPORTED_SUBNET_METRIC_QUERY, "reframe"),
        (ErrorCode.INTENT_CLARIFY_METRIC_MISSING, "clarify"),
        (ErrorCode.INTENT_CLARIFY_OBJECT_AMBIGUOUS, "disambiguate"),
        (ErrorCode.VALUE_RETRIEVAL_KPI_NOT_FOUND, "remove_invalid"),
        (ErrorCode.VALUE_RETRIEVAL_ALIAS_NORMALIZATION_FAILED, "reframe"),
        (ErrorCode.SQL_GENERATION_SCHEMA_MAPPING_FAILED, "reframe"),
        (ErrorCode.SQL_GENERATION_TIMEOUT, "adjust_scope"),
    ],
)
def test_classes_three_to_six_map_to_stable_recovery_strategies(
    error_code,
    expected_strategy,
):
    context = build_recommendation_context({}, refuse_info=error_code.to_info())
    assert context.recovery_strategy == expected_strategy


def test_unconfigured_clarification_defaults_to_clarify():
    rule = get_refusal_recovery_rule("intent_clarify_future_required_parameter")
    assert rule.strategy == "clarify"


def test_ip_not_found_only_removes_ip_identifier():
    context = build_recommendation_context(
        {
            "devices": [
                {"device_id": "1.1.1.1", "id_type": "IP"},
                {"device_id": "device-a", "id_type": "NAME"},
                {"device_id": "00:11:22:33:44:55", "id_type": "MAC"},
            ]
        },
        refuse_info=ErrorCode.VALUE_RETRIEVAL_IP_NOT_FOUND.to_info(),
    )
    assert context.invalid_values == ["1.1.1.1"]
    assert [item.value for item in context.identifiers] == [
        "device-a",
        "00:11:22:33:44:55",
    ]


def test_name_not_found_only_removes_name_identifier():
    context = build_recommendation_context(
        {
            "devices": [
                {"device_id": "1.1.1.1", "id_type": "IP"},
                {"device_id": "device-a", "id_type": "NAME"},
            ]
        },
        refuse_info=ErrorCode.VALUE_RETRIEVAL_NAME_NOT_FOUND.to_info(),
    )
    assert context.invalid_values == ["device-a"]
    assert [item.value for item in context.identifiers] == ["1.1.1.1"]


def test_kpi_multiple_candidates_keeps_original_kpi():
    context = build_recommendation_context(
        {"kpis": ["CPU利用率"]},
        refuse_info=ErrorCode.VALUE_RETRIEVAL_KPI_MULTIPLE_CANDIDATES.to_info(),
    )
    assert context.recovery_strategy == "disambiguate"
    assert context.kpis == ["CPU利用率"]
    assert context.invalid_values == []


def test_unknown_error_and_llm_message_only_use_basic_strategy():
    unknown = build_recommendation_context(
        {"intention": "查指标", "device_types": ["网络设备"]},
        refuse_info=ErrorInfo(
            key="future_new_error",
            level="warning",
            stage="intent",
            message="new",
        ),
    )
    detail_only = build_recommendation_context({}, llm_refuse_message="详细原因")
    assert unknown.recovery_strategy == "basic"
    assert detail_only.recovery_strategy == "basic"


def test_no_refusal_keeps_normal_recommendation():
    context = build_recommendation_context({"intention": "查信息"})
    assert context.recovery_strategy == ""
    assert context.refusal_message == ""
    assert context.refusal_detail == ""


def test_intent_reject_only_selects_basic_information_cards():
    context = build_recommendation_context(
        {
            "intention": "查指标",
            "devices": [{"device_type": "网络设备"}],
            "kpis": ["CPU利用率"],
        },
        refuse_info=ErrorCode.INTENT_REJECT_NON_QUERY_INTENT.to_info(),
    )
    ranked = recommend_capabilities(context)
    assert context.recovery_strategy == "basic"
    assert ranked
    assert all(item.card.intent_type == "查信息" for item in ranked)
    allowed_forms = {"列表", "数量", "基础信息", "属性信息", "概览"}
    assert all(allowed_forms.intersection(item.card.result_forms) for item in ranked)


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


def test_metadata_table_serializes_grouped_columns():
    metadata = MetadataTable(
        table_name="device",
        table_description="设备",
        columns=[
            MetadataColumn("name", "设备名称"),
            MetadataColumn("ip", "设备IP地址"),
        ],
    )
    assert metadata.to_dict() == {
        "table_name": "device",
        "table_description": "设备",
        "columns": [
            {"column_name": "name", "column_description": "设备名称"},
            {"column_name": "ip", "column_description": "设备IP地址"},
        ],
    }


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
    assert [table.to_dict() for table in metadata] == [
        {
            "table_name": "device",
            "table_description": "设备",
            "columns": [
                {
                    "column_name": "ip",
                    "column_description": "设备IP地址",
                }
            ],
        }
    ]


def test_load_logical_metadata_returns_one_group_per_table(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "yaml", SimpleNamespace(safe_load=lambda stream: json.load(stream)))
    for table_name, description in (("device", "设备"), ("metric", "设备指标")):
        (tmp_path / f"{table_name}.logical.yaml").write_text(
            json.dumps(
                {
                    "name": table_name,
                    "description_cn": description,
                    "schema": {
                        "fields": [
                            {"name": "first", "description_cn": "字段一"},
                            {"name": "second", "description_cn": "字段二"},
                        ]
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    metadata = load_logical_metadata(["device", "metric"], lambda: tmp_path)

    assert [table.table_name for table in metadata] == ["device", "metric"]
    assert [len(table.columns) for table in metadata] == [2, 2]


def test_load_logical_metadata_rejects_invalid_directory(tmp_path):
    try:
        load_logical_metadata(["device"], lambda: tmp_path / "missing")
    except LogicalMetadataError as exc:
        assert "不存在或不是目录" in str(exc)
    else:
        raise AssertionError("expected LogicalMetadataError")

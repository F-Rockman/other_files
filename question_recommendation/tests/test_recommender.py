"""最小化上下文、六类能力规格和推荐调用器单元测试。"""

import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from query_errors import ErrorCode, ErrorInfo, ErrorLevel, ErrorStage

from question_recommendation import (
    QUESTION_RECOMMENDATION_SYSTEM_PROMPT,
    QUESTION_RECOMMENDATION_USER_TEMPLATE,
    DEVICE_COUNT,
    DEVICE_INFO,
    DEVICE_METRIC,
    SUBCOMPONENT_COUNT,
    SUBCOMPONENT_INFO,
    SUBCOMPONENT_METRIC,
    LogicalMetadataError,
    MetadataColumn,
    MetadataTable,
    RecommendationContext,
    build_recommendation_context,
    load_device_capability_profiles,
    load_special_capabilities,
    load_logical_metadata,
    recommend_capabilities,
    recommend_questions_chat,
    resolve_primary_capability_type,
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


def _candidate_ids(context, **kwargs):
    return [item.candidate.capability_id for item in recommend_capabilities(context, **kwargs)]


def test_capability_configuration_is_valid():
    profiles = load_device_capability_profiles()
    specials = load_special_capabilities()
    profile_ids = [profile.profile_id for profile in profiles]
    assert len(profiles) >= 8
    assert len(profile_ids) == len(set(profile_ids))
    assert all(profile.domain and profile.device_types for profile in profiles)
    assert all(profile.locators for profile in profiles)
    assert not any("诊断" in question for profile in profiles for question in profile.examples)
    assert not any(
        "诊断" in question
        for profile in profiles
        for spec in profile.subcomponents
        for question in spec.examples
    )
    assert not any("诊断" in question for spec in specials for question in spec.examples)
    assert {item.capability_type for item in specials} == {
        "alarm_query",
        "link_query",
        "resource_query",
        "relation_query",
    }


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        (RecommendationContext(intention="查信息"), DEVICE_INFO),
        (
            RecommendationContext(intention="查信息", aggregations=["count_distinct"]),
            DEVICE_COUNT,
        ),
        (RecommendationContext(intention="查指标"), DEVICE_METRIC),
        (
            RecommendationContext(intention="查信息", subcomponent_types=["接口"]),
            SUBCOMPONENT_INFO,
        ),
        (
            RecommendationContext(
                intention="查信息",
                subcomponent_types=["接口"],
                aggregations=["count"],
            ),
            SUBCOMPONENT_COUNT,
        ),
        (
            RecommendationContext(intention="查指标", subcomponent_types=["接口"]),
            SUBCOMPONENT_METRIC,
        ),
    ],
)
def test_six_skeletons_route_by_intent_subcomponent_and_count(context, expected):
    assert resolve_primary_capability_type(context) == expected


def test_explicit_device_type_hard_filters_other_domains():
    context = RecommendationContext(
        intention="查信息",
        device_types=["网络设备"],
        subcomponent_types=["光模块"],
    )
    ids = _candidate_ids(context)
    assert "network_device:光模块:subcomponent_info" in ids
    assert not any(item.startswith("server:光模块") for item in ids)


def test_missing_device_type_keeps_compatible_parent_domains():
    context = RecommendationContext(intention="查信息", subcomponent_types=["光模块"])
    ids = _candidate_ids(context)
    assert "network_device:光模块:subcomponent_info" in ids
    assert "server:光模块:subcomponent_info" in ids


def test_unsupported_parent_child_relation_returns_no_candidate():
    context = RecommendationContext(
        intention="查信息",
        device_types=["服务器"],
        subcomponent_types=["端口"],
    )
    assert recommend_capabilities(context) == []


def test_server_nic_and_network_port_are_separate_capabilities():
    server_ids = _candidate_ids(
        RecommendationContext(
            intention="查信息",
            device_types=["服务器"],
            subcomponent_types=["网卡"],
        )
    )
    network_ids = _candidate_ids(
        RecommendationContext(
            intention="查信息",
            device_types=["网络设备"],
            subcomponent_types=["端口"],
        )
    )
    assert "server:网卡:subcomponent_info" in server_ids
    assert "network_device:接口:subcomponent_info" in network_ids
    assert not any("端口" in item for item in server_ids)


def test_serial_number_is_property_and_filter_but_not_locator():
    profiles = load_device_capability_profiles()
    for profile_id in ("server", "storage_device"):
        profile = next(item for item in profiles if item.profile_id == profile_id)
        assert "序列号" in profile.properties
        assert "序列号" in profile.filter_fields
        assert "SERIAL" not in profile.locators


@pytest.mark.parametrize("subcomponent", ["存储池", "LUN", "文件系统"])
def test_storage_resources_use_subcomponent_skeleton(subcomponent):
    context = RecommendationContext(
        intention="查信息",
        device_types=["存储设备"],
        subcomponent_types=[subcomponent],
    )
    assert resolve_primary_capability_type(context) == SUBCOMPONENT_INFO
    assert f"storage_device:{subcomponent}:subcomponent_info" in _candidate_ids(context)


def test_fan_metrics_only_support_trend():
    trend = RecommendationContext(
        intention="查指标",
        question="查询服务器风扇转速趋势",
        device_types=["服务器"],
        subcomponent_types=["风扇"],
        kpis=["风扇转速"],
    )
    average = RecommendationContext(
        intention="查指标",
        question="查询服务器风扇转速平均值",
        device_types=["服务器"],
        subcomponent_types=["风扇"],
        kpis=["风扇转速"],
        aggregations=["avg"],
    )
    current = RecommendationContext(
        intention="查指标",
        question="查询服务器风扇转速",
        device_types=["服务器"],
        subcomponent_types=["风扇"],
        kpis=["风扇转速"],
    )
    assert "server:风扇:subcomponent_metric" in _candidate_ids(trend)
    assert "server:风扇:subcomponent_metric" not in _candidate_ids(average)
    assert "server:风扇:subcomponent_metric" not in _candidate_ids(current)


def test_storage_total_capacity_only_supports_current_value():
    current = RecommendationContext(
        intention="查指标",
        question="查询存储设备总容量",
        device_types=["存储设备"],
        kpis=["总容量"],
    )
    trend = RecommendationContext(
        intention="查指标",
        question="查询存储设备总容量趋势",
        device_types=["存储设备"],
        kpis=["总容量"],
    )
    assert "storage_device:device_metric" in _candidate_ids(current)
    assert "storage_device:device_metric" not in _candidate_ids(trend)


def test_topn_requires_explicit_n_and_direction():
    incomplete = RecommendationContext(
        intention="查指标",
        question="查询CPU利用率最高的服务器",
        device_types=["服务器"],
        kpis=["CPU利用率"],
        aggregations=["top_n"],
    )
    complete = RecommendationContext(
        intention="查指标",
        question="查询CPU利用率最高的Top5服务器",
        device_types=["服务器"],
        kpis=["CPU利用率"],
        aggregations=["top_n"],
    )
    assert "server:device_metric" not in _candidate_ids(incomplete)
    assert "server:device_metric" in _candidate_ids(complete)


@pytest.mark.parametrize(
    ("context", "expected_id"),
    [
        (RecommendationContext(intention="查告警", device_types=["服务器"]), "alarm_query"),
        (RecommendationContext(intention="查链路", device_types=["网络设备"]), "network_link"),
        (
            RecommendationContext(
                intention="查信息",
                question="查询OLT下的ONU",
                device_types=["OLT"],
            ),
            "olt_onu_relation",
        ),
        (
            RecommendationContext(
                intention="查信息",
                device_types=["子网"],
            ),
            "subnet_resource",
        ),
    ],
)
def test_special_capabilities_are_preserved(context, expected_id):
    assert expected_id in _candidate_ids(context)


def test_unsupported_metric_filters_metric_candidate():
    context = RecommendationContext(
        intention="查指标",
        device_types=["网络设备"],
        subcomponent_types=["光模块"],
        kpis=["CPU利用率"],
    )
    assert "network_device:光模块:subcomponent_metric" not in _candidate_ids(context)


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
    ids = [item.candidate.capability_id for item in ranked]
    assert "network_device:光模块:subcomponent_info" in ids
    assert "server:光模块:subcomponent_info" in ids
    server = next(item for item in ranked if item.candidate.capability_id == "server:光模块:subcomponent_info")
    network = next(item for item in ranked if item.candidate.capability_id == "network_device:光模块:subcomponent_info")
    assert server.match_score > network.match_score


def test_table_names_can_affect_score_without_loaded_metadata():
    context = RecommendationContext(
        intention="查信息",
        subcomponent_types=["光模块"],
        tables=["server_optical_module"],
    )
    ranked = recommend_capabilities(context)
    server = next(item for item in ranked if item.candidate.capability_id == "server:光模块:subcomponent_info")
    network = next(item for item in ranked if item.candidate.capability_id == "network_device:光模块:subcomponent_info")
    assert server.match_score > network.match_score


def test_top_twelve_selection_is_stable():
    context = RecommendationContext(intention="查信息")
    first = [item.candidate.capability_id for item in recommend_capabilities(context, limit=12)]
    second = [item.candidate.capability_id for item in recommend_capabilities(context, limit=12)]
    assert first == second
    assert len(first) == 12


def test_prompt_contains_minimal_context_and_ambiguity_rules():
    assert "recommendation_context" in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "candidate_capabilities" in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "disambiguate" in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "invalid_values" in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "设备与子部件兼容关系" not in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "candidate_templates" not in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "诊断、异常原因分析" in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "description_cn 明确提供的枚举" in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "不能扩大候选能力" in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "{recommendation_context_json}" in QUESTION_RECOMMENDATION_USER_TEMPLATE


def test_prompt_requires_user_friendly_actionable_explanation():
    assert "直接展示给用户的友好下一步建议" in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "不责备用户" in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "不复述 invalid_values" in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "80 个中文字符以内" in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "候选能力不足时允许少于 3 条" in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    for strategy in (
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
    assert all(
        item.candidate.capability_type in {DEVICE_INFO, DEVICE_COUNT}
        for item in ranked
    )
    assert all(not item.candidate.metrics for item in ranked)


def test_basic_subcomponent_prefers_child_info_count_and_parent_info():
    context = RecommendationContext(
        intention="查指标",
        device_types=["网络设备"],
        subcomponent_types=["光模块"],
        kpis=["接收功率"],
        time="近七天",
        aggregations=["avg"],
        recovery_strategy="basic",
    )
    candidates = [item.candidate for item in recommend_capabilities(context)]
    assert [item.capability_type for item in candidates] == [
        SUBCOMPONENT_INFO,
        SUBCOMPONENT_COUNT,
        DEVICE_INFO,
    ]
    assert all(not item.metrics and "趋势" not in item.result_forms for item in candidates)
    assert all(
        not item.properties and not item.filter_fields and not item.group_by_fields
        for item in candidates
    )
    assert not any("趋势" in example for item in candidates for example in item.examples)


def test_basic_without_object_provides_global_device_basics():
    candidates = [
        item.candidate
        for item in recommend_capabilities(
            RecommendationContext(recovery_strategy="basic"),
            limit=12,
        )
    ]
    assert candidates
    assert all(item.capability_type in {DEVICE_INFO, DEVICE_COUNT} for item in candidates)


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
    assert "network_device:接口:subcomponent_info" in prompt
    assert '"table_name": "network_interface"' in prompt
    assert "candidate_templates" not in prompt


def test_basic_prompt_hides_non_inheritable_query_conditions():
    llm_chat_client = MagicMock(
        return_value='{"recommends": [], "explain": "建议先查看设备基础信息。"}'
    )
    recommend_questions_chat(
        RecommendationContext(
            intention="查指标",
            question="查询近七天网络设备CPU利用率平均值",
            device_types=["网络设备"],
            kpis=["CPU利用率"],
            properties=["状态"],
            time="近七天",
            aggregations=["avg"],
            recovery_strategy="basic",
            refusal_detail="当前条件无法直接查询CPU利用率",
        ),
        llm_chat_client,
    )
    prompt = llm_chat_client.call_args[0][0][1]["content"]
    assert "CPU利用率" not in prompt
    assert "近七天" not in prompt
    assert '"avg"' not in prompt
    assert '"状态"' not in prompt
    assert "当前条件无法直接查询" not in prompt
    assert "network_device:device_info" in prompt


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

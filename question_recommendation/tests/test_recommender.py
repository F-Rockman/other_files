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
    SubnetScope,
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
    assert context.subnet.to_dict() == {"path": "/园区", "name": "生产网"}
    serialized = context.to_dict()
    assert "tenant" not in serialized
    assert serialized["subnet"] == {"path": "/园区", "name": "生产网"}
    assert "link_relation" not in serialized
    assert "unknown" not in serialized


@pytest.mark.parametrize("subnet", [None, "", [], {}, {"path": " ", "name": ""}])
def test_build_context_ignores_empty_or_invalid_subnet(subnet):
    context = build_recommendation_context({"subnet": subnet})
    assert context.subnet is None


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
        subnet={"path": "根子网", "name": "127网段"},
        aggregations=["count"],
    )
    restored = RecommendationContext.from_json(original.to_json())
    assert restored.to_dict() == original.to_dict()
    assert restored.subnet == SubnetScope(path="根子网", name="127网段")


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


def _empty_intention_basic_context(question, llm_refuse_message=""):
    return build_recommendation_context(
        {"question": question},
        refuse_info=ErrorCode.INTENT_REJECT_OUT_OF_SCOPE_QUERY.to_info(),
        llm_refuse_message=llm_refuse_message,
    )


def test_capability_configuration_is_valid():
    profiles = load_device_capability_profiles()
    specials = load_special_capabilities()
    profile_ids = [profile.profile_id for profile in profiles]
    assert len(profiles) >= 8
    assert len(profile_ids) == len(set(profile_ids))
    assert all(profile.domain and profile.device_types for profile in profiles)
    assert all(profile.locators for profile in profiles)
    assert all(isinstance(metric, str) for profile in profiles for metric in profile.metrics)
    assert all(
        isinstance(metric, str)
        for profile in profiles
        for spec in profile.subcomponents
        for metric in spec.metrics
    )
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
    removed_fields = {
        "filter_fields",
        "group_by_fields",
        "allowed_operations",
        "result_forms",
        "parent_device_type",
        "match_reasons",
    }
    assert not removed_fields.intersection(profiles[0].to_dict())
    assert not removed_fields.intersection(specials[0].to_dict())


def test_device_profiles_reflect_cross_domain_device_classification():
    profiles = {profile.profile_id: profile for profile in load_device_capability_profiles()}
    assert profiles["fc_switch"].domain == "存储"
    assert {"WAC", "防火墙", "FATAP"}.issubset(profiles["network_device"].aliases)
    assert "ap" not in profiles
    assert profiles["fitap"].domain == "无线"
    assert profiles["fitap"].device_types == ["FITAP"]
    assert {"AP", "无线接入点"}.issubset(profiles["fitap"].aliases)
    assert all(
        "PON设备" in profiles[profile_id].aliases
        for profile_id in ("olt", "onu")
    )


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


def test_serial_number_is_property_but_not_locator():
    profiles = load_device_capability_profiles()
    for profile_id in ("server", "storage_device"):
        profile = next(item for item in profiles if item.profile_id == profile_id)
        assert "序列号" in profile.properties
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


@pytest.mark.parametrize(
    "context",
    [
        RecommendationContext(
            intention="查指标",
            question="查询服务器风扇转速",
            device_types=["服务器"],
            subcomponent_types=["风扇"],
            kpis=["风扇转速"],
        ),
        RecommendationContext(
            intention="查指标",
            question="查询服务器风扇转速平均值",
            device_types=["服务器"],
            subcomponent_types=["风扇"],
            kpis=["风扇转速"],
            aggregations=["avg"],
        ),
        RecommendationContext(
            intention="查指标",
            question="查询存储设备总容量趋势",
            device_types=["存储设备"],
            kpis=["总容量"],
        ),
        RecommendationContext(
            intention="查指标",
            question="查询CPU利用率最高的服务器",
            device_types=["服务器"],
            kpis=["CPU利用率"],
            aggregations=["top_n"],
        ),
    ],
)
def test_metric_query_form_does_not_filter_named_metric(context):
    assert any(
        item.candidate.capability_type in {DEVICE_METRIC, SUBCOMPONENT_METRIC}
        for item in recommend_capabilities(context)
    )


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


def test_subnet_scope_keeps_device_info_primary_and_adds_relation_candidate():
    context = RecommendationContext(
        intention="查信息",
        question="查询根子网下127网段的存储设备列表",
        device_types=["存储设备"],
        subnet=SubnetScope(path="根子网", name="127网段"),
    )
    ranked = recommend_capabilities(context)
    ids = [item.candidate.capability_id for item in ranked]
    assert resolve_primary_capability_type(context) == DEVICE_INFO
    assert "storage_device:device_info" in ids
    assert "subnet_relation" in ids
    assert ids.index("storage_device:device_info") < ids.index("subnet_relation")


def test_subnet_scope_does_not_change_device_candidate_score():
    without_subnet = RecommendationContext(
        intention="查信息",
        question="查询存储设备列表",
        device_types=["存储设备"],
    )
    with_subnet = RecommendationContext(
        intention="查信息",
        question="查询存储设备列表",
        device_types=["存储设备"],
        subnet=SubnetScope(path="根子网", name="127网段"),
    )
    without_ranked = recommend_capabilities(without_subnet)
    with_ranked = recommend_capabilities(with_subnet)
    without_score = next(
        item.match_score
        for item in without_ranked
        if item.candidate.capability_id == "storage_device:device_info"
    )
    with_score = next(
        item.match_score
        for item in with_ranked
        if item.candidate.capability_id == "storage_device:device_info"
    )
    assert without_score == with_score
    assert "subnet_relation" not in [
        item.candidate.capability_id for item in without_ranked
    ]


def test_subnet_scope_adds_relation_without_changing_metric_primary():
    context = RecommendationContext(
        intention="查指标",
        question="查询根子网下127网段的存储设备CPU利用率",
        device_types=["存储设备"],
        subnet=SubnetScope(path="根子网", name="127网段"),
        kpis=["CPU利用率"],
    )
    ranked = recommend_capabilities(context)
    ids = [item.candidate.capability_id for item in ranked]
    assert ranked[0].candidate.capability_id == "storage_device:device_metric"
    assert "subnet_relation" in ids


def test_subnet_scope_does_not_add_incompatible_relation_candidate():
    context = RecommendationContext(
        intention="查信息",
        device_types=["未知设备"],
        subnet=SubnetScope(path="根子网", name="127网段"),
    )
    assert "subnet_relation" not in _candidate_ids(context)


def test_subnet_special_capabilities_have_no_fixed_domain():
    subnet_capabilities = [
        spec
        for spec in load_special_capabilities()
        if spec.capability_id in {"subnet_resource", "subnet_relation"}
    ]
    assert len(subnet_capabilities) == 2
    assert all(not spec.domain for spec in subnet_capabilities)
    assert all("domain" not in spec.to_dict() for spec in subnet_capabilities)
    resource = next(
        item
        for item in recommend_capabilities(
            RecommendationContext(intention="查信息", device_types=["子网"])
        )
        if item.candidate.capability_id == "subnet_resource"
    )
    assert "domain" not in resource.to_dict()


@pytest.mark.parametrize(
    "device_type",
    [
        "网络设备",
        "路由器",
        "WAC",
        "防火墙",
        "FATAP",
        "存储设备",
        "FC交换机",
        "服务器",
        "OLT",
        "ONU",
        "PON设备",
        "FITAP",
        "AP",
        "终端设备",
        "终端",
    ],
)
def test_subnet_relation_supports_cross_domain_device_types_and_aliases(device_type):
    context = RecommendationContext(
        intention="查信息",
        device_types=[device_type],
        subnet=SubnetScope(path="根子网", name="生产网"),
    )
    ranked = recommend_capabilities(context)
    relation = next(
        item.candidate
        for item in ranked
        if item.candidate.capability_id == "subnet_relation"
    )
    assert relation.device_types == [device_type]


def test_unsupported_metric_filters_metric_candidate():
    context = RecommendationContext(
        intention="查指标",
        device_types=["网络设备"],
        subcomponent_types=["光模块"],
        kpis=["CPU利用率"],
    )
    assert "network_device:光模块:subcomponent_metric" not in _candidate_ids(context)


def test_property_match_adds_score_and_property_miss_does_not_filter():
    matched = recommend_capabilities(
        RecommendationContext(
            intention="查信息",
            device_types=["网络设备"],
            properties=["状态"],
        )
    )
    missed = recommend_capabilities(
        RecommendationContext(
            intention="查信息",
            device_types=["网络设备"],
            properties=["不存在属性"],
        )
    )
    matched_info = next(
        item for item in matched if item.candidate.capability_id == "network_device:device_info"
    )
    missed_info = next(
        item for item in missed if item.candidate.capability_id == "network_device:device_info"
    )
    assert matched_info.match_score > missed_info.match_score


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
    assert "metrics：该对象可查询的 KPI 名称" in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    for removed in (
        "filter_fields",
        "group_by_fields",
        "allowed_operations",
        "result_forms",
        "parent_device_type",
        "match_score",
        "match_reasons",
    ):
        assert removed not in QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "{recommendation_context_json}" in QUESTION_RECOMMENDATION_USER_TEMPLATE


def test_prompt_requires_valid_subnet_scope_inheritance():
    prompt = QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "subnet 是设备或子部件查询的有效范围条件" in prompt
    assert "延续原设备或子部件对象的推荐必须继承有效子网范围" in prompt
    assert "根子网下127网段的存储设备" in prompt
    assert "path 和 name 必须逐字继承" in prompt
    assert "subnet.path 或 subnet.name 出现在 invalid_values 中时" in prompt
    assert "只有 resource_query 或 relation_query 候选才能把子网本身作为主要查询对象" in prompt


def test_prompt_requires_user_friendly_actionable_explanation():
    prompt = QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "直接展示给用户的完整、友好推荐说明" in prompt
    assert "不限制字数" in prompt
    assert "先说明当前提问是什么" in prompt
    assert "说明当前问题" in prompt
    assert "再说明推荐按什么方向进行" in prompt
    assert "推荐方向必须与 recommends 中实际给出的问题一致" in prompt
    assert "80 个中文字符以内" not in prompt
    assert "不责备用户" in prompt
    assert "不复述 invalid_values" in prompt
    assert "先定位，再收敛" in prompt
    assert "basic 是通用 error 兜底策略" in prompt
    assert "必须输出正好 3 条推荐" in prompt
    for strategy in (
        "clarify",
        "disambiguate",
        "remove_invalid",
        "reframe",
        "adjust_scope",
    ):
        assert f"- {strategy}：" in prompt


def test_prompt_preserves_object_context_in_unmatched_scenarios():
    prompt = QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "未匹配场景的委婉表达" in prompt
    assert "recommendation_context.device_types 恰好包含一个设备类型" in prompt
    assert "refusal_message 或 refusal_detail" in prompt
    assert "逐字使用" in prompt
    assert "recommendation_context.device_types[0]" in prompt
    assert "candidate_capabilities.device_types" in prompt
    assert "标准类型、父类或更泛化名称替换" in prompt
    assert "必须保留父子关系" in prompt
    assert '“{recommendation_context.device_types[0]}的{明确子部件}”' in prompt
    assert "包含多个设备类型时，不得将未匹配问题归因于" in prompt
    assert "没有明确设备类型时，不得虚构设备类型或业务对象" in prompt
    assert "不得复述 invalid_values" in prompt
    assert "位于 invalid_values 时不得点名复述" in prompt
    assert "设备类型不是设备定位值，可以按上述规则保留" in prompt
    assert "后半段必须结合 recommends 中实际问题说明推荐方向" in prompt


def test_prompt_uses_polite_wording_for_unmatched_scenarios():
    prompt = QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "当前环境暂未匹配到对应的{设备类型}" in prompt
    assert "当前可查询的{对象}信息中，暂未匹配到“{属性}”相关内容" in prompt
    assert "当前环境中暂未采集到{对象}的“{指标}”相关数据" in prompt
    assert "当前查询涉及多个设备类型，暂未匹配到相关信息" in prompt
    assert "当前条件暂未匹配到合适的业务取值" in prompt
    assert "当前环境暂未识别到相关对象之间的可用关联" in prompt
    assert "当前查询条件下暂未查询到相关数据" in prompt


def test_prompt_forbids_direct_negative_wording():
    prompt = QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "禁止在面向用户的 explain 中使用" in prompt
    for wording in (
        "设备不存在",
        "字段不存在",
        "{对象}没有该属性",
        "{对象}没有该指标",
        "不支持查询该字段",
        "不支持查询该指标",
        "暂不支持该查询",
    ):
        assert f"“{wording}”" in prompt


def test_prompt_polite_examples_cover_device_subcomponent_and_multiple_types():
    prompt = QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "暂未采集到网络设备的" in prompt
    assert "当前可查询的服务器风扇信息中暂未匹配到" in prompt
    assert "当前可查询的闪存存储信息中暂未匹配到" in prompt
    assert "不得将“闪存存储”改写为“存储设备”" in prompt
    assert "当前提问涉及多个设备类型，暂未匹配到相关信息" in prompt


def test_prompt_allows_only_unique_similar_metadata_replacement():
    prompt = QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "唯一相似查询项替换" in prompt
    assert "只有一个冲突属性或指标时才继续" in prompt
    assert "metadata_tables.columns[].column_description" in prompt
    assert "一个唯一、明确相似的业务描述" in prompt
    assert "多个相似项无法明确区分" in prompt
    assert "仅替换唯一冲突属性或指标" in prompt
    assert "相似替换最多占一条推荐" in prompt
    assert "物理列名、表名或“字段”概念" in prompt
    assert "除唯一相似查询项替换外，三条都必须在候选能力边界内" in prompt


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


def test_intent_reject_basic_uses_normal_recall_and_ranking():
    context = build_recommendation_context(
        {
            "intention": "查指标",
            "devices": [{"device_type": "网络设备"}],
            "kpis": ["CPU利用率"],
        },
        refuse_info=ErrorCode.INTENT_REJECT_NON_QUERY_INTENT.to_info(),
    )
    normal_context = RecommendationContext.from_dict(
        {
            key: value
            for key, value in context.to_dict().items()
            if key != "recovery_strategy"
        }
    )
    ranked = recommend_capabilities(context)
    normal_ranked = recommend_capabilities(normal_context)
    assert context.recovery_strategy == "basic"
    assert [item.to_dict() for item in ranked] == [item.to_dict() for item in normal_ranked]
    assert ranked[0].candidate.capability_type == DEVICE_METRIC


def test_basic_subcomponent_uses_normal_recall_and_keeps_metric_context():
    normal_context = RecommendationContext(
        intention="查指标",
        device_types=["网络设备"],
        subcomponent_types=["光模块"],
        kpis=["接收功率"],
        time="近七天",
        aggregations=["avg"],
    )
    basic_context = RecommendationContext.from_dict(
        {**normal_context.to_dict(), "recovery_strategy": "basic"}
    )
    normal_ranked = recommend_capabilities(normal_context)
    basic_ranked = recommend_capabilities(basic_context)
    assert [item.to_dict() for item in basic_ranked] == [
        item.to_dict() for item in normal_ranked
    ]
    assert basic_ranked[0].candidate.capability_type == SUBCOMPONENT_METRIC
    assert basic_ranked[0].candidate.metrics[0] == "接收功率"


@pytest.mark.parametrize(
    "normal_context",
    [
        RecommendationContext(intention="查告警", device_types=["服务器"]),
        RecommendationContext(intention="查链路", device_types=["网络设备"]),
    ],
)
def test_basic_special_intents_use_normal_recall_and_ranking(normal_context):
    basic_context = RecommendationContext.from_dict(
        {**normal_context.to_dict(), "recovery_strategy": "basic"}
    )
    assert [
        item.to_dict() for item in recommend_capabilities(basic_context)
    ] == [
        item.to_dict() for item in recommend_capabilities(normal_context)
    ]


def test_basic_without_compatible_candidate_falls_back_to_global_device_basics():
    basic_context = RecommendationContext(
        intention="查指标",
        device_types=["未知设备"],
        kpis=["未知指标"],
        recovery_strategy="basic",
    )
    normal_context = RecommendationContext.from_dict(
        {
            key: value
            for key, value in basic_context.to_dict().items()
            if key != "recovery_strategy"
        }
    )
    assert recommend_capabilities(normal_context) == []
    candidates = [
        item.candidate
        for item in recommend_capabilities(
            basic_context,
            limit=12,
        )
    ]
    assert candidates
    assert all(item.capability_type in {DEVICE_INFO, DEVICE_COUNT} for item in candidates)


def test_empty_intention_basic_device_object_only_recalls_device_basics():
    context = _empty_intention_basic_context("查询名称为的网络设备")
    assert set(_candidate_ids(context)) == {
        "network_device:device_info",
        "network_device:device_count",
    }


def test_empty_intention_basic_special_object_only_recalls_special_capability():
    context = _empty_intention_basic_context("查询名称的告警")
    assert _candidate_ids(context) == ["alarm_query"]


def test_empty_intention_basic_device_constrains_special_capability():
    context = _empty_intention_basic_context("查询服务器告警")
    ranked = recommend_capabilities(context)
    assert [item.candidate.capability_id for item in ranked] == ["alarm_query"]
    assert ranked[0].candidate.device_types == ["服务器"]


def test_empty_intention_basic_subcomponent_recalls_compatible_parent_basics():
    context = _empty_intention_basic_context("查询光模块信息")
    assert set(_candidate_ids(context)) == {
        "network_device:光模块:subcomponent_info",
        "network_device:光模块:subcomponent_count",
        "server:光模块:subcomponent_info",
        "server:光模块:subcomponent_count",
    }


@pytest.mark.parametrize("question", ["查询名称为", "查询状态"])
def test_empty_intention_basic_attribute_words_keep_global_fallback(question):
    candidates = [
        item.candidate for item in recommend_capabilities(_empty_intention_basic_context(question))
    ]
    assert len(candidates) > 2
    assert all(item.capability_type in {DEVICE_INFO, DEVICE_COUNT} for item in candidates)
    assert len({tuple(item.device_types) for item in candidates}) > 1


def test_empty_intention_basic_object_matching_ignores_refusal_message():
    context = _empty_intention_basic_context(
        "查询名称的告警",
        llm_refuse_message="网络设备名称缺失",
    )
    assert _candidate_ids(context) == ["alarm_query"]


def test_empty_intention_basic_prefers_fc_switch_over_shorter_switch_alias():
    ranked = recommend_capabilities(_empty_intention_basic_context("查询FC交换机列表"))
    assert {item.candidate.capability_id for item in ranked} == {
        "fc_switch:device_info",
        "fc_switch:device_count",
    }
    assert all(item.candidate.domain == "存储" for item in ranked)


def test_empty_intention_basic_keeps_separate_explicit_device_objects():
    ids = set(
        _candidate_ids(_empty_intention_basic_context("查询服务器和网络设备列表"))
    )
    assert {"server:device_info", "network_device:device_info"}.issubset(ids)


@pytest.mark.parametrize(
    ("question", "expected_ids"),
    [
        (
            "查询FATAP列表",
            {"network_device:device_info", "network_device:device_count"},
        ),
        ("查询FITAP列表", {"fitap:device_info", "fitap:device_count"}),
        ("查询AP列表", {"fitap:device_info", "fitap:device_count"}),
        (
            "查询PON设备列表",
            {
                "olt:device_info",
                "olt:device_count",
                "onu:device_info",
                "onu:device_count",
            },
        ),
    ],
)
def test_empty_intention_basic_uses_specific_device_classification(question, expected_ids):
    assert set(_candidate_ids(_empty_intention_basic_context(question))) == expected_ids


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
    assert '"match_score"' not in prompt
    assert '"table_hints"' not in prompt
    assert '"priority"' not in prompt


def test_chat_prompt_contains_structured_subnet_scope_and_relation_candidate():
    llm_chat_client = MagicMock(
        return_value='{"recommends": [], "explain": "建议保留子网范围查询。"}'
    )
    recommend_questions_chat(
        RecommendationContext(
            intention="查信息",
            question="查询根子网下127网段的存储设备列表",
            device_types=["存储设备"],
            subnet=SubnetScope(path="根子网", name="127网段"),
        ),
        llm_chat_client,
    )
    prompt = llm_chat_client.call_args[0][0][1]["content"]
    assert '"subnet": {' in prompt
    assert '"path": "根子网"' in prompt
    assert '"name": "127网段"' in prompt
    assert "storage_device:device_info" in prompt
    assert "subnet_relation" in prompt


def test_basic_prompt_keeps_full_context_and_invalid_values():
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
            invalid_values=["无效设备"],
        ),
        llm_chat_client,
    )
    prompt = llm_chat_client.call_args[0][0][1]["content"]
    assert '"question": "查询近七天网络设备CPU利用率平均值"' in prompt
    assert '"kpis": [' in prompt and '"CPU利用率"' in prompt
    assert '"time": "近七天"' in prompt
    assert '"aggregations": [' in prompt and '"avg"' in prompt
    assert '"properties": [' in prompt and '"状态"' in prompt
    assert '"refusal_detail": "当前条件无法直接查询CPU利用率"' in prompt
    assert '"invalid_values": [' in prompt and '"无效设备"' in prompt
    assert "network_device:device_metric" in prompt


def test_prompt_constrains_empty_intention_basic_to_matched_objects():
    prompt = QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "当 intention 为空时" in prompt
    assert "已根据 question 中明确出现的业务对象" in prompt
    assert "不得重新扩展到候选之外的设备类型" in prompt
    assert "优先推荐列表、数量或基础信息" in prompt
    assert "不要据此推断指标、趋势、聚合、排序或新的正式意图" in prompt


def test_prompt_treats_subnet_as_cross_domain_scope():
    prompt = QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "子网是跨领域资源范围" in prompt
    assert "网络、存储、服务器、PON、无线和终端对象" in prompt
    assert "不得默认将子网归为网络业务域" in prompt
    assert "不得把用户明确的设备类型改写为网络设备" in prompt


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

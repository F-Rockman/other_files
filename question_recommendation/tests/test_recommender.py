"""最小化上下文、六类能力规格和推荐调用器单元测试。"""

import ast
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from query_errors import ErrorCode, ErrorInfo, ErrorLevel, ErrorStage

import question_recommendation.capability_loader as capability_loader_module
import question_recommendation.capability_candidates as capability_candidates_module
import question_recommendation.capability_matching as capability_matching_module
import question_recommendation.prompt as prompt_module
import question_recommendation.refusal_rules as refusal_rules_module
from question_recommendation import (
    QUESTION_RECOMMENDATION_SYSTEM_PROMPT,
    QUESTION_RECOMMENDATION_USER_TEMPLATE,
    DEVICE_COUNT,
    DEVICE_INFO,
    DEVICE_METRIC,
    SUBCOMPONENT_COUNT,
    SUBCOMPONENT_INFO,
    SUBCOMPONENT_METRIC,
    AlarmCondition,
    DeviceCondition,
    DeviceCapabilityProfile,
    MetadataColumn,
    MetadataTable,
    RecommendationContext,
    SpecialCapabilitySpec,
    SubnetScope,
    SubcomponentCapabilitySpec,
    build_recommendation_context,
    load_capability_cards,
    recommend_capabilities,
    recommend_questions_chat,
    resolve_primary_capability_type,
)
from question_recommendation.logical_model_reader import (
    business_names_from_document,
    load_logical_model_document,
    load_metadata_tables,
)
from question_recommendation.recommender import _build_chat_messages, _parse_llm_response
from question_recommendation.refusal_rules import get_refusal_recovery_rule
from question_recommendation.field_analysis import analyze_candidate_fields
from question_recommendation.prompt import _build_system_prompt
from question_recommendation.simplify_analysis import analyze_simplify_constraints


def _network_interface_context(**overrides):
    data = {
        "intention": "查信息",
        "question": "查询网络设备接口",
        "devices": [{"device_type": "网络设备"}],
        "subcomponent_types": ["接口"],
    }
    data.update(overrides)
    return RecommendationContext.from_dict(data)


def _write_logical_yaml(tmp_path, table_name, fields):
    (tmp_path / f"{table_name}.logical.yaml").write_text(
        json.dumps(
            {
                "name": table_name,
                "description_cn": table_name,
                "schema": {"fields": fields},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


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
    assert [item.device_type for item in context.devices if item.device_type] == ["网络设备"]
    assert context.devices[0].to_dict() == {
        "device_id": "10.1",
        "id_type": "IP",
        "match_mode": "PREFIX",
        "device_type": "网络设备",
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
        devices=[
            {
                "device_id": "10.0.0.1",
                "id_type": "IP",
                "match_mode": "EXACT",
                "device_type": "网络设备",
            }
        ],
        subnet={"path": "根子网", "name": "127网段"},
        aggregations=["count"],
    )
    restored = RecommendationContext.from_json(original.to_json())
    assert restored.to_dict() == original.to_dict()
    assert restored.subnet == SubnetScope(path="根子网", name="127网段")


def test_context_only_accepts_new_device_structure_and_preserves_order():
    context = RecommendationContext.from_dict(
        {
            "devices": [
                {
                    "device_id": "10.0.0.1",
                    "id_type": "ip",
                    "match_mode": "exact",
                    "device_type": "网络设备",
                },
                {
                    "device_id": "server-a",
                    "id_type": "name",
                    "match_mode": "fuzzy",
                    "device_type": "服务器",
                },
                {"value": "legacy-inside-devices", "id_type": "NAME"},
            ],
            "identifiers": [{"value": "legacy", "id_type": "NAME"}],
            "device_types": ["旧设备类型"],
        }
    )

    assert [item.to_dict() for item in context.devices] == [
        {
            "device_id": "10.0.0.1",
            "id_type": "IP",
            "match_mode": "EXACT",
            "device_type": "网络设备",
        },
        {
            "device_id": "server-a",
            "id_type": "NAME",
            "match_mode": "FUZZY",
            "device_type": "服务器",
        },
    ]
    assert "identifiers" not in context.to_dict()
    assert "device_types" not in context.to_dict()
    assert "value" not in context.to_json()


def test_build_context_preserves_each_device_type_relationship():
    context = build_recommendation_context(
        {
            "devices": [
                {
                    "device_id": "10.0.0.1",
                    "id_type": "IP",
                    "match_mode": "EXACT",
                    "device_type": "网络设备",
                },
                {
                    "device_id": "server",
                    "id_type": "NAME",
                    "match_mode": "PREFIX",
                    "device_type": "服务器",
                },
            ]
        }
    )

    assert [item.to_dict() for item in context.devices] == [
        {
            "device_id": "10.0.0.1",
            "id_type": "IP",
            "match_mode": "EXACT",
            "device_type": "网络设备",
        },
        {
            "device_id": "server",
            "id_type": "NAME",
            "match_mode": "PREFIX",
            "device_type": "服务器",
        },
    ]


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
    assert context.devices == [DeviceCondition(device_type="网络设备")]


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
    assert context.devices == [DeviceCondition(device_type="网络设备")]


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
    assert context.devices[0].device_id == "10.1"


def _candidate_ids(context, **kwargs):
    return [item.candidate.capability_id for item in recommend_capabilities(context, **kwargs)]


def _empty_intention_basic_context(question, llm_refuse_message=""):
    return build_recommendation_context(
        {"question": question},
        refuse_info=ErrorCode.INTENT_REJECT_OUT_OF_SCOPE_QUERY.to_info(),
        llm_refuse_message=llm_refuse_message,
    )


def test_capability_configuration_is_valid():
    profiles, specials = load_capability_cards()
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
    network_link = next(item for item in specials if item.capability_id == "network_link")
    assert set(network_link.device_types) == {
        "网络设备",
        "服务器",
        "存储设备",
        "FC交换机",
        "OLT",
        "ONU",
        "FITAP",
        "终端设备",
    }
    assert network_link.trigger_terms == ["对端设备", "对端", "对端网元"]
    assert {
        "查询所有链路的信息",
        "查询链路的状态",
        "查询链路的A端网元名称",
        "查询链路的Z端网元名称",
        "查询网络设备的链路状态",
        "查询网络设备的链路A端网元名称",
        "查询网络设备的链路Z端网元名称",
        "查询网络设备的对端设备",
    }.issubset(set(network_link.examples))
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


def test_load_capability_cards_returns_domain_and_special_cards():
    domain_cards, special_cards = load_capability_cards()
    assert domain_cards
    assert special_cards
    assert all(item.profile_id for item in domain_cards)
    assert all(item.capability_id for item in special_cards)


def test_capability_models_parse_metadata_sources():
    domain_card = DeviceCapabilityProfile.from_dict(
        {
            "profile_id": "custom_device",
            "property_sources": ["device_property"],
            "metric_sources": ["device_metric"],
            "subcomponents": [
                {
                    "types": ["接口"],
                    "property_sources": ["interface_property"],
                    "metric_sources": ["interface_metric"],
                }
            ],
        }
    )
    special_card = SpecialCapabilitySpec.from_dict(
        {"capability_id": "custom_special", "property_sources": ["alarm_property"]}
    )

    assert domain_card.property_sources == ["device_property"]
    assert domain_card.metric_sources == ["device_metric"]
    assert domain_card.subcomponents[0].property_sources == ["interface_property"]
    assert domain_card.subcomponents[0].metric_sources == ["interface_metric"]
    assert special_card.property_sources == ["alarm_property"]


def test_load_capability_cards_expands_business_names_from_sources(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setitem(
        sys.modules,
        "yaml",
        SimpleNamespace(safe_load=lambda stream: json.load(stream)),
    )
    _write_logical_yaml(
        tmp_path,
        "device_property",
        [
            {"name": "status", "businessName_cn": "运行状态"},
            {
                "name": "hidden",
                "businessName_cn": "隐藏状态",
                "properties": {"ui": json.dumps({"displayPriority": "never"})},
            },
            {"name": "empty", "businessName_cn": ""},
            {"name": "fallback", "description_cn": "不能作为能力字段"},
        ],
    )
    _write_logical_yaml(
        tmp_path,
        "device_metric",
        [
            {"name": "cpu", "businessName_cn": "CPU利用率"},
            {
                "name": "hidden_rate",
                "businessName_cn": "隐藏利用率",
                "properties": {"ui": json.dumps({"displayPriority": "never"})},
            },
            {"name": "name_only"},
        ],
    )
    _write_logical_yaml(
        tmp_path,
        "interface_property",
        [{"name": "if_name", "businessName_cn": "接口名称"}],
    )
    _write_logical_yaml(
        tmp_path,
        "interface_metric",
        [{"name": "in_rate", "businessName_cn": "入流量"}],
    )
    monkeypatch.setattr(
        capability_loader_module,
        "_load_capability_document",
        lambda: {
            "device_profiles": [
                {
                    "profile_id": "custom_device",
                    "properties": ["名称", "运行状态"],
                    "metrics": ["CPU利用率"],
                    "property_sources": ["device_property", "missing", "../unsafe"],
                    "metric_sources": ["device_metric"],
                    "subcomponents": [
                        {
                            "types": ["接口"],
                            "properties": ["接口状态"],
                            "metrics": ["入流量"],
                            "property_sources": ["interface_property"],
                            "metric_sources": ["interface_metric"],
                        }
                    ],
                }
            ],
            "special_capabilities": [
                {
                    "capability_id": "custom_special",
                    "properties": ["告警级别"],
                    "property_sources": ["device_property"],
                }
            ],
        },
    )

    domain_cards, special_cards = load_capability_cards(logical_model_dir=str(tmp_path))
    domain_card = domain_cards[0]
    subcomponent = domain_card.subcomponents[0]
    special_card = special_cards[0]

    assert domain_card.properties == ["名称", "运行状态"]
    assert domain_card.metrics == ["CPU利用率"]
    assert subcomponent.properties == ["接口状态", "接口名称"]
    assert subcomponent.metrics == ["入流量"]
    assert special_card.properties == ["告警级别", "运行状态"]
    assert domain_card.property_sources == []
    assert domain_card.metric_sources == []
    assert subcomponent.property_sources == []
    assert subcomponent.metric_sources == []
    assert special_card.property_sources == []
    assert "不能作为能力字段" not in domain_card.properties
    assert "fallback" not in domain_card.properties
    assert "隐藏状态" not in domain_card.properties
    assert "隐藏利用率" not in domain_card.metrics


def test_recommend_capabilities_loads_builtin_document_once_for_relation(monkeypatch):
    original_load = capability_loader_module._load_capability_document
    load_spy = MagicMock(side_effect=original_load)
    monkeypatch.setattr(capability_loader_module, "_load_capability_document", load_spy)
    context = RecommendationContext(
        intention="查信息",
        devices=[DeviceCondition(device_type="存储设备")],
        subnet=SubnetScope(path="根子网", name="生产网"),
    )

    ranked = recommend_capabilities(context)

    assert ranked
    assert load_spy.call_count == 1
    assert any(item.candidate.capability_id == "subnet_relation" for item in ranked)


def test_recommend_capabilities_does_not_load_when_both_card_types_are_injected(
    monkeypatch,
):
    domain_cards, special_cards = load_capability_cards()
    load_spy = MagicMock(side_effect=AssertionError("must not load built-in cards"))
    monkeypatch.setattr(capability_loader_module, "_load_capability_document", load_spy)

    ranked = recommend_capabilities(
        RecommendationContext(intention="查告警"),
        domain_cards=domain_cards,
        special_cards=special_cards,
    )

    assert ranked
    load_spy.assert_not_called()


def test_recommend_capabilities_loads_once_and_preserves_injected_domain_cards(
    monkeypatch,
):
    domain_cards, _ = load_capability_cards()
    network_card = next(item for item in domain_cards if item.profile_id == "network_device")
    original_load = capability_loader_module._load_capability_document
    load_spy = MagicMock(side_effect=original_load)
    monkeypatch.setattr(capability_loader_module, "_load_capability_document", load_spy)

    ranked = recommend_capabilities(
        RecommendationContext(intention="查信息"),
        domain_cards=[network_card],
    )

    assert ranked
    assert load_spy.call_count == 1
    assert all(item.candidate.capability_id.startswith("network_device:") for item in ranked)


def test_recommend_capabilities_loads_once_and_preserves_injected_special_cards(
    monkeypatch,
):
    _, special_cards = load_capability_cards()
    alarm_card = next(item for item in special_cards if item.capability_id == "alarm_query")
    custom_alarm_data = alarm_card.to_dict()
    custom_alarm_data["capability_id"] = "custom_alarm_query"
    custom_alarm_card = type(alarm_card).from_dict(custom_alarm_data)
    original_load = capability_loader_module._load_capability_document
    load_spy = MagicMock(side_effect=original_load)
    monkeypatch.setattr(capability_loader_module, "_load_capability_document", load_spy)

    ranked = recommend_capabilities(
        RecommendationContext(intention="查告警"),
        special_cards=[custom_alarm_card],
    )

    assert load_spy.call_count == 1
    assert any(item.candidate.capability_id == "custom_alarm_query" for item in ranked)
    assert not any(item.candidate.capability_id == "alarm_query" for item in ranked)


def test_capabilities_comprehensions_only_express_simple_single_steps():
    comprehension_types = (
        ast.ListComp,
        ast.SetComp,
        ast.DictComp,
        ast.GeneratorExp,
    )

    for source_path in _capability_module_paths():
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, comprehension_types):
                _assert_simple_comprehension(node, comprehension_types, source_path)


def _assert_simple_comprehension(node, comprehension_types, source_path):
    """验证能力模块中的推导式只表达简单单步映射或过滤。"""
    location = f"{source_path.name}:{node.lineno}"
    assert len(node.generators) == 1, f"{location}: comprehension must use one for"
    assert len(node.generators[0].ifs) <= 1, (
        f"{location}: comprehension may use at most one filter"
    )
    nested_nodes = list(ast.walk(node))
    assert not any(
        isinstance(item, comprehension_types) and item is not node
        for item in nested_nodes
    ), f"{location}: nested comprehensions are not allowed"
    assert not any(
        isinstance(item, (ast.BoolOp, ast.IfExp))
        for item in nested_nodes
    ), f"{location}: complex boolean or conditional logic is not allowed"
    assert not any(
        isinstance(item, ast.Call)
        and isinstance(item.func, ast.Name)
        and item.func.id in {"any", "all"}
        for item in nested_nodes
    ), f"{location}: any/all logic must use an explicit helper"


def test_capabilities_module_functions_stay_small_and_low_complexity():
    for source_path in _capability_module_paths():
        source = source_path.read_text(encoding="utf-8")
        assert len(source.splitlines()) <= 500, (
            f"{source_path.name} exceeds the 500-line capability module limit"
        )
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _assert_function_size_and_complexity(node, source_path)


def _capability_module_paths():
    """返回参与能力召回流程的全部 Python 模块路径。"""
    package_path = Path(__file__).resolve().parents[1]
    return sorted(package_path.glob("capabilit*.py"))


def _assert_function_size_and_complexity(node, source_path):
    """验证能力模块函数的物理行数和圈复杂度。"""
    physical_lines = node.end_lineno - node.lineno + 1
    complexity = _cyclomatic_complexity(node)
    location = f"{source_path.name}:{node.name}"
    assert physical_lines <= 50, (
        f"{location} has {physical_lines} physical lines; maximum is 50"
    )
    assert complexity <= 8, (
        f"{location} has cyclomatic complexity {complexity}; maximum is 8"
    )


def _cyclomatic_complexity(function_node):
    """按 capabilities.py 的可读性约束计算圈复杂度。"""

    class ComplexityVisitor(ast.NodeVisitor):
        def __init__(self):
            self.value = 1

        def visit_If(self, node):
            self.value += 1
            self.generic_visit(node)

        def visit_For(self, node):
            self.value += 1
            self.generic_visit(node)

        def visit_While(self, node):
            self.value += 1
            self.generic_visit(node)

        def visit_IfExp(self, node):
            self.value += 1
            self.generic_visit(node)

        def visit_Assert(self, node):
            self.value += 1
            self.generic_visit(node)

        def visit_BoolOp(self, node):
            self.value += len(node.values) - 1
            self.generic_visit(node)

        def visit_Try(self, node):
            self.value += len(node.handlers)
            self.value += int(bool(node.orelse))
            self.value += int(bool(node.finalbody))
            self.generic_visit(node)

        def visit_comprehension(self, node):
            self.value += len(node.ifs)
            self.generic_visit(node)

    visitor = ComplexityVisitor()
    visitor.visit(function_node)
    return visitor.value


def test_device_profiles_reflect_cross_domain_device_classification():
    domain_cards, _ = load_capability_cards()
    profiles = {profile.profile_id: profile for profile in domain_cards}
    assert profiles["fc_switch"].domain == "存储"
    assert {"WAC", "防火墙"}.issubset(profiles["network_device"].aliases)
    assert "FATAP" not in profiles["network_device"].aliases
    assert profiles["fatap"].domain == "网络"
    assert profiles["fatap"].device_types == ["FATAP"]
    assert profiles["fatap"].aliases == []
    assert {spec.types[0] for spec in profiles["fatap"].subcomponents} == {
        "接口",
        "单板",
        "光模块",
        "机框",
    }
    assert "ap" not in profiles
    assert profiles["fitap"].domain == "无线"
    assert profiles["fitap"].device_types == ["FITAP"]
    assert {"AP", "无线接入点"}.issubset(profiles["fitap"].aliases)
    assert all(
        "PON设备" in profiles[profile_id].aliases
        for profile_id in ("olt", "onu")
    )


def test_metric_example_only_matches_current_card_metrics():
    examples = [
        "查询设备CUSTOMKPI趋势",
        "查询设备CPU利用率趋势",
        "查询设备平均值",
        "查询设备Top5",
    ]
    matched = capability_matching_module.examples_for_type(
        examples, DEVICE_METRIC, ["CustomKPI"]
    )
    assert matched == ["查询设备CUSTOMKPI趋势"]


def test_metric_example_new_name_needs_no_code_change():
    example = "查询设备全新业务指标趋势"
    assert not capability_matching_module._is_metric_example(example, [])
    assert capability_matching_module._is_metric_example(
        example, ["全新业务指标"]
    )


def test_count_example_classification_is_unchanged():
    examples = ["查询设备指标A数量", "查询设备指标A趋势"]
    matched = capability_matching_module.examples_for_type(
        examples, DEVICE_COUNT, ["指标A"]
    )
    assert matched == ["查询设备指标A数量"]


def test_device_and_subcomponent_examples_use_their_own_metrics():
    subcomponent = SubcomponentCapabilitySpec(
        types=["测试部件"],
        metrics=["部件指标B"],
        examples=["查询测试部件部件指标B趋势", "查询测试部件设备指标A趋势"],
    )
    domain_card = DeviceCapabilityProfile(
        profile_id="test_device",
        domain="测试",
        device_types=["测试设备"],
        metrics=["设备指标A"],
        subcomponents=[subcomponent],
        examples=["查询测试设备设备指标A趋势", "查询测试设备部件指标B趋势"],
    )
    device_candidate = capability_candidates_module.domain_card_candidates(
        RecommendationContext(intention="查指标"),
        domain_card,
        DEVICE_METRIC,
        relax=True,
    )[0]
    subcomponent_candidate = capability_candidates_module.domain_card_candidates(
        RecommendationContext(
            intention="查指标",
            subcomponent_types=["测试部件"],
        ),
        domain_card,
        SUBCOMPONENT_METRIC,
        relax=True,
    )[0]
    assert device_candidate.examples == ["查询测试设备设备指标A趋势"]
    assert subcomponent_candidate.examples == ["查询测试部件部件指标B趋势"]


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
        devices=[DeviceCondition(device_type="网络设备")],
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
        devices=[DeviceCondition(device_type="服务器")],
        subcomponent_types=["端口"],
    )
    assert recommend_capabilities(context) == []


def test_server_nic_and_network_port_are_separate_capabilities():
    server_ids = _candidate_ids(
        RecommendationContext(
            intention="查信息",
            devices=[DeviceCondition(device_type="服务器")],
            subcomponent_types=["网卡"],
        )
    )
    network_ids = _candidate_ids(
        RecommendationContext(
            intention="查信息",
            devices=[DeviceCondition(device_type="网络设备")],
            subcomponent_types=["端口"],
        )
    )
    assert "server:网卡:subcomponent_info" in server_ids
    assert "network_device:接口:subcomponent_info" in network_ids
    assert not any("端口" in item for item in server_ids)


def test_serial_number_is_property_but_not_locator():
    profiles, _ = load_capability_cards()
    for profile_id in ("server", "storage_device"):
        profile = next(item for item in profiles if item.profile_id == profile_id)
        assert "序列号" in profile.properties
        assert "SERIAL" not in profile.locators


@pytest.mark.parametrize("subcomponent", ["存储池", "LUN", "文件系统"])
def test_storage_resources_use_subcomponent_skeleton(subcomponent):
    context = RecommendationContext(
        intention="查信息",
        devices=[DeviceCondition(device_type="存储设备")],
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
            devices=[DeviceCondition(device_type="服务器")],
            subcomponent_types=["风扇"],
            kpis=["风扇转速"],
        ),
        RecommendationContext(
            intention="查指标",
            question="查询服务器风扇转速平均值",
            devices=[DeviceCondition(device_type="服务器")],
            subcomponent_types=["风扇"],
            kpis=["风扇转速"],
            aggregations=["avg"],
        ),
        RecommendationContext(
            intention="查指标",
            question="查询存储设备总容量趋势",
            devices=[DeviceCondition(device_type="存储设备")],
            kpis=["总容量"],
        ),
        RecommendationContext(
            intention="查指标",
            question="查询CPU利用率最高的服务器",
            devices=[DeviceCondition(device_type="服务器")],
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
        (RecommendationContext(intention="查告警", devices=[DeviceCondition(device_type="服务器")]), "alarm_query"),
        (RecommendationContext(intention="查链路", devices=[DeviceCondition(device_type="网络设备")]), "network_link"),
        (
            RecommendationContext(
                intention="查信息",
                question="查询OLT下的ONU",
                devices=[DeviceCondition(device_type="OLT")],
            ),
            "olt_onu_relation",
        ),
        (
            RecommendationContext(
                intention="查信息",
                devices=[DeviceCondition(device_type="子网")],
            ),
            "subnet_resource",
        ),
    ],
)
def test_special_capabilities_are_preserved(context, expected_id):
    assert expected_id in _candidate_ids(context)


@pytest.mark.parametrize("device_type", ["服务器", "存储设备", "FITAP"])
def test_link_query_supports_cross_domain_device_types(device_type):
    ranked = recommend_capabilities(
        RecommendationContext(
            intention="查链路",
            devices=[DeviceCondition(device_type=device_type)],
        )
    )

    assert ranked[0].candidate.capability_id == "network_link"
    assert ranked[0].candidate.device_types == [device_type]


def test_special_candidate_uses_objects_without_subcomponent_pollution():
    ranked = recommend_capabilities(RecommendationContext(intention="查告警"))
    alarm = next(item for item in ranked if item.candidate.capability_id == "alarm_query")

    assert alarm.candidate.objects == ["告警"]
    assert alarm.candidate.subcomponent_types == []
    assert alarm.to_dict()["objects"] == ["告警"]
    assert "subcomponent_types" not in alarm.to_dict()
    assert "trigger_terms" not in alarm.to_dict()


def test_special_capability_trigger_terms_recall_link_without_pollution():
    ranked = recommend_capabilities(
        _empty_intention_basic_context("查询Mac为的网络设备的对端设备数量")
    )
    link = next(item for item in ranked if item.candidate.capability_id == "network_link")

    assert link.candidate.objects == ["链路"]
    assert link.candidate.subcomponent_types == []
    assert link.candidate.device_types == ["网络设备"]
    assert "trigger_terms" not in link.to_dict()


def test_special_capability_objects_still_recall_link():
    ranked = recommend_capabilities(
        _empty_intention_basic_context("查询网络设备链路状态")
    )
    link = next(item for item in ranked if item.candidate.capability_id == "network_link")

    assert link.candidate.objects == ["链路"]
    assert link.candidate.device_types == ["网络设备"]


def test_special_trigger_terms_do_not_bypass_known_unsupported_device():
    ranked = recommend_capabilities(
        _empty_intention_basic_context("查询测试设备的对端设备"),
        domain_cards=[
            DeviceCapabilityProfile(
                profile_id="test_device",
                domain="测试",
                device_types=["测试设备"],
            )
        ],
        special_cards=[
            SpecialCapabilitySpec(
                capability_id="network_link",
                capability_type="link_query",
                device_types=["网络设备"],
                objects=["链路"],
                trigger_terms=["对端设备"],
            )
        ],
    )

    assert "network_link" not in [item.candidate.capability_id for item in ranked]


def test_special_candidate_keeps_supported_alias_from_question():
    ranked = recommend_capabilities(_empty_intention_basic_context("查询交换机告警"))
    alarm = next(item for item in ranked if item.candidate.capability_id == "alarm_query")

    assert alarm.candidate.device_types == ["交换机"]


def test_special_candidate_does_not_inherit_unknown_device_term_from_question():
    ranked = recommend_capabilities(
        _empty_intention_basic_context("机框设备告警的数量和明细都需要查看")
    )
    alarm = next(item for item in ranked if item.candidate.capability_id == "alarm_query")

    assert alarm.candidate.objects == ["告警"]
    assert alarm.candidate.subcomponent_types == []
    assert "机框设备" not in alarm.candidate.device_types
    assert "机框设备" not in alarm.to_dict().get("device_types", [])


def test_special_candidate_keeps_generic_device_terms_unexcluded():
    ranked = recommend_capabilities(_empty_intention_basic_context("查询所有设备告警"))
    alarm = next(item for item in ranked if item.candidate.capability_id == "alarm_query")

    assert "所有设备" not in alarm.candidate.device_types


def test_structured_unsupported_device_still_filters_special_card():
    ranked = recommend_capabilities(
        RecommendationContext(
            intention="查告警",
            devices=[DeviceCondition(device_type="机框设备")],
        )
    )

    assert "alarm_query" not in [item.candidate.capability_id for item in ranked]


def test_special_candidate_excludes_known_unsupported_device_term():
    ranked = recommend_capabilities(_empty_intention_basic_context("查询FITAP告警"))
    alarm = next(item for item in ranked if item.candidate.capability_id == "alarm_query")

    assert alarm.candidate.device_types != ["FITAP"]
    assert "FITAP" not in alarm.candidate.device_types


def test_special_candidate_constrains_devices_by_subcomponent_parents():
    domain_cards, alarm_card = _optical_module_alarm_cards()
    ranked = recommend_capabilities(
        RecommendationContext(
            intention="查告警",
            question="查询光模块有哪些告警",
            subcomponent_types=["光模块"],
        ),
        domain_cards=domain_cards,
        special_cards=[alarm_card],
    )
    alarm = next(item for item in ranked if item.candidate.capability_id == "alarm_query")

    assert alarm.candidate.device_types == ["网络设备", "服务器"]
    assert alarm.candidate.subcomponent_types == []
    assert alarm.candidate.objects == ["告警"]
    assert "FATAP" not in alarm.candidate.device_types


def test_special_candidate_structured_device_has_priority_over_subcomponent_parents():
    domain_cards, alarm_card = _optical_module_alarm_cards()
    ranked = recommend_capabilities(
        RecommendationContext(
            intention="查告警",
            question="查询网络设备光模块有哪些告警",
            devices=[DeviceCondition(device_type="网络设备")],
            subcomponent_types=["光模块"],
        ),
        domain_cards=domain_cards,
        special_cards=[alarm_card],
    )
    alarm = next(item for item in ranked if item.candidate.capability_id == "alarm_query")

    assert alarm.candidate.device_types == ["网络设备"]
    assert alarm.candidate.subcomponent_types == []
    assert alarm.candidate.objects == ["告警"]


def test_special_candidate_requires_subcomponent_parent_when_device_is_missing():
    ranked = recommend_capabilities(
        RecommendationContext(
            intention="查告警",
            question="查询光模块有哪些告警",
            subcomponent_types=["光模块"],
        ),
        domain_cards=[
            DeviceCapabilityProfile(
                profile_id="fatap",
                device_types=["FATAP"],
            )
        ],
        special_cards=[
            SpecialCapabilitySpec(
                capability_id="alarm_query",
                capability_type="alarm_query",
                device_types=["FATAP"],
                objects=["告警"],
            )
        ],
    )

    assert "alarm_query" not in [item.candidate.capability_id for item in ranked]


def _optical_module_alarm_cards():
    optical_module = SubcomponentCapabilitySpec(types=["光模块"])
    domain_cards = [
        DeviceCapabilityProfile(
            profile_id="network_device",
            device_types=["网络设备"],
            subcomponents=[optical_module],
        ),
        DeviceCapabilityProfile(
            profile_id="server",
            device_types=["服务器"],
            subcomponents=[optical_module],
        ),
        DeviceCapabilityProfile(
            profile_id="fatap",
            device_types=["FATAP"],
        ),
    ]
    alarm_card = SpecialCapabilitySpec(
        capability_id="alarm_query",
        capability_type="alarm_query",
        device_types=["网络设备", "服务器", "FATAP"],
        objects=["告警"],
    )
    return domain_cards, alarm_card


def test_subnet_scope_keeps_device_info_primary_and_adds_relation_candidate():
    context = RecommendationContext(
        intention="查信息",
        question="查询根子网下127网段的存储设备列表",
        devices=[DeviceCondition(device_type="存储设备")],
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
        devices=[DeviceCondition(device_type="存储设备")],
    )
    with_subnet = RecommendationContext(
        intention="查信息",
        question="查询存储设备列表",
        devices=[DeviceCondition(device_type="存储设备")],
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
        devices=[DeviceCondition(device_type="存储设备")],
        subnet=SubnetScope(path="根子网", name="127网段"),
        kpis=["CPU利用率"],
    )
    ranked = recommend_capabilities(context)
    ids = [item.candidate.capability_id for item in ranked]
    assert ranked[0].candidate.capability_id == "storage_device:device_metric"
    assert "subnet_relation" in ids


def test_simplify_device_scope_keeps_device_family_and_filters_subnet_targets():
    context = RecommendationContext(
        intention="查信息",
        question="查询子网名称为核心层下的防火墙设备数量",
        devices=[DeviceCondition(id_type="OTHER", device_type="防火墙")],
        subnet=SubnetScope(name="核心层"),
        recovery_strategy="simplify",
    )
    ids = _candidate_ids(context)
    assert {
        "network_device:device_info",
        "network_device:device_count",
        "network_device:device_metric",
    }.issubset(ids)
    assert "subnet_resource" not in ids
    assert "subnet_relation" not in ids


def test_simplify_empty_intention_uses_device_family_instead_of_subnet_target():
    context = RecommendationContext(
        question="查询子网名称为核心层下的防火墙设备数量",
        devices=[DeviceCondition(id_type="OTHER", device_type="防火墙")],
        subnet=SubnetScope(name="核心层"),
        recovery_strategy="simplify",
    )
    ids = _candidate_ids(context)
    assert "network_device:device_info" in ids
    assert "network_device:device_count" in ids
    assert "subnet_resource" not in ids
    assert "subnet_relation" not in ids


def test_simplify_subcomponent_scope_filters_unrelated_special_targets():
    context = RecommendationContext(
        intention="查信息",
        question="查询核心层下的光模块信息",
        subcomponent_types=["光模块"],
        subnet=SubnetScope(name="核心层"),
        recovery_strategy="simplify",
    )
    candidates = [item.candidate for item in recommend_capabilities(context)]
    assert candidates
    assert all(
        item.capability_type
        in {SUBCOMPONENT_INFO, SUBCOMPONENT_COUNT, SUBCOMPONENT_METRIC}
        for item in candidates
    )
    assert "subnet_relation" not in [item.capability_id for item in candidates]


@pytest.mark.parametrize(
    ("context", "expected_id"),
    [
        (
            RecommendationContext(
                intention="查告警",
                devices=[DeviceCondition(device_type="服务器")],
                recovery_strategy="simplify",
            ),
            "alarm_query",
        ),
        (
            RecommendationContext(
                intention="查链路",
                devices=[DeviceCondition(device_type="网络设备")],
                recovery_strategy="simplify",
            ),
            "network_link",
        ),
    ],
)
def test_simplify_special_intents_keep_their_task_family(context, expected_id):
    assert _candidate_ids(context) == [expected_id]


def test_subnet_scope_does_not_add_incompatible_relation_candidate():
    context = RecommendationContext(
        intention="查信息",
        devices=[DeviceCondition(device_type="未知设备")],
        subnet=SubnetScope(path="根子网", name="127网段"),
    )
    assert "subnet_relation" not in _candidate_ids(context)


def test_subnet_special_capabilities_have_no_fixed_domain():
    _, special_cards = load_capability_cards()
    subnet_capabilities = [
        spec
        for spec in special_cards
        if spec.capability_id in {"subnet_resource", "subnet_relation"}
    ]
    assert len(subnet_capabilities) == 2
    assert all(not spec.domain for spec in subnet_capabilities)
    assert all("domain" not in spec.to_dict() for spec in subnet_capabilities)
    resource = next(
        item
        for item in recommend_capabilities(
            RecommendationContext(intention="查信息", devices=[DeviceCondition(device_type="子网")])
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
        "存储设备",
        "FC交换机",
        "服务器",
        "OLT",
        "ONU",
        "PON设备",
        "FITAP",
        "AP",
        "olt",
        "fitap",
        "ap",
        "fc交换机",
    ],
)
def test_subnet_relation_supports_cross_domain_device_types_and_aliases(device_type):
    context = RecommendationContext(
        intention="查信息",
        devices=[DeviceCondition(device_type=device_type)],
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
        devices=[DeviceCondition(device_type="网络设备")],
        subcomponent_types=["光模块"],
        kpis=["CPU利用率"],
    )
    assert "network_device:光模块:subcomponent_metric" not in _candidate_ids(context)


@pytest.mark.parametrize("recovery_strategy", ["clarify", "disambiguate"])
def test_recovery_question_network_direction_relaxes_ambiguous_kpi(recovery_strategy):
    ranked = recommend_capabilities(
        RecommendationContext(
            intention="查指标",
            question="查询网络CUP",
            kpis=["CUP"],
            recovery_strategy=recovery_strategy,
        )
    )
    assert [item.candidate.capability_id for item in ranked] == [
        "network_device:device_metric",
        "network_device:device_count",
        "network_device:device_info",
    ]
    assert all(item.candidate.domain == "网络" for item in ranked)
    metric = ranked[0].candidate
    assert "CPU利用率" in metric.metrics
    assert "CUP" not in metric.metrics


@pytest.mark.parametrize(
    ("question", "expected_domains", "expected_metric_ids"),
    [
        ("查询存储CUP", {"存储"}, {"storage_device:device_metric"}),
        ("查询服务器CUP", {"服务器"}, {"server:device_metric"}),
        ("查询PON CUP", {"PON"}, {"olt:device_metric", "onu:device_metric"}),
    ],
)
def test_recovery_question_direction_uses_capability_domains(
    question,
    expected_domains,
    expected_metric_ids,
):
    ranked = recommend_capabilities(
        RecommendationContext(
            intention="查指标",
            question=question,
            kpis=["CUP"],
            recovery_strategy="disambiguate",
        )
    )
    assert {item.candidate.domain for item in ranked} == expected_domains
    assert {
        item.candidate.capability_id
        for item in ranked
        if item.candidate.capability_type == DEVICE_METRIC
    } == expected_metric_ids


def test_recovery_question_direction_keeps_multiple_explicit_domains():
    ranked = recommend_capabilities(
        RecommendationContext(
            intention="查指标",
            question="查询网络和服务器CUP",
            kpis=["CUP"],
            recovery_strategy="disambiguate",
        )
    )
    assert {item.candidate.domain for item in ranked} == {"网络", "服务器"}


def test_recovery_question_direction_keeps_domain_subcomponent_relation():
    ids = set(
        _candidate_ids(
            RecommendationContext(
                intention="查指标",
                question="查询网络光模块CUP",
                kpis=["CUP"],
                recovery_strategy="disambiguate",
            )
        )
    )
    assert ids == {
        "network_device:光模块:subcomponent_metric",
        "network_device:光模块:subcomponent_info",
        "network_device:光模块:subcomponent_count",
    }


def test_recovery_question_direction_does_not_override_structured_object():
    ranked = recommend_capabilities(
        RecommendationContext(
            intention="查信息",
            question="查询网络设备信息",
            devices=[DeviceCondition(device_type="服务器")],
            recovery_strategy="disambiguate",
        )
    )
    assert ranked
    assert all(item.candidate.domain == "服务器" for item in ranked)


def test_regular_recall_uses_question_direction_without_structured_object():
    ranked = recommend_capabilities(
        RecommendationContext(
            intention="查指标",
            question="查询网络CUP",
            kpis=["CUP"],
        )
    )
    assert not any(item.candidate.capability_type == DEVICE_METRIC for item in ranked)
    assert {item.candidate.domain for item in ranked} == {"网络"}


def test_recovery_without_question_direction_keeps_global_recall():
    ranked = recommend_capabilities(
        RecommendationContext(
            intention="查信息",
            question="查询设备信息",
            recovery_strategy="disambiguate",
        )
    )
    assert len({item.candidate.domain for item in ranked}) > 1


def test_kpi_relaxation_is_limited_to_clarify_and_disambiguate():
    ranked = recommend_capabilities(
        RecommendationContext(
            intention="查指标",
            question="查询网络CUP",
            kpis=["CUP"],
            recovery_strategy="basic",
        )
    )
    assert not any(item.candidate.capability_type == DEVICE_METRIC for item in ranked)
    assert all(item.candidate.domain == "网络" for item in ranked)


def test_capability_metric_matching_ignores_case_and_keeps_standard_name():
    context = RecommendationContext(
        intention="查指标",
        devices=[DeviceCondition(device_type="网络设备")],
        kpis=["cpu利用率"],
    )
    metric = next(
        item
        for item in recommend_capabilities(context)
        if item.candidate.capability_id == "network_device:device_metric"
    )
    assert metric.candidate.metrics == ["CPU利用率"]


def test_capability_device_subcomponent_and_locator_matching_ignore_case():
    device_ids = _candidate_ids(
        RecommendationContext(intention="查信息", devices=[DeviceCondition(device_type="fitap")])
    )
    assert "fitap:device_info" in device_ids

    subcomponent_ids = _candidate_ids(
        RecommendationContext(
            intention="查信息",
            subcomponent_types=["bbu"],
            devices=[
                DeviceCondition(
                    device_id="10.0.0.1",
                    id_type="ip",
                    device_type="存储设备",
                )
            ],
        )
    )
    assert "storage_device:BBU:subcomponent_info" in subcomponent_ids


def test_property_match_adds_score_and_property_miss_does_not_filter():
    matched = recommend_capabilities(
        RecommendationContext(
            intention="查信息",
            devices=[DeviceCondition(device_type="网络设备")],
            properties=["状态"],
        )
    )
    missed = recommend_capabilities(
        RecommendationContext(
            intention="查信息",
            devices=[DeviceCondition(device_type="网络设备")],
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


def test_capability_property_score_matching_ignores_case():
    upper = recommend_capabilities(
        RecommendationContext(
            intention="查信息",
            devices=[DeviceCondition(device_type="网络设备")],
            properties=["IP地址"],
        )
    )
    lower = recommend_capabilities(
        RecommendationContext(
            intention="查信息",
            devices=[DeviceCondition(device_type="网络设备")],
            properties=["ip地址"],
        )
    )
    upper_info = next(
        item for item in upper if item.candidate.capability_id == "network_device:device_info"
    )
    lower_info = next(
        item for item in lower if item.candidate.capability_id == "network_device:device_info"
    )
    assert lower_info.match_score == upper_info.match_score


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


def test_capability_table_hint_matching_ignores_case():
    lower = recommend_capabilities(
        RecommendationContext(
            intention="查信息",
            subcomponent_types=["光模块"],
            tables=["server_optical_module"],
        )
    )
    upper = recommend_capabilities(
        RecommendationContext(
            intention="查信息",
            subcomponent_types=["光模块"],
            tables=["SERVER_OPTICAL_MODULE"],
        )
    )
    lower_server = next(
        item
        for item in lower
        if item.candidate.capability_id == "server:光模块:subcomponent_info"
    )
    upper_server = next(
        item
        for item in upper
        if item.candidate.capability_id == "server:光模块:subcomponent_info"
    )
    assert upper_server.match_score == lower_server.match_score


def test_top_twelve_selection_is_stable():
    context = RecommendationContext(intention="查信息")
    first = [item.candidate.capability_id for item in recommend_capabilities(context, limit=12)]
    second = [item.candidate.capability_id for item in recommend_capabilities(context, limit=12)]
    assert first == second
    assert len(first) == 12


def test_core_prompt_keeps_global_and_text_interpretation_rules():
    prompt = QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert "trigger_terms" not in prompt
    for expected in (
        "candidate_capabilities 决定允许的业务域",
        "candidate_capabilities.device_types 只是能力边界证明",
        "不等于用户已识别设备类型",
        "每条推荐必须绑定一个具体 candidate_capability",
        "禁止把多张候选卡的字段并集当作通用白名单",
        "invalid_values",
        "objects 表示告警、链路、子网等特殊能力对象，不是设备子部件",
        "原始 question 不是特殊能力设备词的继承来源",
        "不得从原始 question 继承未出现在候选 device_types 中的设备词",
        "未结构化且未被候选支持的模糊修饰词不得继承",
        "禁止生成“候选外设备 + objects”的组合",
        "最终推荐不得输出候选 device_types 中的具体设备类型",
        "应沿用“设备”等泛化表达",
        "只有绑定候选 locators 支持的设备定位类型才可继承",
        "结果形态与语义去重",
        "推荐形态优先级为：列表 > 数量 > 其他基础信息方向",
        "该偏好不得覆盖原始意图、恢复策略或候选能力边界",
        "原问题查数量时不得推荐查信息",
        "明确缺失属性剔除",
        "多意图拆分",
        "多定位备选条件拆分",
        "不得生成同比、环比、较上期、较同期",
        "即使原始 question 明确包含这类对比表达",
        "拒答恢复场景",
        "禁止继承或生成未来时间",
        "明天、后天、下周、下月、明年、未来某天",
        "明确晚于当前日期的绝对时间",
        "优先删除时间条件",
        "输出 1 到 3 条推荐即可",
        "禁止为了凑满 3 条生成无关对象",
    ):
        assert expected in prompt
    for dynamic_heading in (
        "当前场景：simplify",
        "当前场景：无恢复要求",
        "当前场景：空 intention Basic",
        "当前场景：basic",
        "当前场景：子网范围",
        "当前场景：可用实时元数据",
        "当前场景：无可用实时元数据",
        "当前场景：拒答业务方向",
    ):
        assert dynamic_heading not in prompt
    assert "{recommendation_context_json}" in QUESTION_RECOMMENDATION_USER_TEMPLATE


def test_core_prompt_requires_actionable_natural_explain():
    prompt = QUESTION_RECOMMENDATION_SYSTEM_PROMPT
    assert prompt.count("## explain") == 1
    assert "explain 是直接展示给用户" in prompt
    assert "不写成错误分析报告或推荐系统处理日志" in prompt
    assert "先概括用户当前想查询的业务对象" in prompt
    assert "再自然说明当前查询为什么不适合直接继续" in prompt
    assert "最后结合 recommends 中实际问题" in prompt
    assert "没有恢复要求时，说明当前查询方向" in prompt
    assert "推荐方向必须与 recommends 实际内容一致" in prompt
    assert "不责备用户，不使用带有指责" in prompt
    assert "\u201c错误原因是\u201d" in prompt
    assert "优先用自然连接表达" in prompt
    assert "通常不复述 invalid_values" in prompt
    assert "设备定位未查询到场景" in prompt
    assert "恰好包含一个非空值" in prompt
    assert "设备类型A不支持属性1属性查询" in prompt
    assert "设备类型A不支持指标1指标查询" in prompt
    assert "设备类型A的子部件A不支持属性1属性查询" in prompt
    assert "对象A不支持能力A查询" in prompt
    assert "对象A不支持查询方向A查询" in prompt
    assert "设备类型A的子部件A不支持查询方向A" in prompt
    assert "允许并优先使用\"对象 + 不支持 + 属性/指标/能力\"" in prompt
    assert "当前未查询到IP地址为A的设备" in prompt
    assert "当前未查询到MAC地址为A的设备" in prompt
    assert "当前未查询到名称为A的设备" in prompt
    assert "PREFIX 表达为\"以A开头\"" in prompt
    assert "SUFFIX 表达为\"以A结尾\"" in prompt
    assert "FUZZY 表达为\"包含A\"" in prompt
    assert "序列号为A" in prompt
    assert "设备编码为A" in prompt
    assert "资产编号为A" in prompt
    assert "当前未查询到与A匹配的设备" in prompt
    assert "不存在设备A到设备B的关联关系" in prompt
    assert "当前未查询到这些对象之间的关联关系" in prompt
    assert "设备类型A“属性1”不存在“取值A”这一取值" in prompt
    assert "“属性1”不存在“取值A”这一取值" in prompt
    assert "当前过滤条件不存在该取值" in prompt
    assert "设备类型A、设备类型B、设备A、设备B、属性1、指标1、取值A、IP地址A、MAC地址A、名称A" in prompt
    assert "无对象归属的委婉兜底表达" in prompt
    assert "\u201c设备不存在\u201d" in prompt
    assert "explain 是否包含当前提问、当前原因和下一步方向" in prompt
    assert "暂未匹配到对应对象" not in prompt
    assert "暂未识别到可用关联" not in prompt
    assert "当前未查询到过滤条件" not in prompt
    assert "其他类型" not in prompt
    assert "标识为A" not in prompt
    assert "当前查询条件下未查询到相关数据" not in prompt
    assert "结果为空" not in prompt
    for forbidden_output in (
        "错误原因是",
        "失败原因是",
        "推荐调整为",
        "建议调整为",
        "推荐方向是",
        "基于上述原因",
        "针对该错误",
        "系统建议",
        "支持查看",
        "可查看",
        "设备不存在",
        "字段不存在",
        "对象没有该属性/指标",
        "不支持查询该字段",
        "暂不支持该查询",
        "不支持该查询",
    ):
        assert f"\u201c{forbidden_output}\u201d" in prompt
    explain_section = prompt.split("## explain", 1)[1].split("## 输出与自检", 1)[0]
    for concrete_example in ("闪存存储", "节点信息", "7.183.7.126"):
        assert concrete_example not in explain_section


def test_dynamic_fragments_do_not_define_explain_wording():
    dynamic_fragments = [
        prompt_module._NORMAL_RULES,
        prompt_module._SIMPLIFY_RULES,
        prompt_module._EMPTY_INTENTION_BASIC_RULES,
        prompt_module._BASIC_RULES,
        *prompt_module._RECOVERY_RULES.values(),
        prompt_module._RECOVERY_DIRECTION_RULES,
        prompt_module._SUBNET_RULES,
        prompt_module._METADATA_RULES,
        prompt_module._NO_METADATA_RULES,
    ]
    for fragment in dynamic_fragments:
        assert "explain" not in fragment
        assert "错误原因是" not in fragment
        assert "推荐调整为" not in fragment


def test_normal_runtime_prompt_loads_only_normal_fragment():
    prompt = _build_system_prompt(RecommendationContext(intention="查信息"))
    assert "当前场景：无恢复要求" in prompt
    assert "信息/列表 → 数量/统计 → 指标 → 关联能力" in prompt
    assert "趋势、聚合、排序和 TopN" in prompt
    assert "禁止主动虚构" in prompt
    assert "当前场景：simplify" not in prompt
    assert "当前场景：basic" not in prompt
    assert "当前场景：空 intention Basic" not in prompt


def test_simplify_fragment_overrides_empty_intention_basic():
    prompt = _build_system_prompt(RecommendationContext(recovery_strategy="simplify"))
    assert "当前场景：simplify" in prompt
    assert "当前场景：空 intention Basic" not in prompt
    assert "先区分核心语义和附加约束" in prompt
    assert "同任务族内可能同时包含列表、数量、详情、属性或指标等相邻形态" in prompt
    assert "不代表可以只靠形态切换生成推荐" in prompt
    assert "候选标准设备类型只证明能力边界" in prompt
    assert "优先使用 recommendation_context.devices[].device_type 中的原始对象表达" in prompt
    assert "核心语义必须保留，不能替换、泛化或改变" in prompt
    assert "主查询对象和用户原始对象表达" in prompt
    assert "禁止替换成父类、子类、相近对象或其他候选对象" in prompt
    assert "查设备仍查设备，查告警仍查告警，查链路仍查链路，查指标仍查指标" in prompt
    assert "对象关系，例如父子对象、链路两端、告警所属对象等关系不能被改写" in prompt
    assert "范围角色" in prompt
    assert "删除后可以不出现，但不能变成新的查询目标" in prompt
    assert "可删除附加约束只以 simplify_analysis.removable_constraints 为准" in prompt
    assert "该清单是本场景唯一明确可删条件列表" in prompt
    assert "时间、子网范围、定位条件、过滤条件、聚合、分组、排序" in prompt
    assert "多余对象、多余 KPI、多余属性、多余设备条件" in prompt
    assert "每条 simplify 推荐必须删除 removable_constraints 中至少一项" in prompt
    assert "不在 removable_constraints 中的内容" in prompt
    assert "不得被当成复杂条件、失败原因或可删除约束" in prompt
    assert "无可删附加约束时允许少于 3 条" in prompt
    assert "列表、数量、有哪些、以列表形式展示、趋势、展示趋势、TopN" in prompt
    assert "结果形态表达不是复杂条件" in prompt
    assert "不得只把列表改数量、数量改列表" in prompt
    assert "展示趋势”“趋势”“趋势图”“查看趋势”等查询形态词也不算有效简化" in prompt
    assert "查指标时无论是否有时间范围" in prompt
    assert "有无展示趋势视为同一指标查询" in prompt
    assert "不能只靠省略、删除、补充或改写趋势表达生成推荐" in prompt
    assert "推荐问题与原问题只差趋势表达时视为语义一致" in prompt
    assert "三条推荐之间也不得只靠趋势表达差异区分" in prompt
    assert "补充或删除 TopN、Top5、排名最高等表达" in prompt
    assert "不得只把 KPI 做轻微泛化" in prompt
    assert "禁止指标替换" in prompt
    assert "不得推荐查询同对象的指标B" in prompt
    assert "不得用相近指标、同类指标或其他性能指标补足三条" in prompt
    assert "同类型简化最多生成 1 条推荐" in prompt
    assert "已有一条推荐通过删除时间简化，其余推荐不得继续删除时间来凑数" in prompt
    assert "按本场景退化路径补足，而不是重复同类简化" in prompt
    assert "按顺序退化补足" in prompt
    assert "先保留原指标继续删除其他复杂条件" in prompt
    assert "再删除指标条件并保留设备、子部件、子网、时间等非指标有效条件" in prompt
    assert "推荐详情或基础信息方向" in prompt
    assert "不得跳到无关对象" in prompt


def test_empty_intention_uses_basic_fragment_for_other_strategies():
    prompt = _build_system_prompt(
        RecommendationContext(recovery_strategy="clarify", question="任意问题")
    )
    assert "当前场景：空 intention Basic" in prompt
    assert "当前场景：clarify" not in prompt
    assert "当前场景：无恢复要求" not in prompt
    assert "绑定特殊能力候选时，设备表达仍只能来自" in prompt
    assert "不能从 question 继承候选外设备词" in prompt


def test_out_of_scope_alarm_question_does_not_support_unstructured_modifier():
    context = build_recommendation_context(
        {"question": "电源相关的告警信息在哪里"},
        refuse_info=ErrorInfo(
            key="intent_reject_out_of_scope_query",
            level="warning",
            stage="intent",
            message="非问数场景，询问位置而非查询数据",
        ),
    )
    ranked = recommend_capabilities(context)
    candidate = ranked[0].candidate

    assert context.to_dict() == {
        "question": "电源相关的告警信息在哪里",
        "recovery_strategy": "basic",
        "refusal_message": "非问数场景，询问位置而非查询数据",
    }
    assert candidate.capability_id == "alarm_query"
    assert "电源" not in candidate.objects
    assert "电源" not in candidate.properties
    assert "电源" not in candidate.device_types


@pytest.mark.parametrize(
    ("strategy", "heading"),
    [
        ("basic", "当前场景：basic"),
        ("clarify", "当前场景：clarify"),
        ("disambiguate", "当前场景：disambiguate"),
        ("remove_invalid", "当前场景：remove_invalid"),
        ("adjust_scope", "当前场景：adjust_scope"),
    ],
)
def test_nonempty_intention_selects_only_matching_recovery_fragment(strategy, heading):
    prompt = _build_system_prompt(
        RecommendationContext(intention="查信息", recovery_strategy=strategy)
    )
    assert heading in prompt
    assert prompt.count("## 当前场景：") == 3
    assert "当前场景：拒答业务方向" in prompt
    assert "当前场景：无可用实时元数据" in prompt
    assert "当前场景：可用实时元数据" not in prompt
    assert "当前场景：无恢复要求" not in prompt


def test_unknown_recovery_strategy_does_not_load_normal_fragment():
    prompt = _build_system_prompt(
        RecommendationContext(intention="查信息", recovery_strategy="unknown")
    )
    assert "当前场景：无恢复要求" not in prompt


def test_recovery_direction_fragment_requires_recovery_and_no_structured_object():
    no_object = RecommendationContext(intention="查指标", recovery_strategy="clarify")
    with_device = RecommendationContext(
        intention="查指标",
        recovery_strategy="clarify",
        devices=[DeviceCondition(device_type="网络设备")],
    )
    with_subcomponent = RecommendationContext(
        intention="查指标",
        recovery_strategy="clarify",
        subcomponent_types=["光模块"],
    )
    assert "当前场景：拒答业务方向" in _build_system_prompt(no_object)
    assert "当前场景：拒答业务方向" not in _build_system_prompt(with_device)
    assert "当前场景：拒答业务方向" not in _build_system_prompt(with_subcomponent)


def test_question_and_refusal_text_do_not_select_dynamic_fragments():
    first = RecommendationContext(
        intention="查信息",
        recovery_strategy="basic",
        question="查询网络设备",
        refusal_detail="缺少节点字段",
    )
    second = RecommendationContext(
        intention="查信息",
        recovery_strategy="basic",
        question="查询服务器告警",
        refusal_detail="多意图：不同指标查询",
    )
    assert _build_system_prompt(first) == _build_system_prompt(second)


def test_subnet_fragment_is_selected_only_by_structured_subnet():
    plain = _build_system_prompt(RecommendationContext(intention="查信息"))
    scoped = _build_system_prompt(
        RecommendationContext(intention="查信息", subnet=SubnetScope(name="生产网"))
    )
    assert "当前场景：子网范围" not in plain
    assert "当前场景：子网范围" in scoped
    assert "subnet 是跨领域查询范围" in scoped
    assert "必须逐字继承有效 path/name" in scoped
    assert "name 已包含在完整 path 中时避免重复表达" in scoped


def test_metadata_fragment_requires_nonempty_column_description():
    no_metadata = _build_system_prompt(RecommendationContext(intention="查指标"))
    empty_metadata = _build_system_prompt(
        RecommendationContext(intention="查指标"),
        [MetadataTable(table_name="metric", columns=[MetadataColumn(column_name="cpu")])],
    )
    usable_metadata = _build_system_prompt(
        RecommendationContext(intention="查指标"),
        [
            MetadataTable(
                table_name="metric",
                columns=[
                    MetadataColumn(
                        column_name="cpu_usage",
                        column_description="CPU利用率",
                    )
                ],
            )
        ],
    )
    assert "当前场景：可用实时元数据" not in no_metadata
    assert "当前场景：无可用实时元数据" in no_metadata
    assert "当前场景：可用实时元数据" not in empty_metadata
    assert "当前场景：无可用实时元数据" in empty_metadata
    assert "当前场景：可用实时元数据" in usable_metadata
    assert "当前场景：无可用实时元数据" not in usable_metadata
    assert "实时元数据没有的字段不得推荐" in usable_metadata
    assert "元数据不能扩展绑定候选的设备、业务域、父子关系" in usable_metadata
    assert "每条推荐仍必须绑定一张具体候选卡" in usable_metadata
    assert "不能把一个候选对象的字段用于另一个对象" in usable_metadata
    assert "必须保留原设备类型、父子关系、定位条件、时间、聚合和子网范围" in usable_metadata


def test_no_metadata_fragment_uses_candidate_fields_as_strict_whitelist():
    prompt = _build_system_prompt(
        RecommendationContext(
            intention="查信息",
            question="查询运行状态正常的网络设备",
            devices=[DeviceCondition(device_type="网络设备")],
            properties=["运行状态"],
        )
    )
    assert "当前场景：无可用实时元数据" in prompt
    assert "字段白名单" in prompt
    assert "每条推荐仍先绑定一张具体候选卡" in prompt
    assert "多张候选卡字段的并集不是通用白名单" in prompt
    assert "绑定候选的具体 device_types 不得进入推荐正文" in prompt
    assert "只能使用用户原文的泛化对象或引导先明确设备类型" in prompt
    assert "属性和指标名称匹配忽略英文字母大小写" in prompt
    assert "具体属性只能来自绑定的" in prompt
    assert "具体指标只能来自绑定的" in prompt
    assert "禁止跨设备、子部件或候选借用字段" in prompt


def test_no_metadata_fragment_removes_unmatched_field_and_bound_value():
    prompt = _build_system_prompt(RecommendationContext(intention="查信息"))
    assert "原属性或指标未命中绑定候选白名单时" in prompt
    assert "禁止在该候选推荐中使用原字段及其过滤值" in prompt
    assert "禁止从 question、recommendation_context 或 examples 重新继承" in prompt
    assert "可以从当前绑定候选选择一个语义相近字段" in prompt
    assert "相近字段不得继承原字段绑定的过滤值" in prompt
    assert "禁止生成“属性1取值A的设备类型B”或“属性2取值A的设备类型B”" in prompt
    assert "原属性或指标精确命中绑定候选白名单时" in prompt
    assert "属性1取值A的设备类型A" in prompt


def test_no_metadata_fragment_falls_back_to_same_object_information():
    prompt = _build_system_prompt(RecommendationContext(intention="查信息"))
    assert "当前绑定候选没有合适相近字段时" in prompt
    assert "回退到当前对象不依赖具体字段的基础信息查询" in prompt
    assert "继续继承其他有效对象、父子关系、定位条件、时间和子网范围" in prompt
    assert "禁止为了补足三条切换为数量查询" in prompt


def test_no_metadata_fragment_keeps_empty_intention_kpi_exception():
    prompt = _build_system_prompt(RecommendationContext())
    assert "intention 为空时" in prompt
    assert "device_metric 或 subcomponent_metric 候选时受控继承" in prompt


def test_cross_domain_candidate_fields_remain_bound_to_their_device_type():
    context = build_recommendation_context(
        {
            "intention": "查信息",
            "question": "查询运行状态正常的设备",
            "properties": ["运行状态"],
        },
        refuse_info=ErrorCode.INTENT_GUIDE_CROSS_DOMAIN_QUERY.to_info(),
        llm_refuse_message="涉及多个业务域",
    )
    ranked = recommend_capabilities(context)
    server = next(
        item.candidate for item in ranked if item.candidate.capability_id == "server:device_info"
    )
    network = next(
        item.candidate
        for item in ranked
        if item.candidate.capability_id == "network_device:device_info"
    )
    assert "运行状态" in server.properties
    assert "运行状态" not in network.properties
    assert "状态" in network.properties


def test_candidate_field_analysis_marks_field_missing_from_all_final_candidates():
    context = RecommendationContext(
        intention="查信息",
        question="查询运行状态正常的设备有哪些",
        properties=["运行状态"],
    )
    candidates = [
        {"device_types": ["网络设备"], "properties": ["状态"]},
        {"device_types": ["服务器"], "properties": ["健康状态"]},
        {"device_types": ["分布式存储"], "properties": ["名称"]},
        {"device_types": ["闪存存储"], "properties": ["型号"]},
    ]

    assert analyze_candidate_fields(context, candidates) == {
        "unsupported_properties": ["运行状态"],
        "unsupported_kpis": [],
    }


def test_candidate_field_analysis_keeps_field_supported_by_any_final_candidate():
    context = RecommendationContext(properties=["运行状态"], kpis=["CPU利用率"])
    candidates = [
        {"device_types": ["网络设备"], "properties": ["状态"], "metrics": ["cpu利用率"]},
        {"device_types": ["服务器"], "properties": ["运行状态"]},
    ]

    assert analyze_candidate_fields(context, candidates) == {
        "unsupported_properties": [],
        "unsupported_kpis": [],
    }


def test_candidate_field_analysis_is_disabled_by_usable_metadata():
    context = RecommendationContext(properties=["运行状态"], kpis=["CPU利用率"])
    metadata = [
        MetadataTable(
            table_description="设备信息",
            columns=[MetadataColumn(column_description="设备名称")],
        )
    ]

    assert analyze_candidate_fields(context, [], metadata) == {
        "unsupported_properties": [],
        "unsupported_kpis": [],
    }


def test_simplify_analysis_collects_only_removable_constraints():
    context = RecommendationContext(
        recovery_strategy="simplify",
        devices=[
            DeviceCondition(
                device_id="1.1.1.1",
                id_type="IP",
                match_mode="EXACT",
                device_type="防火墙",
            ),
            DeviceCondition(device_type="网络设备"),
        ],
        subcomponent_types=["光模块"],
        subnet=SubnetScope(name="核心层"),
        properties=["运行状态"],
        kpis=["KPI1", "KPI2", "KPI3"],
        time="近一小时",
        alarm=AlarmCondition(alarm_type="LEVEL", alarm_value="严重"),
        aggregations=["avg", "count", "top_n", "sum"],
    )

    analysis = analyze_simplify_constraints(context)

    assert analysis == {
        "removable_constraints": [
            {"type": "subnet", "value": "核心层"},
            {"type": "time", "value": "近一小时"},
            {
                "type": "device_locator",
                "value": "1.1.1.1",
                "id_type": "IP",
                "match_mode": "EXACT",
                "device_type": "防火墙",
            },
            {
                "type": "alarm",
                "value": "严重",
                "alarm_type": "LEVEL",
            },
            {"type": "aggregation", "value": "avg"},
            {"type": "aggregation", "value": "sum"},
            {"type": "extra_kpi", "value": "KPI2"},
            {"type": "extra_kpi", "value": "KPI3"},
        ]
    }
    serialized = json.dumps(analysis, ensure_ascii=False)
    assert "role" not in serialized
    assert "网络设备" not in serialized
    assert "光模块" not in serialized
    assert "运行状态" not in serialized
    assert "KPI1" not in serialized
    assert "count" not in serialized
    assert "top_n" not in serialized


def test_simplify_analysis_is_empty_outside_simplify():
    context = RecommendationContext(
        recovery_strategy="basic",
        subnet=SubnetScope(name="核心层"),
        time="近一小时",
    )

    assert analyze_simplify_constraints(context) == {"removable_constraints": []}


def test_chat_prompt_contains_simplify_analysis_without_shape_phrases():
    context = RecommendationContext(
        intention="查信息",
        question="查询核心层子网下的防火墙设备，以列表形式展示",
        devices=[DeviceCondition(id_type="OTHER", device_type="防火墙")],
        subnet=SubnetScope(name="核心层"),
        recovery_strategy="simplify",
        refusal_message="查询语句生成失败，请换一种更明确或者减少问题复杂度的问法重试。",
        refusal_detail="查询语句生成失败，请换一种更明确或者减少问题复杂度的问法重试。",
    )

    messages = _build_chat_messages(context, [], [])
    user_prompt = messages[1]["content"]
    simplify_section = user_prompt.split(
        "确定性简化分析 simplify_analysis：", 1
    )[1]

    assert "确定性简化分析 simplify_analysis" in user_prompt
    assert '"type": "subnet"' in simplify_section
    assert '"value": "核心层"' in simplify_section
    assert "以列表形式展示" not in simplify_section


def test_chat_prompt_prioritizes_similar_fields_for_globally_unsupported_item():
    context = RecommendationContext(
        intention="查信息",
        question="查询运行状态正常的设备有哪些",
        properties=["运行状态"],
        recovery_strategy="disambiguate",
    )
    candidates = [
        {"device_types": ["网络设备"], "properties": ["状态"]},
        {"device_types": ["服务器"], "properties": ["健康状态"]},
        {"device_types": ["闪存存储"], "properties": ["型号"]},
    ]

    messages = _build_chat_messages(context, [], candidates)
    system_prompt = messages[0]["content"]
    user_prompt = messages[1]["content"]

    assert '"unsupported_properties": [\n    "运行状态"\n  ]' in user_prompt
    assert "每条推荐仍先绑定一张具体候选卡" in system_prompt
    assert "优先从该卡自身字段中选择一个语义明确的相近字段" in system_prompt
    assert "替换时必须删除原字段及其直接绑定的过滤值" in system_prompt
    assert "只有绑定候选没有清晰相近字段时" in system_prompt
    assert "继承设备定位、父子关系、子网、时间、其他未冲突条件" in system_prompt
    assert "最后才回退同对象基础信息" in system_prompt


def test_dynamic_fragments_have_stable_order_and_are_not_duplicated():
    prompt = _build_system_prompt(
        RecommendationContext(
            intention="查信息",
            recovery_strategy="basic",
            subnet=SubnetScope(name="生产网"),
        ),
        [
            MetadataTable(
                columns=[MetadataColumn(column_description="设备状态")]
            )
        ],
    )
    headings = [
        "当前场景：basic",
        "当前场景：拒答业务方向",
        "当前场景：子网范围",
        "当前场景：可用实时元数据",
        "输出与自检",
    ]
    positions = [prompt.index(heading) for heading in headings]
    assert positions == sorted(positions)
    for heading in headings:
        assert prompt.count(heading) == 1


def test_chat_messages_use_runtime_system_prompt():
    context = RecommendationContext(
        intention="查信息",
        recovery_strategy="basic",
        subnet=SubnetScope(name="生产网"),
    )
    messages = _build_chat_messages(context, [], [])
    system_prompt = messages[0]["content"]
    assert "当前场景：basic" in system_prompt
    assert "当前场景：子网范围" in system_prompt
    assert system_prompt != QUESTION_RECOMMENDATION_SYSTEM_PROMPT


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
    assert context.devices[0].device_id == "device-a"
    assert context.invalid_values == []


@pytest.mark.parametrize(
    ("error_code", "expected_strategy"),
    [
        (ErrorCode.INTENT_GUIDE_CROSS_DOMAIN_QUERY, "disambiguate"),
        (ErrorCode.INTENT_GUIDE_UNSUPPORTED_SUBNET_METRIC_QUERY, "basic"),
        (ErrorCode.INTENT_CLARIFY_METRIC_MISSING, "clarify"),
        (ErrorCode.INTENT_CLARIFY_OBJECT_AMBIGUOUS, "disambiguate"),
        (ErrorCode.VALUE_RETRIEVAL_KPI_NOT_FOUND, "remove_invalid"),
        (ErrorCode.VALUE_RETRIEVAL_ALIAS_NORMALIZATION_FAILED, "basic"),
        (ErrorCode.SQL_GENERATION_SCHEMA_MAPPING_FAILED, "basic"),
        (ErrorCode.SQL_GENERATION_FAILED, "simplify"),
        (ErrorCode.QUERY_EXECUTION_ENGINE_ERROR, "simplify"),
        (ErrorCode.SQL_GENERATION_TIMEOUT, "adjust_scope"),
    ],
)
def test_configured_errors_map_to_stable_recovery_strategies(
    error_code,
    expected_strategy,
):
    context = build_recommendation_context({}, refuse_info=error_code.to_info())
    assert context.recovery_strategy == expected_strategy


@pytest.mark.parametrize(
    "error_key",
    [
        "intent_guide_unsupported_subnet_metric_query",
        "intent_guide_unsupported_subnet_alarm_query",
        "intent_guide_relation_not_found",
        "intent_guide_field_retrieval_failed",
        "value_retrieval_alias_normalization_failed",
        "sql_generation_schema_mapping_failed",
        "sql_generation_join_path_failed",
        "sql_generation_unsupported_sql_feature",
    ],
)
def test_former_reframe_errors_use_basic(error_key):
    assert get_refusal_recovery_rule(error_key).strategy == "basic"


def test_valid_recovery_strategies_use_simplify_without_reframe():
    assert "simplify" in refusal_rules_module.VALID_RECOVERY_STRATEGIES
    assert "re" + "frame" not in refusal_rules_module.VALID_RECOVERY_STRATEGIES


def test_reframe_is_removed_from_recommendation_code_and_docs():
    package_path = Path(__file__).resolve().parents[1]
    source_paths = list(package_path.glob("*.py")) + [package_path / "README.md"]
    assert all("reframe" not in path.read_text(encoding="utf-8") for path in source_paths)


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
    assert [item.device_id for item in context.devices if item.device_id] == [
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
    assert [item.device_id for item in context.devices if item.device_id] == ["1.1.1.1"]


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
        {"intention": "查指标", "devices": [{"device_type": "网络设备"}]},
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
        devices=[DeviceCondition(device_type="网络设备")],
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
        RecommendationContext(intention="查告警", devices=[DeviceCondition(device_type="服务器")]),
        RecommendationContext(intention="查链路", devices=[DeviceCondition(device_type="网络设备")]),
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
        devices=[DeviceCondition(device_type="未知设备")],
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


def test_empty_intention_basic_device_object_recalls_info_count_and_metric():
    context = _empty_intention_basic_context("查询名称为的网络设备")
    assert set(_candidate_ids(context)) == {
        "network_device:device_info",
        "network_device:device_count",
        "network_device:device_metric",
    }


def test_empty_intention_basic_special_object_only_recalls_special_capability():
    context = _empty_intention_basic_context("查询名称的告警")
    assert _candidate_ids(context) == ["alarm_query"]


def test_empty_intention_basic_device_constrains_special_capability():
    context = _empty_intention_basic_context("查询服务器告警")
    ranked = recommend_capabilities(context)
    assert [item.candidate.capability_id for item in ranked] == ["alarm_query"]
    assert ranked[0].candidate.device_types == ["服务器"]


def test_empty_intention_structured_device_constrains_special_capability():
    context = RecommendationContext(
        question="查询服务器告警",
        devices=[DeviceCondition(device_type="网络设备")],
        recovery_strategy="clarify",
    )
    ranked = recommend_capabilities(context)
    assert [item.candidate.capability_id for item in ranked] == ["alarm_query"]
    assert ranked[0].candidate.device_types == ["网络设备"]


def test_empty_intention_basic_domain_constrains_special_capability():
    ranked = recommend_capabilities(_empty_intention_basic_context("查询网络告警"))
    assert [item.candidate.capability_id for item in ranked] == ["alarm_query"]
    assert ranked[0].candidate.device_types == ["网络设备"]


def test_recovery_question_direction_constrains_special_capability():
    ranked = recommend_capabilities(
        RecommendationContext(
            intention="查告警",
            question="查询网络告警",
            recovery_strategy="disambiguate",
        )
    )
    assert ranked[0].candidate.capability_id == "alarm_query"
    assert ranked[0].candidate.device_types == ["网络设备"]
    assert all(
        not item.candidate.device_types
        or item.candidate.device_types == ["网络设备"]
        for item in ranked
    )


def test_empty_intention_basic_subcomponent_recalls_compatible_parent_basics():
    context = _empty_intention_basic_context("查询光模块信息")
    assert set(_candidate_ids(context)) == {
        "network_device:光模块:subcomponent_info",
        "network_device:光模块:subcomponent_count",
        "network_device:光模块:subcomponent_metric",
        "fatap:光模块:subcomponent_info",
        "fatap:光模块:subcomponent_count",
        "fatap:光模块:subcomponent_metric",
        "server:光模块:subcomponent_info",
        "server:光模块:subcomponent_count",
    }


def test_empty_intention_basic_metric_phrase_does_not_match_memory_subcomponent():
    context = _empty_intention_basic_context("查询设备内存利用率")
    candidates = [item.candidate for item in recommend_capabilities(context)]

    assert candidates
    assert all("内存" not in item.subcomponent_types for item in candidates)
    assert all(
        item.capability_type in {DEVICE_INFO, DEVICE_COUNT, DEVICE_METRIC}
        for item in candidates
    )
    assert any(item.capability_type == DEVICE_METRIC for item in candidates)


def test_empty_intention_basic_subcomponent_filter_uses_card_metrics():
    card = DeviceCapabilityProfile(
        profile_id="test_device",
        domain="测试",
        device_types=["测试设备"],
        subcomponents=[
            SubcomponentCapabilitySpec(
                types=["部件A"],
                metrics=["部件A指标1"],
            )
        ],
    )
    special_card = SpecialCapabilitySpec(capability_id="unused")

    metric_context = _empty_intention_basic_context("查询设备部件A指标1")
    metric_candidates = [
        item.candidate
        for item in recommend_capabilities(
            metric_context,
            domain_cards=[card],
            special_cards=[special_card],
        )
    ]
    assert metric_candidates
    assert {item.capability_type for item in metric_candidates} == {SUBCOMPONENT_METRIC}
    assert all(item.subcomponent_types == ["部件A"] for item in metric_candidates)

    subcomponent_context = _empty_intention_basic_context("查询测试设备部件A数量")
    subcomponent_candidates = [
        item.candidate
        for item in recommend_capabilities(
            subcomponent_context,
            domain_cards=[card],
            special_cards=[special_card],
        )
    ]
    assert any(
        item.capability_type == SUBCOMPONENT_COUNT
        and item.subcomponent_types == ["部件A"]
        for item in subcomponent_candidates
    )


def test_empty_intention_basic_explicit_memory_subcomponent_still_matches():
    context = _empty_intention_basic_context("查询服务器内存数量")
    candidates = [item.candidate for item in recommend_capabilities(context)]

    assert any(
        item.capability_type == SUBCOMPONENT_COUNT
        and item.subcomponent_types == ["内存"]
        for item in candidates
    )


@pytest.mark.parametrize("question", ["查询名称为", "查询状态"])
def test_empty_intention_basic_attribute_words_keep_global_fallback(question):
    candidates = [
        item.candidate for item in recommend_capabilities(_empty_intention_basic_context(question))
    ]
    assert len(candidates) > 2
    assert all(
        item.capability_type in {DEVICE_INFO, DEVICE_COUNT, DEVICE_METRIC}
        for item in candidates
    )
    assert any(item.capability_type == DEVICE_METRIC for item in candidates)
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


def _network_and_fatap_cards():
    network_card = DeviceCapabilityProfile(
        profile_id="network_device",
        domain="网络",
        device_types=["网络设备"],
        aliases=["路由器", "交换机", "FAT AP"],
        metrics=["CPU利用率"],
        subcomponents=[
            SubcomponentCapabilitySpec(types=["接口"], metrics=["带宽利用率"])
        ],
        priority=95,
    )
    fatap_card = DeviceCapabilityProfile(
        profile_id="fatap",
        domain="网络",
        device_types=["FATAP"],
        metrics=["CPU利用率"],
        priority=88,
    )
    return [network_card, fatap_card]


@pytest.mark.parametrize(
    ("question", "expected_ids"),
    [
        (
            "查询网络设备列表",
            {
                "network_device:device_info",
                "network_device:device_count",
                "network_device:device_metric",
            },
        ),
        (
            "查询网络相关设备数量",
            {
                "network_device:device_info",
                "network_device:device_count",
                "network_device:device_metric",
            },
        ),
        (
            "查询Fat AP列表",
            {
                "network_device:device_info",
                "network_device:device_count",
                "network_device:device_metric",
            },
        ),
        (
            "查询FATAP列表",
            {"fatap:device_info", "fatap:device_count", "fatap:device_metric"},
        ),
        (
            "查询交换机列表",
            {
                "network_device:device_info",
                "network_device:device_count",
                "network_device:device_metric",
            },
        ),
        (
            "查询网络设备和 FatAP 数量",
            {
                "network_device:device_info",
                "network_device:device_count",
                "network_device:device_metric",
                "fatap:device_info",
                "fatap:device_count",
                "fatap:device_metric",
            },
        ),
    ],
)
def test_regular_recall_resolves_device_type_over_alias_conflicts(
    question, expected_ids
):
    ids = set(
        _candidate_ids(
            RecommendationContext(intention="查信息", question=question),
            domain_cards=_network_and_fatap_cards(),
            special_cards=[],
        )
    )
    assert ids == expected_ids


def test_regular_recall_keeps_global_candidates_without_clear_device_term():
    ids = set(
        _candidate_ids(
            RecommendationContext(intention="查信息", question="查询设备列表"),
            domain_cards=_network_and_fatap_cards(),
            special_cards=[],
        )
    )
    assert {
        "network_device:device_info",
        "fatap:device_info",
    }.issubset(ids)


def test_regular_recall_prefers_structured_device_over_question_text():
    ids = set(
        _candidate_ids(
            RecommendationContext(
                intention="查信息",
                question="查询网络设备列表",
                devices=[DeviceCondition(device_type="FATAP")],
            ),
            domain_cards=_network_and_fatap_cards(),
            special_cards=[],
        )
    )
    assert ids == {"fatap:device_info", "fatap:device_count", "fatap:device_metric"}


def test_regular_recall_filters_question_subcomponent_by_parent_compatibility():
    ids = set(
        _candidate_ids(
            RecommendationContext(intention="查信息", question="查询网络设备接口数量"),
            domain_cards=_network_and_fatap_cards(),
            special_cards=[],
        )
    )
    assert ids == {
        "network_device:接口:subcomponent_info",
        "network_device:接口:subcomponent_count",
        "network_device:接口:subcomponent_metric",
    }


def test_device_term_matching_does_not_normalize_unlisted_spellings():
    network_card = DeviceCapabilityProfile(
        profile_id="network_device",
        domain="网络",
        device_types=["网络设备"],
        aliases=["Fat AP"],
    )
    fatap_card = DeviceCapabilityProfile(
        profile_id="fatap",
        domain="网络",
        device_types=["FATAP"],
    )
    ids = set(
        _candidate_ids(
            RecommendationContext(intention="查信息", question="查询Fat AP列表"),
            domain_cards=[network_card, fatap_card],
            special_cards=[],
        )
    )
    assert ids == {"network_device:device_info", "network_device:device_count"}


@pytest.mark.parametrize(
    ("question", "expected_ids"),
    [
        (
            "查询FATAP列表",
            {"fatap:device_info", "fatap:device_count", "fatap:device_metric"},
        ),
        (
            "查询FITAP列表",
            {"fitap:device_info", "fitap:device_count", "fitap:device_metric"},
        ),
        (
            "查询AP列表",
            {"fitap:device_info", "fitap:device_count", "fitap:device_metric"},
        ),
        (
            "查询PON设备列表",
            {
                "olt:device_info",
                "olt:device_count",
                "olt:device_metric",
                "onu:device_info",
                "onu:device_count",
                "onu:device_metric",
            },
        ),
    ],
)
def test_empty_intention_basic_uses_specific_device_classification(question, expected_ids):
    assert set(_candidate_ids(_empty_intention_basic_context(question))) == expected_ids


def test_empty_intention_basic_object_matching_ignores_case():
    assert set(_candidate_ids(_empty_intention_basic_context("查询fitap列表"))) == {
        "fitap:device_info",
        "fitap:device_count",
        "fitap:device_metric",
    }


def test_empty_intention_uses_structured_device_and_overrides_recovery_strategy():
    context = RecommendationContext(
        question="查询网络设备",
        devices=[DeviceCondition(device_type="服务器")],
        recovery_strategy="basic",
    )
    assert set(_candidate_ids(context)) == {
        "server:device_info",
        "server:device_count",
        "server:device_metric",
    }


def test_empty_intention_structured_subcomponent_includes_metric_direction():
    context = RecommendationContext(
        question="查询设备部件",
        devices=[DeviceCondition(device_type="网络设备")],
        subcomponent_types=["光模块"],
        recovery_strategy="clarify",
    )
    assert set(_candidate_ids(context)) == {
        "network_device:光模块:subcomponent_info",
        "network_device:光模块:subcomponent_count",
        "network_device:光模块:subcomponent_metric",
    }


def test_empty_intention_metric_direction_ignores_unstandardized_context_kpi():
    context = RecommendationContext(
        question="查询A设备的KPI1平均值",
        devices=[DeviceCondition(device_type="服务器")],
        kpis=["KPI1"],
        recovery_strategy="clarify",
    )
    ranked = recommend_capabilities(context)
    metric = next(
        item.candidate
        for item in ranked
        if item.candidate.capability_id == "server:device_metric"
    )
    assert metric.metrics
    assert "KPI1" not in metric.metrics


def test_empty_intention_does_not_create_metric_for_card_without_metrics():
    ids = set(_candidate_ids(_empty_intention_basic_context("查询FC交换机")))
    assert ids == {"fc_switch:device_info", "fc_switch:device_count"}


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
                        {
                            "name": "hidden",
                            "description_cn": "隐藏接口字段",
                            "properties": {"ui": json.dumps({"displayPriority": "never"})},
                        },
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
        logical_model_dir=str(tmp_path),
    )

    assert result["recommends"] == ["查询网络设备接口列表"]
    prompt = llm_chat_client.call_args[0][0][1]["content"]
    assert "network_device:接口:subcomponent_info" in prompt
    assert '"table_name": "network_interface"' in prompt
    assert "隐藏接口字段" not in prompt
    assert "candidate_templates" not in prompt
    assert '"match_score"' not in prompt
    assert '"table_hints"' not in prompt
    assert '"priority"' not in prompt


def test_chat_recommendation_expands_capability_sources_from_business_name(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setitem(
        sys.modules,
        "yaml",
        SimpleNamespace(safe_load=lambda stream: json.load(stream)),
    )
    _write_logical_yaml(
        tmp_path,
        "dynamic_device_property",
        [
            {"name": "status", "businessName_cn": "动态状态"},
            {
                "name": "hidden",
                "businessName_cn": "动态隐藏状态",
                "properties": {"ui": json.dumps({"displayPriority": "never"})},
            },
        ],
    )
    _write_logical_yaml(
        tmp_path,
        "dynamic_device_metric",
        [
            {"name": "cpu", "businessName_cn": "动态CPU利用率"},
            {
                "name": "hidden_cpu",
                "businessName_cn": "动态隐藏CPU",
                "properties": {"ui": json.dumps({"displayPriority": "never"})},
            },
        ],
    )
    monkeypatch.setattr(
        capability_loader_module,
        "_load_capability_document",
        lambda: {
            "device_profiles": [
                {
                    "profile_id": "dynamic_device",
                    "domain": "测试",
                    "device_types": ["动态设备"],
                    "locators": ["IP"],
                    "property_sources": ["dynamic_device_property"],
                    "metric_sources": ["dynamic_device_metric"],
                    "priority": 100,
                }
            ],
            "special_capabilities": [],
        },
    )
    llm_chat_client = MagicMock(
        return_value=json.dumps(
            {"recommends": ["查询动态设备动态CPU利用率"], "explain": "继续查看指标。"},
            ensure_ascii=False,
        )
    )

    recommend_questions_chat(
        RecommendationContext(intention="查指标", devices=[DeviceCondition(device_type="动态设备")]),
        llm_chat_client,
        logical_model_dir=str(tmp_path),
    )
    prompt = llm_chat_client.call_args[0][0][1]["content"]

    assert '"properties": [\n      "动态状态"\n    ]' in prompt
    assert '"metrics": [\n      "动态CPU利用率"\n    ]' in prompt
    assert "动态隐藏状态" not in prompt
    assert "动态隐藏CPU" not in prompt
    assert "property_sources" not in prompt
    assert "metric_sources" not in prompt
    assert "description_cn" not in prompt
    assert '"name": "cpu"' not in prompt


def test_chat_prompt_contains_link_query_examples_without_templates():
    context = RecommendationContext(
        intention="查链路",
        devices=[DeviceCondition(device_type="网络设备")],
    )
    candidate_capabilities = [
        item.to_dict() for item in recommend_capabilities(context, limit=1)
    ]

    messages = _build_chat_messages(context, [], candidate_capabilities)
    user_prompt = messages[1]["content"]

    assert "network_link" in user_prompt
    assert "candidate_templates" not in user_prompt
    for example in (
        "查询所有链路的信息",
        "查询链路的A端网元名称",
        "查询网络设备的链路状态",
        "查询网络设备的对端设备",
    ):
        assert example in user_prompt


def test_chat_prompt_contains_structured_subnet_scope_and_relation_candidate():
    llm_chat_client = MagicMock(
        return_value='{"recommends": [], "explain": "建议保留子网范围查询。"}'
    )
    recommend_questions_chat(
        RecommendationContext(
            intention="查信息",
            question="查询根子网下127网段的存储设备列表",
            devices=[DeviceCondition(device_type="存储设备")],
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
            devices=[DeviceCondition(device_type="网络设备")],
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


def test_logical_model_reader_skips_missing_unsafe_and_invalid_tables(
    tmp_path,
    monkeypatch,
):
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
    (tmp_path / "broken.logical.yaml").write_text("{invalid", encoding="utf-8")
    metadata = load_metadata_tables(
        ["device", "missing", "../unsafe", "broken", "device"],
        str(tmp_path),
    )
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
    assert load_metadata_tables(["device"], str(tmp_path / "missing")) == []


def test_logical_model_reader_filters_ui_hidden_fields(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "yaml", SimpleNamespace(safe_load=lambda stream: json.load(stream)))
    _write_logical_yaml(
        tmp_path,
        "device",
        [
            {
                "name": "hidden",
                "description_cn": "隐藏字段",
                "businessName_cn": "隐藏业务字段",
                "properties": {"ui": json.dumps({"displayPriority": "never"})},
            },
            {"name": "no_properties", "description_cn": "无属性配置", "businessName_cn": "无属性配置"},
            {
                "name": "no_ui",
                "description_cn": "无UI配置",
                "businessName_cn": "无UI配置",
                "properties": {},
            },
            {
                "name": "none_ui",
                "description_cn": "空UI配置",
                "businessName_cn": "空UI配置",
                "properties": {"ui": None},
            },
            {
                "name": "bad_ui",
                "description_cn": "非法UI配置",
                "businessName_cn": "非法UI配置",
                "properties": {"ui": "{bad"},
            },
            {
                "name": "no_priority",
                "description_cn": "无优先级配置",
                "businessName_cn": "无优先级配置",
                "properties": {"ui": json.dumps({"name": "visible"})},
            },
            {
                "name": "visible_priority",
                "description_cn": "展示优先级配置",
                "businessName_cn": "展示优先级配置",
                "properties": {"ui": json.dumps({"displayPriority": "always"})},
            },
        ],
    )

    document = load_logical_model_document(str(tmp_path), "device")
    business_names = business_names_from_document(document)
    metadata = load_metadata_tables(["device"], str(tmp_path))[0]

    assert "隐藏业务字段" not in business_names
    assert "隐藏字段" not in [
        column.column_description for column in metadata.columns
    ]
    assert business_names == [
        "无属性配置",
        "无UI配置",
        "空UI配置",
        "非法UI配置",
        "无优先级配置",
        "展示优先级配置",
    ]
    assert [column.column_name for column in metadata.columns] == [
        "no_properties",
        "no_ui",
        "none_ui",
        "bad_ui",
        "no_priority",
        "visible_priority",
    ]


def test_logical_model_reader_returns_one_group_per_table(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "yaml", SimpleNamespace(safe_load=lambda stream: json.load(stream)))
    for table_name, description in (("device", "设备"), ("metric", "设备指标")):
        _write_logical_yaml(
            tmp_path,
            table_name,
            [
                {"name": "first", "description_cn": "字段一"},
                {"name": "second", "description_cn": "字段二"},
            ],
        )
        document = json.loads((tmp_path / f"{table_name}.logical.yaml").read_text())
        document["description_cn"] = description
        (tmp_path / f"{table_name}.logical.yaml").write_text(
            json.dumps(document, ensure_ascii=False),
            encoding="utf-8",
        )

    metadata = load_metadata_tables(["device", "metric"], str(tmp_path))

    assert [table.table_name for table in metadata] == ["device", "metric"]
    assert [len(table.columns) for table in metadata] == [2, 2]

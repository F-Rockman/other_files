from unit_normalization import analyze_unit_correction, build_unit_correction_knowledge


def test_recognizes_common_units():
    result = analyze_unit_correction("查询响应时间 ms 和带宽 Mbps 以及容量 GB")

    units = {item.raw: item.unit_type for item in result.matched_units}

    assert units["ms"] == "latency_time"
    assert units["Mbps"] == "data_rate"
    assert units["GB"] == "data_volume"


def test_corrects_latency_m_per_second_to_ms_with_high_evidence():
    result = analyze_unit_correction("查询时延 m/s 大于10的设备")

    assert result.status == "corrected"
    assert result.selected_correction is not None
    assert result.selected_correction.type == "unit"
    assert result.selected_correction.source == "m/s"
    assert result.selected_correction.target == "ms"
    assert 'rewrite_unit_to="ms"' in result.business_knowledge


def test_corrects_response_time_m_per_second_to_ms():
    knowledge = build_unit_correction_knowledge("查询响应时间 m/s")

    assert "m/s" in knowledge
    assert "ms" in knowledge
    assert "rewrite_unit_to" in knowledge


def test_cpu_usage_with_latency_unit_is_not_force_corrected():
    result = analyze_unit_correction("查询CPU利用率 ms 大于10的设备")

    assert result.status in {"unknown", "unsafe", "ambiguous"}
    assert result.selected_correction is None


def test_inbound_traffic_mbps_uses_capability_metric():
    result = analyze_unit_correction("查询服务器网卡入流量 Mbps 趋势")

    assert result.status == "matched"
    assert any(field.canonical_field == "入流量" for field in result.matched_fields)


def test_generic_inbound_flow_mbps_rewrites_to_supported_metric():
    result = analyze_unit_correction("查询网卡入方向流量 Mbps 趋势")

    assert result.status == "corrected"
    assert result.selected_correction is not None
    assert result.selected_correction.type == "metric"
    assert result.selected_correction.target == "入流量"


def test_generic_outbound_flow_mbps_rewrites_to_supported_metric():
    result = analyze_unit_correction("查询网卡出方向流量 Mbps 趋势")

    assert result.status == "corrected"
    assert result.selected_correction is not None
    assert result.selected_correction.target == "出流量"


def test_interface_flow_without_direction_is_ambiguous():
    result = analyze_unit_correction("查询接口流量 Mbps 趋势")

    assert result.status == "ambiguous"
    assert result.selected_correction is None


def test_flow_gb_does_not_rewrite_without_supported_metric():
    result = analyze_unit_correction("查询流量 GB 最大的链路")

    assert result.status in {"unknown", "unsafe"}
    assert result.selected_correction is None


def test_mbps_and_MBps_are_not_auto_confused():
    result = analyze_unit_correction("查询带宽 MBps 最大的设备")

    assert result.status in {"unknown", "unsafe"}
    assert result.selected_correction is None

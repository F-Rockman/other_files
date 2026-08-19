"""共享查询错误类型单元测试。"""

from query_errors import ErrorCode, ErrorInfo, ErrorLevel, ErrorStage


def test_error_code_preserves_stable_definition():
    error = ErrorCode.INTENT_GUIDE_DEVICE_NOT_FOUND
    assert error.key == "intent_guide_device_not_found"
    assert error.level == ErrorLevel.WARNING.value
    assert error.stage == ErrorStage.INTENT.value
    assert error.message == "未找到匹配设备，请确认设备名称、IP 或设备编码是否正确。"


def test_error_code_builds_shared_error_info():
    info = ErrorCode.VALUE_RETRIEVAL_IP_NOT_FOUND.to_info(message="custom")
    assert isinstance(info, ErrorInfo)
    assert info.to_dict() == {
        "key": "value_retrieval_ip_not_found",
        "level": "warning",
        "stage": "value_retrieval",
        "message": "custom",
    }


def test_unknown_error_code_resolves_to_system_unknown():
    assert ErrorCode.resolve("future_unknown") is ErrorCode.SYSTEM_UNKNOWN_ERROR


def test_error_code_can_list_by_stage():
    intent_errors = ErrorCode.list_by_stage(ErrorStage.INTENT)
    assert ErrorCode.INTENT_GUIDE_DEVICE_NOT_FOUND in intent_errors
    assert all(item.stage == ErrorStage.INTENT.value for item in intent_errors)

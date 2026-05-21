"""
SVG 安全校验单元测试

测试覆盖：
- 正常 SVG 通过校验
- XML Bomb (Billion Laughs) 被拦截
- 含 <script> 的 SVG 被拦截
- 含事件属性的 SVG 被拦截
- 含外部 href 的 SVG 被拦截
- 非 SVG XML 被拦截
- 超大文件被拦截
- 非法 Base64 被拦截
- 嵌套过深被拦截
- 危险属性值被拦截
"""

import base64
import pytest
from svg_security import SVGSecurityError, validate_base64_svg, get_safe_svg_string


# ============ 测试用 SVG 数据 ============

def make_b64(svg_bytes: bytes) -> str:
    """将 SVG 字节转为 Base64 字符串"""
    return base64.b64encode(svg_bytes).decode()


# 正常 SVG
NORMAL_SVG = make_b64(b'''<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <circle cx="50" cy="50" r="40" fill="red"/>
  <rect x="10" y="10" width="30" height="30" fill="blue"/>
</svg>''')

# XML Bomb SVG (Billion Laughs Attack)
BOMB_SVG = make_b64(b'''<?xml version="1.0"?>
<!DOCTYPE svg [
  <!ENTITY x0 "foo">
  <!ENTITY x1 "&x0;&x0;&x0;&x0;&x0;&x0;&x0;&x0;">
  <!ENTITY x2 "&x1;&x1;&x1;&x1;&x1;&x1;&x1;&x1;">
  <!ENTITY x3 "&x2;&x2;&x2;&x2;&x2;&x2;&x2;&x2;">
]>
<svg xmlns="http://www.w3.org/2000/svg">
  <text>&x3;</text>
</svg>''')

# 含 <script> 的 SVG
SCRIPT_SVG = make_b64(b'''<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <script>alert('XSS')</script>
  <circle cx="50" cy="50" r="40"/>
</svg>''')

# 含 onclick 事件的 SVG
EVENT_SVG = make_b64(b'''<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <circle onclick="alert(1)" cx="50" cy="50" r="40"/>
</svg>''')

# 含外部 href 的 SVG
EXTERNAL_HREF_SVG = make_b64(b'''<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <use xlink:href="http://evil.com/malicious.svg"/>
</svg>''')

# 非 SVG XML
NOT_SVG_XML = make_b64(b'''<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>Hello</body>
</html>''')

# 含 javascript: URI 的 SVG
JS_URI_SVG = make_b64(b'''<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <a href="javascript:alert(1)">
    <circle cx="50" cy="50" r="40"/>
  </a>
</svg>''')

# 含 CSS expression 的 SVG
CSS_EXPR_SVG = make_b64(b'''<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <rect style="width: expression(alert(1))" x="10" y="10" width="30" height="30"/>
</svg>''')

# data URI 前缀的正常 SVG
DATA_URI_SVG = "data:image/svg+xml;base64," + NORMAL_SVG


# ============ 测试：正常 SVG 通过 ============

class TestNormalSVG:
    def test_normal_svg_passes(self):
        result = validate_base64_svg(NORMAL_SVG)
        assert result["valid"] is True
        assert result["size"] > 0
        assert "svg" in result["elements"]
        assert "circle" in result["elements"]

    def test_data_uri_svg_passes(self):
        result = validate_base64_svg(DATA_URI_SVG)
        assert result["valid"] is True

    def test_get_safe_svg_string(self):
        safe_svg = get_safe_svg_string(NORMAL_SVG)
        assert '<svg' in safe_svg
        assert '<circle' in safe_svg
        assert '<script' not in safe_svg


# ============ 测试：XML Bomb 被拦截 ============

class TestXMLBomb:
    def test_bomb_svg_blocked(self):
        with pytest.raises(SVGSecurityError):
            validate_base64_svg(BOMB_SVG)

    def test_bomb_svg_error_message(self):
        with pytest.raises(SVGSecurityError) as exc_info:
            validate_base64_svg(BOMB_SVG)
        # 预处理应剥离 DOCTYPE，解析后实体引用残留应被检测
        assert "entity" in str(exc_info.value).lower() or "parsing" in str(exc_info.value).lower()


# ============ 测试：XSS 被拦截 ============

class TestXSS:
    def test_script_svg_blocked(self):
        with pytest.raises(SVGSecurityError):
            validate_base64_svg(SCRIPT_SVG)

    def test_event_handler_blocked(self):
        with pytest.raises(SVGSecurityError):
            validate_base64_svg(EVENT_SVG)

    def test_javascript_uri_blocked(self):
        with pytest.raises(SVGSecurityError):
            validate_base64_svg(JS_URI_SVG)

    def test_css_expression_blocked(self):
        with pytest.raises(SVGSecurityError):
            validate_base64_svg(CSS_EXPR_SVG)


# ============ 测试：外部引用被拦截 ============

class TestExternalRefs:
    def test_external_href_blocked(self):
        with pytest.raises(SVGSecurityError):
            validate_base64_svg(EXTERNAL_HREF_SVG)


# ============ 测试：非 SVG 被拦截 ============

class TestNonSVG:
    def test_non_svg_xml_blocked(self):
        with pytest.raises(SVGSecurityError):
            validate_base64_svg(NOT_SVG_XML)


# ============ 测试：大小限制 ============

class TestSizeLimits:
    def test_empty_input_blocked(self):
        with pytest.raises(SVGSecurityError):
            validate_base64_svg("")

    def test_invalid_base64_blocked(self):
        with pytest.raises(SVGSecurityError):
            validate_base64_svg("not-valid-base64!!!")

    def test_none_input_blocked(self):
        with pytest.raises(SVGSecurityError):
            validate_base64_svg(None)


# ============ 测试：嵌套深度 ============

class TestNestingDepth:
    def test_deeply_nested_svg_blocked(self):
        # 构造深度嵌套 SVG
        nested = '<svg xmlns="http://www.w3.org/2000/svg">'
        for i in range(60):
            nested += '<g>'
        nested += '<circle cx="50" cy="50" r="40"/>'
        for i in range(60):
            nested += '</g>'
        nested += '</svg>'
        with pytest.raises(SVGSecurityError):
            validate_base64_svg(make_b64(nested.encode()))
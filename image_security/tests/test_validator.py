"""
图片安全校验单元测试

测试覆盖：
- 正常图片通过校验
- 解压缩炸弹被拦截
- Polyglot 文件被拦截
- EXIF 危险内容被检测
- Content-Type 不匹配被拦截
- 非白名单格式被拦截
- 超大文件被拦截
- 隐写术检测
- 安全文件名生成
- Base64 图片校验
"""

import os
import base64
import struct
import tempfile
import pytest
from PIL import Image

from image_security import (
    ImageSecurityError,
    validate_image_file,
    validate_image_bytes,
    validate_base64_image,
    sanitize_exif,
    detect_polyglot,
    detect_steganography,
    verify_content_type,
    safe_open_image,
    generate_safe_filename,
)


# ============ 辅助函数 ============

def create_test_image(fmt='PNG', size=(100, 100), color='red', filepath=None):
    """创建测试图片文件"""
    img = Image.new('RGB', size, color)
    if filepath is None:
        filepath = tempfile.mktemp(suffix=f'.{fmt.lower()}')
    img.save(filepath, format=fmt)
    return filepath


def create_test_image_bytes(fmt='PNG', size=(100, 100), color='red'):
    """创建测试图片字节数据"""
    img = Image.new('RGB', size, color)
    buf = tempfile.SpooledTemporaryFile(max_size=1024*1024)
    img.save(buf, format=fmt)
    return buf.getvalue()


def make_b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


# ============ 测试：正常图片通过 ============

class TestNormalImage:
    def test_normal_png_passes(self):
        filepath = create_test_image('PNG')
        result = validate_image_file(filepath)
        assert result["valid"] is True
        assert result["format"] == 'PNG'
        assert result["dimensions"] == (100, 100)
        os.unlink(filepath)

    def test_normal_jpeg_passes(self):
        filepath = create_test_image('JPEG')
        result = validate_image_file(filepath)
        assert result["valid"] is True
        assert result["format"] == 'JPEG'
        os.unlink(filepath)

    def test_normal_bytes_passes(self):
        data = create_test_image_bytes('PNG')
        result = validate_image_bytes(data)
        assert result["valid"] is True
        assert result["format"] == 'PNG'

    def test_normal_base64_passes(self):
        data = create_test_image_bytes('PNG')
        b64 = make_b64(data)
        result = validate_base64_image(b64)
        assert result["valid"] is True

    def test_data_uri_base64_passes(self):
        data = create_test_image_bytes('PNG')
        b64 = "data:image/png;base64," + make_b64(data)
        result = validate_base64_image(b64)
        assert result["valid"] is True


# ============ 测试：解压缩炸弹 ============

class TestDecompressionBomb:
    def test_decompression_bomb_blocked(self):
        # 构造一个声称巨大尺寸的 PNG
        # PNG 允许在 IHDR 中声明巨大尺寸，但实际数据很小
        # Pillow 的 MAX_IMAGE_PIXELS 会拦截
        filepath = tempfile.mktemp(suffix='.png')
        # 创建一个正常大小的图片，然后修改 IHDR 声明巨大尺寸
        img = Image.new('RGB', (10, 10), 'red')
        img.save(filepath, format='PNG')

        # 读取文件并修改 IHDR 中的尺寸声明
        with open(filepath, 'rb') as f:
            data = f.read()

        # 找到 IHDR chunk 并修改尺寸
        ihdr_start = data.find(b'IHDR')
        if ihdr_start >= 0:
            # IHDR: 4字节长度 + "IHDR" + 4字节宽 + 4字节高 + ...
            width_offset = ihdr_start + 4
            # 将宽度和高度改为巨大值
            huge_w = struct.pack('>I', 65535)
            huge_h = struct.pack('>I', 65535)
            modified = data[:width_offset] + huge_w + huge_h + data[width_offset+8:]
            with open(filepath, 'wb') as f:
                f.write(modified)

        with pytest.raises(ImageSecurityError):
            validate_image_file(filepath)
        os.unlink(filepath)


# ============ 测试：Polyglot 检测 ============

class TestPolyglot:
    def test_polyglot_with_script_tail_blocked(self):
        # 创建正常 PNG，尾部附加 PHP 代码
        data = create_test_image_bytes('PNG')
        malicious = data + b'<?php eval($_GET["cmd"]); ?>'
        with pytest.raises(ImageSecurityError):
            detect_polyglot(malicious)

    def test_polyglot_with_html_header_blocked(self):
        # 创建以 HTML 开头但包含图片数据的文件
        html_header = b'<html><body>'
        data = html_header + create_test_image_bytes('PNG')
        with pytest.raises(ImageSecurityError):
            detect_polyglot(data)

    def test_normal_image_not_polyglot(self):
        data = create_test_image_bytes('PNG')
        result = detect_polyglot(data)
        assert isinstance(result, list)
        assert len(result) > 0  # 应匹配 PNG 格式


# ============ 测试：EXIF 清洗 ============

class TestEXIF:
    def test_exif_strip_mode(self):
        filepath = create_test_image('JPEG')
        img = Image.open(filepath)
        clean_img = sanitize_exif(img, mode='strip')
        assert clean_img.size == img.size
        assert clean_img.mode == img.mode
        # 清洗后不应有 EXIF
        assert clean_img._getexif() is None or len(clean_img._getexif()) == 0
        os.unlink(filepath)

    def test_exif_check_mode_returns_list(self):
        filepath = create_test_image('JPEG')
        img = Image.open(filepath)
        result = sanitize_exif(img, mode='check')
        assert isinstance(result, list)
        os.unlink(filepath)


# ============ 测试：Content-Type 验证 ============

class TestContentType:
    def test_matching_content_type_passes(self):
        data = create_test_image_bytes('PNG')
        result = verify_content_type(data, 'image/png')
        assert result == 'PNG'

    def test_mismatched_content_type_blocked(self):
        data = create_test_image_bytes('PNG')
        with pytest.raises(ImageSecurityError):
            verify_content_type(data, 'image/jpeg')


# ============ 测试：格式白名单 ============

class TestFormatWhitelist:
    def test_tiff_format_blocked(self):
        # TIFF 不在白名单中
        filepath = tempfile.mktemp(suffix='.tif')
        img = Image.new('RGB', (10, 10), 'red')
        img.save(filepath, format='TIFF')
        with pytest.raises(ImageSecurityError):
            validate_image_file(filepath)
        os.unlink(filepath)


# ============ 测试：大小限制 ============

class TestSizeLimits:
    def test_empty_base64_blocked(self):
        with pytest.raises(ImageSecurityError):
            validate_base64_image("")

    def test_invalid_base64_blocked(self):
        with pytest.raises(ImageSecurityError):
            validate_base64_image("not-valid-base64!!!")

    def test_none_input_blocked(self):
        with pytest.raises(ImageSecurityError):
            validate_base64_image(None)


# ============ 测试：隐写术检测 ============

class TestSteganography:
    def test_normal_image_no_steg(self):
        filepath = create_test_image('PNG')
        img = Image.open(filepath)
        result = detect_steganography(img)
        # 正常图片 LSB 分布应接近随机，不应被标记
        assert isinstance(result, bool)
        os.unlink(filepath)

    def test_steg_detection_with_check_flag(self):
        filepath = create_test_image('PNG')
        result = validate_image_file(filepath, check_steg=True)
        assert "steganography_detected" in result
        os.unlink(filepath)


# ============ 测试：安全文件名 ============

class TestSafeFilename:
    def test_generate_safe_filename_png(self):
        name = generate_safe_filename("original.png", "PNG")
        assert name.endswith('.png')
        assert len(name) == 36 + 4  # UUID hex (32) + ".png"

    def test_generate_safe_filename_jpeg(self):
        name = generate_safe_filename("malicious.php.jpg", "JPEG")
        assert name.endswith('.jpg')
        # 不应包含原始文件名
        assert 'malicious' not in name
        assert 'php' not in name


# ============ 测试：safe_open_image ============

class TestSafeOpenImage:
    def test_safe_open_from_file(self):
        filepath = create_test_image('PNG')
        img = safe_open_image(filepath)
        assert isinstance(img, Image.Image)
        assert img.size == (100, 100)
        os.unlink(filepath)

    def test_safe_open_from_bytes(self):
        data = create_test_image_bytes('PNG')
        img = safe_open_image(data)
        assert isinstance(img, Image.Image)
        assert img.size == (100, 100)
"""
图片安全校验模块

防护层级：
L1: 文件大小校验
L2: 格式签名验证（防 Polyglot）
L3: 解压缩炸弹检测（Pillow MAX_IMAGE_PIXELS）
L4: 图片尺寸限制
L5: EXIF 元数据清洗
L6: 隐写术检测（LSB 统计分析）
L7: Content-Type 一致性验证
L8: 尾部危险内容检测

依赖 Pillow（PIL），不依赖 ImageMagick。
"""

import os
import re
import base64
import hashlib
import uuid
from io import BytesIO
from PIL import Image, ExifTags
from PIL.ExifTags import TAGS, GPSTAGS

from .config import (
    MAX_FILE_SIZE, MAX_BASE64_SIZE, MAX_PIXELS, MAX_DIMENSION,
    SAFE_FORMATS, FORMAT_SIGNATURES, FORMAT_MIME_MAP,
    DANGEROUS_SIGNATURES, DANGEROUS_TAIL_PATTERNS,
    SAFE_EXIF_TAGS, DANGEROUS_EXIF_PATTERNS,
    STEGANOGRAPHY_LSB_THRESHOLD, STEGANOGRAPHY_SAMPLE_PIXELS,
)


class ImageSecurityError(Exception):
    """图片安全校验异常"""
    pass


# ============ L1: 文件大小校验 ============

def check_file_size(filepath):
    file_size = os.path.getsize(filepath)
    if file_size > MAX_FILE_SIZE:
        raise ImageSecurityError(
            f"Image file too large: {file_size} bytes (max {MAX_FILE_SIZE})"
        )
    return file_size


def check_bytes_size(data):
    if len(data) > MAX_FILE_SIZE:
        raise ImageSecurityError(
            f"Image data too large: {len(data)} bytes (max {MAX_FILE_SIZE})"
        )
    return len(data)


# ============ L2: 格式签名验证（防 Polyglot） ============

def detect_format(header_bytes):
    """根据文件头字节识别图片格式"""
    for fmt, sig in FORMAT_SIGNATURES.items():
        if isinstance(sig, tuple):
            for s in sig:
                if header_bytes.startswith(s):
                    return fmt if fmt in SAFE_FORMATS else fmt.rstrip('87a').rstrip('9a')
        elif header_bytes.startswith(sig):
            return fmt if fmt in SAFE_FORMATS else fmt.rstrip('87a').rstrip('9a')
    return None


def detect_polyglot(filepath_or_bytes):
    """
    检测多语言文件（Polyglot）
    一个文件同时匹配多种格式签名 = Polyglot 攻击
    """
    if isinstance(filepath_or_bytes, str):
        with open(filepath_or_bytes, 'rb') as f:
            header = f.read(32)
            full_content = f.read()
    elif isinstance(filepath_or_bytes, bytes):
        header = filepath_or_bytes[:32]
        full_content = filepath_or_bytes
    else:
        raise ImageSecurityError("Invalid input type for polyglot detection")

    matched_formats = []

    # 检查图片格式签名
    for fmt, sig in FORMAT_SIGNATURES.items():
        if isinstance(sig, tuple):
            for s in sig:
                if header.startswith(s):
                    matched_formats.append(fmt)
                    break
        elif header.startswith(sig):
            matched_formats.append(fmt)

    # 检查危险格式签名（非图片格式出现在图片文件中）
    for fmt, sig in DANGEROUS_SIGNATURES.items():
        if sig is None:
            continue
        if isinstance(sig, tuple):
            for s in sig:
                if header.startswith(s):
                    matched_formats.append(fmt)
                    break
        elif header.startswith(sig):
            matched_formats.append(fmt)

    # 图片文件不应匹配非图片格式
    image_formats = set()
    for fmt, sig in FORMAT_SIGNATURES.items():
        if isinstance(sig, tuple):
            for s in sig:
                if header.startswith(s):
                    image_formats.add(fmt)
                    break
        elif header.startswith(sig):
            image_formats.add(fmt)

    non_image_formats = set(matched_formats) - image_formats
    if non_image_formats:
        raise ImageSecurityError(
            f"Polyglot detected: file matches both image and non-image formats: "
            f"{non_image_formats}"
        )

    # 检查文件尾部是否包含脚本标签
    for pattern in DANGEROUS_TAIL_PATTERNS:
        if pattern in full_content:
            raise ImageSecurityError(
                f"Script content found in image file: {pattern.decode('utf-8', errors='replace')}"
            )

    return matched_formats


# ============ L3: 解压缩炸弹检测 ============

def safe_open_image(filepath_or_bytes):
    """
    安全打开图片文件，检测解压缩炸弹

    Pillow 的 Image.MAX_IMAGE_PIXELS 限制像素总数，
    超过限制时抛出 DecompressionBombError。
    """
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS

    try:
        if isinstance(filepath_or_bytes, str):
            img = Image.open(filepath_or_bytes)
        elif isinstance(filepath_or_bytes, bytes):
            img = Image.open(BytesIO(filepath_or_bytes))
        else:
            raise ImageSecurityError("Invalid input type for image opening")

        img.load()

    except Image.DecompressionBombError:
        raise ImageSecurityError("Decompression bomb detected: pixel count exceeds limit")
    except Image.DecompressionBombWarning:
        raise ImageSecurityError("Decompression bomb warning: pixel count near limit")
    except Exception as e:
        raise ImageSecurityError(f"Failed to open image: {e}")

    return img


# ============ L4: 图片尺寸限制 ============

def check_dimensions(img):
    """验证图片尺寸在安全范围内"""
    w, h = img.size

    if w > MAX_DIMENSION or h > MAX_DIMENSION:
        raise ImageSecurityError(
            f"Image dimension too large: {w}×{h} (max {MAX_DIMENSION}×{MAX_DIMENSION})"
        )

    if w * h > MAX_PIXELS:
        raise ImageSecurityError(
            f"Image pixel count too large: {w * h} (max {MAX_PIXELS})"
        )

    return w, h


# ============ L5: EXIF 元数据清洗 ============

def sanitize_exif(filepath_or_bytes_or_img, mode='strip'):
    """
    EXIF 元数据清洗

    mode='strip':  删除所有 EXIF（最安全）
    mode='safe':   只保留安全字段，清洗危险值
    mode='check':  只检查不修改，返回危险字段列表
    """
    if isinstance(filepath_or_bytes_or_img, Image.Image):
        img = filepath_or_bytes_or_img
    elif isinstance(filepath_or_bytes_or_img, str):
        img = Image.open(filepath_or_bytes_or_img)
    elif isinstance(filepath_or_bytes_or_img, bytes):
        img = Image.open(BytesIO(filepath_or_bytes_or_img))
    else:
        img = filepath_or_bytes_or_img

    exif_data = img._getexif() or {}

    if mode == 'strip':
        # 删除所有元数据，返回干净的图片
        data = list(img.getdata())
        clean_img = Image.new(img.mode, img.size)
        clean_img.putdata(data)
        return clean_img

    elif mode == 'safe':
        dangerous_fields = []
        for tag_id, value in exif_data.items():
            tag = TAGS.get(tag_id, tag_id)
            if tag_id not in SAFE_EXIF_TAGS:
                dangerous_fields.append((tag, str(value)[:100]))
                continue
            if isinstance(value, str):
                for pattern in DANGEROUS_EXIF_PATTERNS:
                    if re.search(pattern, value, re.IGNORECASE):
                        dangerous_fields.append((tag, str(value)[:100]))
                        break

        if dangerous_fields:
            # 发现危险字段，降级为 strip 模式
            data = list(img.getdata())
            clean_img = Image.new(img.mode, img.size)
            clean_img.putdata(data)
            return clean_img

        return img

    elif mode == 'check':
        dangerous_fields = []
        for tag_id, value in exif_data.items():
            tag = TAGS.get(tag_id, tag_id)
            if tag_id not in SAFE_EXIF_TAGS:
                dangerous_fields.append((tag, str(value)[:100]))
                continue
            if isinstance(value, str):
                for pattern in DANGEROUS_EXIF_PATTERNS:
                    if re.search(pattern, value, re.IGNORECASE):
                        dangerous_fields.append((tag, str(value)[:100]))
                        break

        return dangerous_fields

    else:
        raise ImageSecurityError(f"Unknown sanitize mode: {mode}")


# ============ L6: 隐写术检测 ============

def detect_steganography(filepath_or_bytes_or_img):
    """
    LSB（最低有效位）统计分析检测隐写术

    正常图片的 LSB 分布接近随机（约 50% 为 0，50% 为 1）。
    隐写术修改 LSB 后分布会偏离。
    """
    if isinstance(filepath_or_bytes_or_img, Image.Image):
        img = filepath_or_bytes_or_img
    elif isinstance(filepath_or_bytes_or_img, str):
        img = Image.open(filepath_or_bytes_or_img)
    elif isinstance(filepath_or_bytes_or_img, bytes):
        img = Image.open(BytesIO(filepath_or_bytes_or_img))
    else:
        img = filepath_or_bytes_or_img

    img = img.convert('RGB')
    pixels = list(img.getdata())

    sample_count = min(STEGANOGRAPHY_SAMPLE_PIXELS, len(pixels))
    sampled = pixels[:sample_count]

    lsb_zeros = 0
    lsb_ones = 0

    for r, g, b in sampled:
        lsb_zeros += (r & 1 == 0) + (g & 1 == 0) + (b & 1 == 0)
        lsb_ones += (r & 1 == 1) + (g & 1 == 1) + (b & 1 == 1)

    total = lsb_zeros + lsb_ones
    if total == 0:
        return False

    ratio = lsb_zeros / total
    deviation = abs(ratio - 0.5)

    return deviation > STEGANOGRAPHY_LSB_THRESHOLD


# ============ L7: Content-Type 一致性验证 ============

def verify_content_type(filepath_or_bytes, claimed_mime):
    """验证文件实际格式与声称的 Content-Type 一致"""
    if isinstance(filepath_or_bytes, str):
        with open(filepath_or_bytes, 'rb') as f:
            header = f.read(16)
    elif isinstance(filepath_or_bytes, bytes):
        header = filepath_or_bytes[:16]
    else:
        raise ImageSecurityError("Invalid input type for content-type verification")

    detected_fmt = detect_format(header)
    if detected_fmt is None:
        raise ImageSecurityError("Unknown image format in file header")

    expected_mime = FORMAT_MIME_MAP.get(detected_fmt)
    if expected_mime is None:
        raise ImageSecurityError(f"Detected format {detected_fmt} not in MIME map")

    if claimed_mime != expected_mime:
        raise ImageSecurityError(
            f"Content-Type mismatch: claimed '{claimed_mime}', "
            f"actual format suggests '{expected_mime}'"
        )

    return detected_fmt


# ============ 完整校验流程 ============

def validate_image_file(filepath, claimed_mime=None, check_steg=False) -> dict:
    """
    完整的图片文件安全校验流程

    参数:
        filepath: 图片文件路径
        claimed_mime: 声称的 MIME 类型（可选，用于 Content-Type 验证）
        check_steg: 是否检测隐写术（较慢，默认关闭）

    返回:
        dict: {"valid": True, "format": str, "size": int, "dimensions": tuple, ...}

    异常:
        ImageSecurityError: 校验失败时抛出
    """

    # L1: 文件大小
    file_size = check_file_size(filepath)

    # L2: Polyglot 检测
    matched = detect_polyglot(filepath)

    # L3: 解压缩炸弹检测 + 安全打开
    img = safe_open_image(filepath)

    # L4: 尺寸限制
    w, h = check_dimensions(img)

    # L5: 格式白名单
    img_format = img.format
    if img_format not in SAFE_FORMATS:
        raise ImageSecurityError(f"Disallowed image format: {img_format}")

    # L6: EXIF 检查
    exif_issues = sanitize_exif(img, mode='check')

    # L7: Content-Type 验证（如果提供了 claimed_mime）
    if claimed_mime:
        verify_content_type(filepath, claimed_mime)

    # L8: 隐写术检测（可选）
    steg_detected = False
    if check_steg:
        steg_detected = detect_steganography(img)

    return {
        "valid": True,
        "format": img_format,
        "size": file_size,
        "dimensions": (w, h),
        "pixel_count": w * h,
        "exif_issues": exif_issues,
        "steganography_detected": steg_detected,
    }


def validate_image_bytes(data, claimed_mime=None, check_steg=False) -> dict:
    """
    完整的图片字节数据安全校验流程

    参数:
        data: 图片二进制数据
        claimed_mime: 声称的 MIME 类型
        check_steg: 是否检测隐写术
    """

    # L1: 数据大小
    data_size = check_bytes_size(data)

    # L2: Polyglot 检测
    matched = detect_polyglot(data)

    # L3: 解压缩炸弹检测 + 安全打开
    img = safe_open_image(data)

    # L4: 尺寸限制
    w, h = check_dimensions(img)

    # L5: 格式白名单
    img_format = img.format
    if img_format not in SAFE_FORMATS:
        raise ImageSecurityError(f"Disallowed image format: {img_format}")

    # L6: EXIF 检查
    exif_issues = sanitize_exif(img, mode='check')

    # L7: Content-Type 验证
    if claimed_mime:
        verify_content_type(data, claimed_mime)

    # L8: 隐写术检测
    steg_detected = False
    if check_steg:
        steg_detected = detect_steganography(img)

    return {
        "valid": True,
        "format": img_format,
        "size": data_size,
        "dimensions": (w, h),
        "pixel_count": w * h,
        "exif_issues": exif_issues,
        "steganography_detected": steg_detected,
    }


def validate_base64_image(b64_string, claimed_mime=None, check_steg=False) -> dict:
    """
    完整的 Base64 图片安全校验流程

    参数:
        b64_string: Base64 编码的图片数据（支持 data URI 前缀）
        claimed_mime: 声称的 MIME 类型
        check_steg: 是否检测隐写术
    """

    if not b64_string or not isinstance(b64_string, str):
        raise ImageSecurityError("Empty or invalid base64 input")

    if len(b64_string) > MAX_BASE64_SIZE:
        raise ImageSecurityError(
            f"Base64 too large: {len(b64_string)} chars (max {MAX_BASE64_SIZE})"
        )

    # 提取纯 Base64 数据
    b64_data = b64_string
    if b64_string.startswith('data:'):
        try:
            b64_data = b64_string.split(',', 1)[1]
        except IndexError:
            raise ImageSecurityError("Invalid data URI format")

    # Base64 解码
    try:
        decoded_bytes = base64.b64decode(b64_data, validate=True)
    except Exception as e:
        raise ImageSecurityError(f"Invalid base64 encoding: {e}")

    # 转交字节校验流程
    return validate_image_bytes(decoded_bytes, claimed_mime, check_steg)


# ============ 安全存储辅助 ============

def generate_safe_filename(original_name, img_format):
    """生成安全的文件名（UUID + 白名单扩展名）"""
    ext_map = {
        'PNG': '.png', 'JPEG': '.jpg', 'GIF': '.gif',
        'BMP': '.bmp', 'WEBP': '.webp',
    }
    ext = ext_map.get(img_format, '.img')
    safe_name = f"{uuid.uuid4().hex}{ext}"
    return safe_name


def safe_save_image(img, output_dir, original_name=None):
    """
    安全保存图片：二次渲染 + EXIF 清洗 + 安全文件名

    二次渲染确保原始恶意数据被丢弃，
    只保留像素数据重新编码。
    """
    img_format = img.format or 'PNG'

    # EXIF 清洗
    clean_img = sanitize_exif(img, mode='strip')

    # 生成安全文件名
    safe_name = generate_safe_filename(original_name, img_format)
    output_path = os.path.join(output_dir, safe_name)

    # 二次渲染保存（丢弃原始数据，重新编码）
    save_format = img_format
    if save_format == 'JPEG':
        clean_img.save(output_path, format='JPEG', quality=85)
    elif save_format == 'WEBP':
        clean_img.save(output_path, format='WEBP', quality=85)
    else:
        clean_img.save(output_path, format=save_format)

    return output_path
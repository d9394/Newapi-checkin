#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图形验证码识别模块

支持 new-api 系列的图形验证码接口（已适配 sheapi.cc 实测接口）:

    GET  /api/captcha?scene=checkin   -> {"data": {"captcha_id": "...", "image": "data:image/png;base64,..."}}
    POST /api/user/checkin            -> {"captcha_code": "<识别结果>", "captcha_id": "<id>"}
    POST /api/user/login              -> {"username", "password", "captcha_code", "captcha_id"}

设计要点:
    - ddddocr 为可选依赖，未安装时整个模块降级为不可用（不影响其它站点签到）
    - 验证码一次性消耗，识别错误必须重新取图，不能复用 captcha_id
    - 识别结果按长度/字符集做基本校验，明显异常直接换图，省一次网络往返
"""

import base64
import os
import re
import time
from typing import Optional, Tuple

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

try:
    import ddddocr
    OCR_AVAILABLE = True
except ImportError:
    ddddocr = None
    OCR_AVAILABLE = False

# 图形验证码默认场景名（new-api 约定）
DEFAULT_SCENE = 'checkin'

# 已知站点的验证码固定位数；未列出的站点自动探测
KNOWN_CODE_LENGTH = {
    'sheapi.cc': 4,
}

# 识别结果合法字符集：数字 + 大小写字母
_VALID_CODE = re.compile(r'^[0-9A-Za-z]{2,8}$')

# 单例：ddddocr 初始化要加载 onnx 模型，多次实例化很慢
_ocr_engine = None


def _get_engine():
    """惰性初始化 ddddocr 引擎（全局复用）"""
    global _ocr_engine
    if _ocr_engine is None:
        # show_ad=False 关掉启动时的广告输出，避免污染 Actions 日志
        _ocr_engine = ddddocr.DdddOcr(show_ad=False)
    return _ocr_engine


def is_available() -> bool:
    """OCR 能力是否可用（ddddocr 已安装）"""
    return OCR_AVAILABLE and requests is not None


def unavailable_reason() -> str:
    if requests is None:
        return '未安装 requests'
    if not OCR_AVAILABLE:
        return '未安装 ddddocr（pip install ddddocr）'
    return ''


def decode_image(data_url: str) -> Optional[bytes]:
    """把 data:image/png;base64,xxx 或裸 base64 解成字节"""
    if not data_url:
        return None
    try:
        payload = data_url.split(',', 1)[-1] if data_url.startswith('data:') else data_url
        return base64.b64decode(payload)
    except Exception:
        return None


def recognize(image_bytes: bytes) -> str:
    """识别单张验证码图片，返回文本（失败返回空串）"""
    if not image_bytes or not OCR_AVAILABLE:
        return ''
    try:
        text = _get_engine().classification(image_bytes) or ''
    except Exception:
        return ''
    text = text.strip().replace(' ', '')
    return text if _VALID_CODE.match(text) else ''


def fetch_captcha(session, base_url: str, scene: str = DEFAULT_SCENE,
                  timeout: int = 20) -> Optional[Tuple[str, bytes]]:
    """
    取一张验证码图片。

    Returns:
        (captcha_id, image_bytes) 或 None
    """
    if requests is None:
        return None

    endpoints = ('/api/captcha', '/api/verification')
    for ep in endpoints:
        try:
            resp = session.get(f'{base_url.rstrip("/")}{ep}',
                               params={'scene': scene}, timeout=timeout)
            if resp.status_code != 200:
                continue
            data = resp.json()
            if not data.get('success'):
                continue
            payload = data.get('data') or {}
            captcha_id = (payload.get('captcha_id') or payload.get('captchaId')
                          or payload.get('id') or '')
            image = payload.get('image') or payload.get('img') or ''
            raw = decode_image(image)
            if captcha_id and raw:
                return captcha_id, raw
        except Exception:
            continue
    return None


def expected_length(base_url: str) -> Optional[int]:
    """按域名查已知的验证码位数，未知返回 None（不启用位数校验）"""
    try:
        host = (base_url or '').split('//', 1)[-1].split('/', 1)[0].lower()
        host = host.split(':', 1)[0]
        for domain, length in KNOWN_CODE_LENGTH.items():
            if host == domain or host.endswith('.' + domain):
                return length
    except Exception:
        pass
    return None


def solve_once(session, base_url: str, scene: str = DEFAULT_SCENE,
               timeout: int = 20, expect_len: Optional[int] = None,
               local_retries: int = 3) -> Optional[dict]:
    """
    取图 + 识别，返回可直接并入请求体的字段。

    验证码位数固定时传 expect_len（如 4），识别位数不符会在本地重新取图，
    避免白白消耗一次网络请求（漏字的图必然判错）。

    Returns:
        {'captcha_id': ..., 'captcha_code': ...} 或 None
    """
    for _ in range(max(1, local_retries)):
        got = fetch_captcha(session, base_url, scene, timeout)
        if not got:
            continue
        captcha_id, raw = got
        code = recognize(raw)
        if not code:
            continue
        if expect_len and len(code) != expect_len:
            continue
        return {'captcha_id': captcha_id, 'captcha_code': code}
    return None


def with_captcha_retry(session, base_url: str, do_request, scene: str = DEFAULT_SCENE,
                       max_attempts: int = 6, timeout: int = 25,
                       retry_delay: float = 0.6, expect_len: Optional[int] = None):
    """
    带验证码重试地执行请求。

    每次失败（验证码错误/过期）都会重新取一张新图，因为 captcha_id 是一次性的。

    Args:
        expect_len: 验证码固定位数（如 4），识别位数不符会在本地重取，不消耗网络请求
        do_request: 接收 captcha 字段 dict，返回 (ok: bool, data: dict, msg: str)

    Returns:
        (ok, data, message, attempts)
    """
    if not is_available():
        return False, {}, f'图形验证码无法识别: {unavailable_reason()}', 0

    if expect_len is None:
        expect_len = expected_length(base_url)

    last_msg = ''
    for attempt in range(1, max_attempts + 1):
        fields = solve_once(session, base_url, scene, timeout, expect_len=expect_len)
        if not fields:
            last_msg = '获取或识别验证码失败'
            time.sleep(retry_delay)
            continue

        ok, data, msg = do_request(fields)
        if ok:
            return True, data, msg, attempt

        last_msg = msg or '未知错误'
        # 只有验证码本身的问题才值得换图重试
        if not _is_captcha_error(last_msg):
            return False, data, last_msg, attempt
        time.sleep(retry_delay)

    return False, {}, f'{last_msg}（已重试 {max_attempts} 次）', max_attempts


def _is_captcha_error(message: str) -> bool:
    """判断错误信息是否属于验证码问题（值得换图重试）"""
    if not message:
        return False
    low = message.lower()
    keys = ('验证码', 'captcha', 'expired', '过期', '错误')
    return any(k in low for k in keys)

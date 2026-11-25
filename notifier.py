# -*- coding: utf-8 -*-
"""
通知推送模块
支持邮件推送、ServerChan 推送和通用 Webhook 推送
"""

import os
import json
import re
import hmac
import time
import base64
import smtplib
import hashlib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import Optional, List, Dict, Any
from urllib.parse import quote, quote_plus, urlsplit, urlencode, urlunsplit, parse_qsl

try:
    import requests
except ImportError:
    requests = None


def format_quota(quota: int) -> str:
    """格式化额度显示"""
    if quota >= 1000000:
        return f'{quota / 1000000:.2f}M'
    elif quota >= 1000:
        return f'{quota / 1000:.2f}K'
    else:
        return str(quota)


def build_report_text(results: List[Dict[str, Any]], execution_time: str) -> str:
    """构建纯文本报告内容"""
    success_list = [r for r in results if r.get('success')]
    fail_list = [r for r in results if not r.get('success')]

    lines = [
        'NewAPI 签到报告',
        f'执行时间: {execution_time}',
        '',
        '-' * 40,
        ''
    ]

    if success_list:
        lines.append(f'✓ 成功 ({len(success_list)}个):')
        for r in success_list:
            name = r.get('name', '未知账号')
            quota = r.get('quota_awarded') or 0
            quota_str = f'+{format_quota(quota)}' if quota > 0 else '-'
            checkin_count = r.get('checkin_count')
            detail = f'已签 {checkin_count} 天' if checkin_count else r.get('message', '成功')
            lottery = ' / '.join(r.get('lottery', [])) or '-'
            lines.append(f'  {name} | {quota_str} | {detail} | {lottery}')
        lines.append('')

    if fail_list:
        lines.append(f'✗ 失败 ({len(fail_list)}个):')
        for r in fail_list:
            name = r.get('name', '未知账号')
            message = r.get('message', '未知错误')
            if r.get('session_expired') or 'session' in message.lower() or '认证' in message:
                message = f'[Session失效] {message}'
            lines.append(f'  {name} | {message}')
            if r.get('advice'):
                lines.append(f'  💡 建议: {r["advice"]}')
        lines.append('')

    lines.append('-' * 40)
    total = len(results)
    success_count = len(success_list)
    fail_count = len(fail_list)

    if fail_count == 0:
        lines.append(f'汇总: 全部成功 ({success_count}/{total})')
    elif success_count == 0:
        lines.append(f'汇总: 全部失败 ({fail_count}/{total})')
    else:
        lines.append(f'汇总: 成功 {success_count}，失败 {fail_count}')

    expired_accounts = [r for r in fail_list if r.get('session_expired') or
                        'session' in r.get('message', '').lower() or
                        '认证' in r.get('message', '')]
    if expired_accounts:
        lines.append('')
        lines.append('注意: 部分账号 Session 已失效，请及时更新 Cookie！')

    return '\n'.join(lines)


def build_report_html(results: List[Dict[str, Any]], execution_time: str) -> str:
    """构建 HTML 格式报告"""
    success_list = [r for r in results if r.get('success')]
    fail_list = [r for r in results if not r.get('success')]

    rows = ''

    if success_list:
        rows += '<tr><td colspan="3" style="background:#f0fff4;font-weight:bold;padding:6px 10px;">'
        rows += f'✅ 成功 ({len(success_list)}个)</td></tr>'
        for r in success_list:
            name = r.get('name', '未知账号')
            quota = r.get('quota_awarded') or 0
            quota_str = f'+{format_quota(quota)}' if quota > 0 else '-'
            checkin_count = r.get('checkin_count')
            detail = f'已签 {checkin_count} 天' if checkin_count else r.get('message', '成功')
            lottery = ' / '.join(r.get('lottery', [])) or '-'
            rows += f'<tr><td style="padding:4px 10px;">{name}</td>'
            rows += f'<td style="padding:4px 10px;">{quota_str}</td>'
            rows += f'<td style="padding:4px 10px;">{detail}</td>'
            rows += f'<td style="padding:4px 10px;">{lottery}</td></tr>'

    if fail_list:
        rows += '<tr><td colspan="4" style="background:#fff5f5;font-weight:bold;padding:6px 10px;">'
        rows += f'❌ 失败 ({len(fail_list)}个)</td></tr>'
        for r in fail_list:
            name = r.get('name', '未知账号')
            message = r.get('message', '未知错误')
            if r.get('session_expired') or 'session' in message.lower() or '认证' in message:
                message = f'⚠️ {message}'
            rows += f'<tr><td style="padding:4px 10px;">{name}</td>'
            rows += f'<td colspan="2" style="padding:4px 10px;color:#e53e3e;">{message}</td></tr>'
            if r.get('advice'):
                rows += (f'<tr><td colspan="3" style="padding:2px 10px 6px;color:#718096;font-size:12px;">'
                         f'💡 {r["advice"]}</td></tr>')

    total = len(results)
    success_count = len(success_list)
    fail_count = len(fail_list)

    if fail_count == 0:
        summary = f'✅ 全部成功 ({success_count}/{total})'
    elif success_count == 0:
        summary = f'❌ 全部失败 ({fail_count}/{total})'
    else:
        summary = f'成功 {success_count}，失败 {fail_count}'

    html = f'''<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;padding:20px;color:#333;">
<div style="max-width:600px;margin:0 auto;background:#fff;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;">
<div style="background:#667eea;padding:16px 20px;">
<h2 style="margin:0;color:#fff;font-size:18px;">📋 NewAPI 签到报告</h2>
</div>
<div style="padding:16px 20px;">
<p style="color:#718096;font-size:14px;margin:0 0 12px 0;">执行时间: {execution_time}</p>
<table style="width:100%;border-collapse:collapse;font-size:14px;">
{rows}
</table>
<hr style="border:none;border-top:1px solid #e2e8f0;margin:12px 0;">
<p style="font-size:14px;margin:0;"><strong>{summary}</strong></p>
'''
    expired_accounts = [r for r in fail_list if r.get('session_expired') or
                        'session' in r.get('message', '').lower() or
                        '认证' in r.get('message', '')]
    if expired_accounts:
        html += '<p style="color:#e53e3e;font-size:13px;margin:8px 0 0 0;">⚠️ 部分账号 Session 已失效，请及时更新 Cookie！</p>'

    html += '''
</div>
</div>
</body>
</html>'''
    return html


def _build_report_title(results: List[Dict[str, Any]]) -> str:
    """构建通知标题"""
    success_count = len([r for r in results if r.get('success')])
    fail_count = len([r for r in results if not r.get('success')])
    if fail_count == 0:
        return f'✅ NewAPI 签到成功 ({success_count}个账号)'
    elif success_count == 0:
        return f'❌ NewAPI 签到失败 ({fail_count}个账号)'
    return f'📋 NewAPI 签到完成 (成功{success_count}/失败{fail_count})'


def build_report_markdown(results: List[Dict[str, Any]], execution_time: str) -> str:
    """构建 Markdown 格式报告（ServerChan / Webhook / 企业微信 等复用）"""
    lines = [f'**执行时间**: {execution_time}', '']

    success_list = [r for r in results if r.get('success')]
    if success_list:
        lines.append(f'### ✅ 成功 ({len(success_list)}个)')
        lines.append('')
        lines.append('| 账号 | 奖励 | 结果 | 已签 | 抽奖 |')
        lines.append('|------|------|------|------|------|')
        for r in success_list:
            name = r.get('name', '未知账号')
            quota = r.get('quota_awarded') or 0
            quota_str = f'+{format_quota(quota)}' if quota > 0 else '-'
            message = r.get('message', '成功')
            checkin_count = r.get('checkin_count')
            days = f'已签 {checkin_count} 天' if checkin_count else '-'
            lottery = ' / '.join(r.get('lottery', [])) or '-'
            lines.append(f'| {name} | {quota_str} | {message} | {days} | {lottery} |')
        lines.append('')

    fail_list = [r for r in results if not r.get('success')]
    if fail_list:
        lines.append(f'### ❌ 失败 ({len(fail_list)}个)')
        lines.append('')
        lines.append('| 账号 | 原因 |')
        lines.append('|------|------|')
        for r in fail_list:
            name = r.get('name', '未知账号')
            message = r.get('message', '未知错误')
            if r.get('session_expired') or 'session' in message.lower() or '认证' in message:
                message = f'⚠️ {message}'
            lines.append(f'| {name} | {message} |')
        lines.append('')

    total = len(results)
    success_count = len(success_list)
    fail_count = len(fail_list)
    if fail_count == 0:
        lines.append(f'**汇总**: 全部成功 ✨ ({success_count}/{total})')
    elif success_count == 0:
        lines.append(f'**汇总**: 全部失败 ⚠️ ({fail_count}/{total})')
    else:
        lines.append(f'**汇总**: 成功 {success_count}，失败 {fail_count}')

    expired_accounts = [r for r in fail_list if (
        r.get('session_expired') or
        'session' in r.get('message', '').lower() or
        '认证' in r.get('message', '')
    )]
    if expired_accounts:
        lines.append('')
        lines.append('> ⚠️ **注意**: 部分账号 Session 已失效，请及时更新 Cookie！')

    return '\n'.join(lines)


def build_report_plain(results: List[Dict[str, Any]], execution_time: str) -> str:
    """构建适合 IM 展示的纯文本报告（无表格，企业微信/飞书/Bark 通用）"""
    lines = [f'执行时间: {execution_time}', '']

    success_list = [r for r in results if r.get('success')]
    fail_list = [r for r in results if not r.get('success')]

    if success_list:
        lines.append(f'✅ 成功 {len(success_list)} 个')
        for r in success_list:
            name = r.get('name', '未知账号')
            quota = r.get('quota_awarded') or 0
            quota_str = f'+{format_quota(quota)}' if quota > 0 else '-'
            checkin_count = r.get('checkin_count')
            detail = f'已签 {checkin_count} 天' if checkin_count else r.get('message', '成功')
            lines.append(f'· {name}: {quota_str} {detail}')

    if fail_list:
        if success_list:
            lines.append('')
        lines.append(f'❌ 失败 {len(fail_list)} 个')
        for r in fail_list:
            name = r.get('name', '未知账号')
            message = r.get('message', '未知错误')
            if r.get('session_expired') or 'session' in message.lower() or '认证' in message:
                message = f'[Session失效] {message}'
            lines.append(f'· {name}: {message}')

    total = len(results)
    lines.append('')
    if not fail_list:
        lines.append(f'汇总: 全部成功 ({len(success_list)}/{total})')
    elif not success_list:
        lines.append(f'汇总: 全部失败 ({len(fail_list)}/{total})')
    else:
        lines.append(f'汇总: 成功 {len(success_list)}，失败 {len(fail_list)}')

    expired = [r for r in fail_list if (
        r.get('session_expired') or
        'session' in r.get('message', '').lower() or
        '认证' in r.get('message', '')
    )]
    if expired:
        lines.append('⚠️ 部分账号 Session 已失效，请及时更新 Cookie！')

    return '\n'.join(lines)


def send_email_notification(results: List[Dict[str, Any]], execution_time: Optional[str] = None) -> bool:
    """
    发送邮件通知

    环境变量配置:
        EMAIL_SMTP_HOST: SMTP 服务器地址
        EMAIL_SMTP_PORT: SMTP 服务器端口 (默认 465)
        EMAIL_USER: SMTP 用户名
        EMAIL_PASS: SMTP 密码/授权码
        EMAIL_TO: 收件人地址
        EMAIL_FROM: 发件人地址 (可选，默认与 EMAIL_USER 相同)
    """
    host = os.environ.get('EMAIL_SMTP_HOST', '')
    port_str = os.environ.get('EMAIL_SMTP_PORT', '465')
    port = int(port_str) if port_str else 465
    user = os.environ.get('EMAIL_USER', '')
    password = os.environ.get('EMAIL_PASS', '')
    to_addr = os.environ.get('EMAIL_TO', '')
    from_addr = os.environ.get('EMAIL_FROM', user)

    if not all([host, user, password, to_addr]):
        print('[邮件通知] 未完整配置邮件参数 (EMAIL_SMTP_HOST/USER/PASS/TO)，跳过通知')
        return False

    if not execution_time:
        execution_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    success_count = len([r for r in results if r.get('success')])
    fail_count = len([r for r in results if not r.get('success')])

    # 构建邮件标题
    if fail_count == 0:
        subject = f'✅ NewAPI 签到成功 ({success_count}个账号)'
    elif success_count == 0:
        subject = f'❌ NewAPI 签到失败 ({fail_count}个账号)'
    else:
        subject = f'📋 NewAPI 签到完成 (成功{success_count}/失败{fail_count})'

    text_content = build_report_text(results, execution_time)
    html_content = build_report_html(results, execution_time)

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = from_addr
    msg['To'] = to_addr
    msg.attach(MIMEText(text_content, 'plain', 'utf-8'))
    msg.attach(MIMEText(html_content, 'html', 'utf-8'))

    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            server.starttls()
        server.login(user, password)
        server.sendmail(from_addr, [to_addr], msg.as_string())
        server.quit()
        print(f'[邮件通知] 发送成功 -> {to_addr}')
        return True
    except Exception as e:
        print(f'[邮件通知] 发送失败: {e}')
        return False


def send_serverchan_notification(results: List[Dict[str, Any]], execution_time: Optional[str] = None) -> bool:
    """
    发送 ServerChan 推送

    环境变量配置:
        SERVERCHAN_SENDKEY: ServerChan SendKey (https://sctapi.ftqq.com/SENDKEY.send)
    """
    send_key = os.environ.get('SERVERCHAN_SENDKEY', '')

    if not send_key:
        print('[ServerChan] 未配置 SERVERCHAN_SENDKEY，跳过通知')
        return False

    if requests is None:
        print('[ServerChan] 错误: 未安装 requests 库')
        return False

    if not execution_time:
        execution_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # 构建标题与内容
    title = _build_report_title(results)
    content = build_report_markdown(results, execution_time)


    url = f'https://sctapi.ftqq.com/{send_key}.send'

    try:
        resp = requests.post(url, data={
            'title': title,
            'desp': content
        }, timeout=15)
        result = resp.json()
        if result.get('code') == 0:
            print(f'[ServerChan] 推送成功')
            return True
        else:
            print(f'[ServerChan] 推送失败: {result.get("message", "未知错误")}')
            return False
    except Exception as e:
        print(f'[ServerChan] 推送异常: {e}')
        return False


# ==================== 通用 Webhook 通知 ====================

_WEBHOOK_TYPES = ('generic', 'wecom', 'dingtalk', 'feishu', 'bark', 'telegram',
                  'query', 'get', 'url', 'form', 'urlencoded', 'post',
                  'path', 'template')

# 消息类参数名：按优先级匹配，命中就替换其值，否则追加 msg
_WEBHOOK_MSG_KEYS = ('msg', 'message', 'text', 'content')

# URL 路径里的消息占位符，如 /api/msg/send/TOKEN/{消息}
_WEBHOOK_PATH_PLACEHOLDERS = ('{消息}', '{msg}', '{message}', '{text}', '{content}')

# 形如 token/密钥的长串（含数字、16 位以上），日志里一律打码
_LONG_TOKEN = re.compile(r'(?=[A-Za-z0-9_\-]*\d)[A-Za-z0-9_\-]{16,}')

_FULL_URL_Q = re.compile(r'(https?://[^\s\'\"?#]+)\?[^\s\'\"]*')
_PATH_Q = re.compile(r'(/[^\s\'\"?#]*)\?[^\s\'\"]*')


def _detect_webhook_type(url: str) -> str:
    """未显式设置 WEBHOOK_TYPE 时，按 URL/环境变量推断类型"""
    if any(p in url for p in _WEBHOOK_PATH_PLACEHOLDERS):
        return 'path'
    try:
        keys = [k.lower() for k, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True)]
    except ValueError:
        keys = []
    if {'msg', 'message', 'text', 'content'} & set(keys):
        return 'query'
    if (os.environ.get('WEBHOOK_EXTRA') or '').strip():
        return 'form'
    return 'generic'


def describe_webhook_config() -> str:
    """返回当前 Webhook 配置的可读状态（已脱敏），用于启动自检日志"""
    url = (os.environ.get('WEBHOOK_URL') or '').strip()
    if not url:
        return '未配置（缺少环境变量 WEBHOOK_URL）'

    kind = (os.environ.get('WEBHOOK_TYPE') or '').strip().lower()
    resolved = kind if kind in _WEBHOOK_TYPES else _detect_webhook_type(url)

    fmt = (os.environ.get('WEBHOOK_FORMAT') or 'full').strip().lower()
    info = [f'type={resolved}', f'format={fmt}', _mask_url(url)]
    extra = (os.environ.get('WEBHOOK_EXTRA') or '').strip().lstrip('?&')
    if extra:
        keys = [k for k, _ in parse_qsl(extra, keep_blank_values=True)]
        info.append('extra={' + ','.join(f'{k}=***' for k in keys) + '}')
    return '已配置 ' + ' '.join(info)


def _mask_path(path: str) -> str:
    """路径分段打码：过长的段（token、消息正文）替换为 ***，保留接口结构"""
    return '/'.join('***' if len(seg) > 12 else seg for seg in path.split('/'))


def _mask_url(url: str) -> str:
    """脱敏 URL：隐藏主机名、路径中的长段和所有查询参数值，可安全写入日志"""
    try:
        parts = urlsplit(url)
        port = f':{parts.port}' if parts.port else ''
        keys = [k for k, _ in parse_qsl(parts.query, keep_blank_values=True)]
        query = ('?' + '&'.join(f'{k}=***' for k in keys)) if keys else ''
        return f'{parts.scheme}://***{port}{_mask_path(parts.path)}{query}'
    except Exception:
        return '***'


def _scrub(text: str, url: str = '') -> str:
    """清洗异常信息：requests/urllib3 的报错会带 path+query，必须抹掉"""
    out = str(text)
    if url:
        out = out.replace(url, _mask_url(url))
        try:
            path = urlsplit(url).path
            if path:
                out = out.replace(path, _mask_path(path))
        except ValueError:
            pass
    out = _FULL_URL_Q.sub(r'\1?***', out)
    out = _PATH_Q.sub(r'\1?***', out)
    out = _LONG_TOKEN.sub('***', out)
    return out


def _inject_message(params: List[Any], text: str) -> List[Any]:
    """把消息写进消息类参数：已有 msg/message/text/content 则替换其值，否则追加 msg"""
    for key in _WEBHOOK_MSG_KEYS:
        if any(k == key for k, _ in params):
            return [(k, text if k == key else v) for k, v in params]
    return list(params) + [('msg', text)]


def _build_query_webhook_url(url: str, text: str, max_url_len: int = 1800) -> str:
    """
    把消息塞进 URL 的 msg 查询参数（GET 方式推送）。

    - 原 URL 已有 msg=xxx 时替换其值，否则自动追加 &msg=
    - 超过 max_url_len（部分服务端/nginx 默认 2KB 限制）时自动截断文本

    注意: URL 会把消息暴露在访问日志里，长文本建议用 form 方式 POST。
    """
    parts = urlsplit(url)
    base_q = parse_qsl(parts.query, keep_blank_values=True)

    def compose(t: str) -> str:
        q = _inject_message(base_q, t)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))

    budget = max_url_len - len(compose(''))
    if budget < 200:
        budget = 200

    encoded_len = len(quote_plus(text))
    if encoded_len > budget:
        keep = max(1, int(len(text) * budget / encoded_len) - 10)
        text = text[:keep] + '...(内容过长已截断)'

    target = compose(text)
    for _ in range(10):
        if len(target) <= max_url_len or len(text) <= 30:
            break
        text = text[:max(20, len(text) - (len(target) - max_url_len) - 10)] + '...(已截断)'
        target = compose(text)

    return target


def _build_form_request(url: str, text: str):
    """
    构造 POST application/x-www-form-urlencoded 请求（推荐，不受 URL 长度限制）。

    参数来源（可叠加）:
        1. URL 查询串里的固定参数，如 http://host:8001/?usr=YOUR_TOKEN&from=newapi-checkin
           → 这些参数会被挪进 POST body，URL 本身保持干净
        2. 环境变量 WEBHOOK_EXTRA，如 WEBHOOK_EXTRA=usr=YOUR_TOKEN&from=newapi-checkin
           → 用于 URL 不想带任何参数（http://host:8001）的场景

    消息写入 msg 参数（已有 msg/message/text/content 则替换其值）。

    返回: (target_url, params)
    """
    parts = urlsplit(url)
    params = parse_qsl(parts.query, keep_blank_values=True)

    extra = (os.environ.get('WEBHOOK_EXTRA') or '').strip().lstrip('?&')
    if extra:
        params += parse_qsl(extra, keep_blank_values=True)

    params = _inject_message(params, text)
    target = urlunsplit((parts.scheme, parts.netloc, parts.path, '', parts.fragment))
    return target, params


def _build_path_webhook_url(url: str, text: str, max_url_len: int = 1800) -> str:
    """
    把消息替换进 URL 路径里的占位符（GET 方式）。

    支持占位符: {消息} {msg} {message} {text} {content}
    例: http://host:8765/api/msg/send/TOKEN/{消息}
        → http://host:8765/api/msg/send/TOKEN/<URL编码后的消息>

    消息按整体做百分号编码（/ 与换行都会被编码，不会破坏路径层级）；
    超出 max_url_len 时按比例裁剪，避免网关 414/431。
    """
    placeholder = next((p for p in _WEBHOOK_PATH_PLACEHOLDERS if p in url), None)

    def compose(t: str) -> str:
        encoded = quote(t, safe='')
        if placeholder:
            return url.replace(placeholder, encoded)
        return url.rstrip('/') + '/' + encoded

    base_len = len(compose(''))
    encoded_len = len(quote(text, safe=''))
    if base_len + encoded_len > max_url_len:
        budget = max(max_url_len - base_len, 1)
        keep = max(1, int(len(text) * budget / max(encoded_len, 1)) - 10)
        text = text[:keep]

    target = compose(text)
    while len(target) > max_url_len and len(text) > 5:
        text = text[:max(5, len(text) - 20)]
        target = compose(text)
    return target


def _sanitize_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """提取可安全外发的字段（绝不包含 session / cookie / token）"""
    safe_keys = ('name', 'success', 'message', 'quota_awarded', 'checkin_count',
                 'lottery', 'session_expired')
    return [{k: r.get(k) for k in safe_keys if k in r} for r in results]


def _clip(text: str, limit: int) -> str:
    """按 UTF-8 字节数截断（企业微信 markdown 上限 4096 字节）"""
    data = text.encode('utf-8')
    if len(data) <= limit:
        return text
    return data[:max(limit - 40, 0)].decode('utf-8', 'ignore') + '\n...(内容过长已截断)'


def _dingtalk_signed_url(url: str, secret: str) -> str:
    """钉钉机器人加签（DINGTALK_SECRET 存在时自动启用）"""
    timestamp = str(round(time.time() * 1000))
    string_to_sign = f'{timestamp}\n{secret}'
    hmac_code = hmac.new(secret.encode('utf-8'), string_to_sign.encode('utf-8'),
                         digestmod=hashlib.sha256).digest()
    sign = quote_plus(base64.b64encode(hmac_code))
    sep = '&' if '?' in url else '?'
    return f'{url}{sep}timestamp={timestamp}&sign={sign}'


def _verify_webhook_response(kind: str, text: str):
    """校验各家返回码，返回 (是否成功, 详情)"""
    try:
        data = json.loads(text) if text else {}
    except (ValueError, TypeError):
        return True, text[:200]

    if kind in ('wecom', 'wechat_work', 'qywx', 'dingtalk'):
        return data.get('errcode', 0) == 0, str(data)[:300]
    if kind == 'feishu':
        return data.get('code', data.get('StatusCode', 0)) == 0, str(data)[:300]
    if kind == 'telegram':
        return bool(data.get('ok')), str(data)[:300]
    if kind == 'bark':
        return data.get('code', 200) == 200, str(data)[:300]

    # query / generic：宽松校验，识别常见的失败标志
    if isinstance(data.get('errcode'), int) and data['errcode'] != 0:
        return False, str(data)[:300]
    if isinstance(data.get('code'), int) and data['code'] not in (0, 200):
        return False, str(data)[:300]
    if data.get('success') is False or data.get('ok') is False:
        return False, str(data)[:300]
    return True, str(data)[:300]


def _build_webhook_request(kind: str, url: str, title: str, markdown: str,
                           plain: str, results: List[Dict[str, Any]],
                           execution_time: str, text: str = ''):
    """返回 (method, url, payload)；text 为按 WEBHOOK_FORMAT 选好的正文"""
    if kind in ('wecom', 'wechat_work', 'qywx'):
        return 'POST', url, {
            'msgtype': 'markdown',
            'markdown': {'content': _clip(f'### {text}', 4000)},
        }

    if kind == 'dingtalk':
        return 'POST', url, {
            'msgtype': 'markdown',
            'markdown': {'title': title, 'text': _clip(f'### {text}', 18000)},
        }

    if kind == 'feishu':
        return 'POST', url, {
            'msg_type': 'text',
            'content': {'text': _clip(text, 18000)},
        }

    if kind == 'bark':
        base = url.rstrip('/')
        return 'GET', f'{base}/{quote_plus(title)}/{quote_plus(_clip(text, 1500))}', None

    if kind in ('query', 'get', 'url'):
        return 'GET', _build_query_webhook_url(url, text), None

    if kind in ('path', 'template'):
        return 'GET', _build_path_webhook_url(url, text), None

    if kind in ('form', 'urlencoded', 'post'):
        target, form = _build_form_request(url, f'{title}\n\n{plain}')
        return 'POST_FORM', target, form

    if kind == 'telegram':
        token = os.environ.get('WEBHOOK_TOKEN', '').strip()
        return 'POST', f'https://api.telegram.org/bot{token}/sendMessage', {
            'chat_id': url,
            'text': _clip(text, 4000),
            'disable_web_page_preview': True,
        }

    # generic：通用字段 + 结构化结果，方便自建服务二次加工
    success_count = len([r for r in results if r.get('success')])
    fail_count = len([r for r in results if not r.get('success')])
    return 'POST', url, {
        'event': 'newapi_checkin',
        'title': title,
        'content': markdown,
        'text': f'{title}\n\n{plain}',
        'execution_time': execution_time,
        'summary': {
            'total': len(results),
            'success': success_count,
            'fail': fail_count,
        },
        'results': _sanitize_results(results),
    }


def send_webhook_notification(results: List[Dict[str, Any]],
                              execution_time: Optional[str] = None,
                              retries: int = 3) -> bool:
    """
    发送通用 Webhook 通知

    环境变量配置:
        WEBHOOK_URL:    Webhook 地址（必填）
        WEBHOOK_TYPE:   form | query | generic | wecom | dingtalk | feishu | bark | telegram
                        （留空则自动识别）
        WEBHOOK_EXTRA:  额外表单参数，如 usr=YOUR_TOKEN&from=newapi-checkin（form 类型可选）
        WEBHOOK_FORMAT: full（默认，完整报告）| short（只发一行摘要，适合长度受限通道）
        WEBHOOK_TOKEN:  仅 telegram 需要；填 bot token，此时 WEBHOOK_URL 填 chat_id

    类型速查:
        form     POST 表单，body: usr=YOUR_TOKEN&from=newapi-checkin&msg=<报告>
        query    GET，报告塞进 URL 的 msg 参数（受 URL 长度限制）
        path     GET，报告替换 URL 路径里的占位符 {消息} / {msg} 等
        generic  POST JSON 结构化数据
        wecom / dingtalk / feishu / bark / telegram  各家机器人

    说明:
        - dingtalk 类型下若同时设置 DINGTALK_SECRET，会自动加签
        - 失败自动重试（2s/4s 退避），异常不会中断主流程
        - 日志中的地址与异常信息均已脱敏
    """
    url = (os.environ.get('WEBHOOK_URL') or '').strip()

    if not url:
        print('[Webhook] 未配置 WEBHOOK_URL，跳过通知')
        return False

    if requests is None:
        print('[Webhook] 错误: 未安装 requests 库')
        return False

    if not execution_time:
        execution_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    kind = (os.environ.get('WEBHOOK_TYPE') or '').strip().lower()
    if not kind:
        # 未显式指定时自动判断
        kind = _detect_webhook_type(url)

    if kind not in _WEBHOOK_TYPES:
        print(f'[Webhook] 未知的 WEBHOOK_TYPE: {kind}，回退为 generic')
        kind = 'generic'

    title = _build_report_title(results)
    markdown = build_report_markdown(results, execution_time)
    plain = build_report_plain(results, execution_time)

    # WEBHOOK_FORMAT=short 只发一行摘要，适合 APRS/短信等长度受限的通道
    fmt = (os.environ.get('WEBHOOK_FORMAT') or 'full').strip().lower()
    if fmt in ('short', 'brief', 'title', 'simple'):
        text = title
    else:
        text = f'{title}\n\n{plain}'

    method, target, payload = _build_webhook_request(
        kind, url, title, markdown, plain, results, execution_time, text
    )

    if kind == 'dingtalk':
        secret = (os.environ.get('DINGTALK_SECRET') or '').strip()
        if secret:
            target = _dingtalk_signed_url(target, secret)

    for attempt in range(1, retries + 1):
        try:
            if method == 'GET':
                resp = requests.get(target, timeout=15)
            elif method == 'POST_FORM':
                # requests 对有序键值对会自动带上
                # Content-Type: application/x-www-form-urlencoded
                resp = requests.post(target, data=payload, timeout=15)
            else:
                resp = requests.post(
                    target, json=payload, timeout=15,
                    headers={'Content-Type': 'application/json; charset=utf-8'},
                )

            body = resp.text or ''
            if resp.status_code != 200:
                raise RuntimeError(f'HTTP {resp.status_code}: {_scrub(body[:200], target)}')

            ok, detail = _verify_webhook_response(kind, body)
            if ok:
                print(f'[Webhook] 推送成功 ({kind}) -> {_mask_url(target)}')
                return True
            raise RuntimeError(detail)

        except Exception as e:
            print(f'[Webhook] 第 {attempt}/{retries} 次推送失败: {_scrub(e, target)}')
            if attempt < retries:
                time.sleep(2 * attempt)

    print('[Webhook] 推送最终失败（签到结果不受影响）')
    return False

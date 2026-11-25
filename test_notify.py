#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Webhook 通知连通性测试（不执行签到，只发一条测试消息）

用法:
    # 从 .env 读取（本地）
    python test_notify.py

    # 或直接传环境变量
    WEBHOOK_URL="http://host:8001/?usr=xxx&from=newapi-checkin&msg=消息" \
    WEBHOOK_TYPE=form python test_notify.py

退出码: 0 表示推送成功, 1 表示失败（方便在 CI 里判断）
"""

import os
import sys
from datetime import datetime

# Windows 控制台(cp936)无法编码 emoji，做降级处理避免脚本直接崩
try:
    sys.stdout.reconfigure(errors='replace')
    sys.stderr.reconfigure(errors='replace')
except Exception:
    pass


def load_env_file(path='.env'):
    """极简 .env 加载，不覆盖已有环境变量"""
    if not os.path.exists(path):
        return
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"\''))
    except Exception as e:
        print(f'[测试] 读取 .env 失败: {e}')


def main() -> int:
    load_env_file()

    # 保证无论从哪个目录执行都能导入同目录的 notifier
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    try:
        from notifier import send_webhook_notification, describe_webhook_config
    except ImportError as e:
        print(f'[测试] 无法导入 notifier: {e}')
        return 1

    print('=' * 52)
    print('Webhook 通知连通性测试')
    print('=' * 52)
    print('配置:', describe_webhook_config())

    if not (os.environ.get('WEBHOOK_URL') or '').strip():
        print('')
        print('请先设置 WEBHOOK_URL，例如:')
        print('  export WEBHOOK_URL="http://host:8001/?usr=xxx&from=newapi-checkin&msg=消息"')
        return 1

    execution_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    results = [
        {'name': '测试账号A', 'success': True, 'message': '签到成功',
         'quota_awarded': 123456, 'checkin_count': 7, 'lottery': ['抽奖+0.1M']},
        {'name': '测试账号B', 'success': False, 'message': '这是测试消息，无需处理',
         'session_expired': False},
    ]

    print('')
    print('正在发送测试消息...')
    ok = send_webhook_notification(results, execution_time)

    print('')
    print('=' * 52)
    print('结果:', '[OK] 推送成功，请检查接收端' if ok else '[FAIL] 推送失败，看上面 [Webhook] 日志')
    print('=' * 52)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())

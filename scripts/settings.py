#!/usr/bin/env python3
"""Validate dispatch values before they can reach shell commands or UCI."""
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit
from password import DEFAULT_KEY


def mask(value):
    if value and os.getenv('GITHUB_ACTIONS') == 'true':
        print('::add-mask::' + value.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A'), flush=True)


def url(value, optional=False):
    if not value and optional:
        return ''
    p = urlsplit(value)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password or p.fragment:
        raise ValueError('下载地址须为不含用户名、密码和片段的 HTTP(S) URL')
    return value


def validate(env):
    hostname = env.get('ROUTER_NAME', 'AW1000')
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', hostname):
        raise ValueError('路由器名称须为 1–63 位字母、数字、连字符，首尾不能为连字符')
    ip = ipaddress.IPv4Address(env.get('LAN_IP') or '192.168.1.1')
    nets = ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')
    if not any(ip in ipaddress.IPv4Network(n) for n in nets) or int(ip) & 255 in (0, 255):
        raise ValueError('LAN IP 须为 RFC1918 私网中可用的 /24 主机地址')
    result = {'hostname': hostname, 'lan_ip': str(ip)}
    for k, default in [('ssid_24g', 'AW1000_2.4G'), ('ssid_5g', 'AW1000_5G')]:
        value = env.get(k.upper()) or default
        if not 1 <= len(value.encode()) <= 32 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('SSID 须为 1–32 字节且不能含控制字符')
        result[k] = value
    wifi = env.get('WIFI_PASSWORD') or '1234567890'
    admin = env.get('ROOT_PASSWORD', '')
    serial = env.get('ROUTER_SERIAL', '').strip()
    if serial and not re.fullmatch(r'ARC[0-9]{10}', serial):
        raise ValueError('机身 SN 须为 ARC 加 10 位数字，或留空自动读取')
    key = env.get('BOARD_STATE_KEY') or DEFAULT_KEY
    for value in (wifi, admin, key, serial):
        mask(value)
    if not 8 <= len(wifi) <= 63 or any(not 32 <= ord(c) <= 126 for c in wifi):
        raise ValueError('Wi-Fi 密码须为 8–63 位可打印 ASCII 字符')
    if admin and (not 8 <= len(admin.encode()) <= 128 or any(ord(c) < 32 or ord(c) == 127 for c in admin)):
        raise ValueError('root 密码须为 8–128 字节，或留空采用 SN 派生')
    if not re.fullmatch(r'[0-9a-fA-F]{64}', key):
        raise ValueError('BOARD_STATE_KEY 须为 64 位十六进制')
    digest = env.get('FIRMWARE_SHA256', '').strip().lower()
    if digest and not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise ValueError('原固件 SHA256 须为 64 位十六进制')
    mode = env.get('PHONE_MODE') or 'bundled'
    if mode not in ('bundled', 'deferred'):
        raise ValueError('电话模式只支持源码包内置 bundled 或关闭 deferred')
    if env.get('PHONE_BUNDLE_URL'):
        raise ValueError('电话驱动只从源码包集成，不再接受外部驱动 URL')
    result.update(wifi_password=wifi, board_state_key=key.lower(),
                  root_password_hash='', router_serial=serial, firmware_sha256=digest, phone_mode=mode,
                  firmware_url=url(env.get('FIRMWARE_URL', ''), optional=True))
    if admin:
        result['root_password_hash'] = subprocess.run(
            ['openssl', 'passwd', '-1', '-stdin'], input=admin+'\n', text=True,
            capture_output=True, check=True).stdout.strip()
        mask(result['root_password_hash'])
    return result


if __name__ == '__main__':
    try:
        result = validate(os.environ)
    except (ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from None
    directory = Path('.private')
    directory.mkdir(mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    target = directory/'settings.json'
    target.write_text(json.dumps(result, ensure_ascii=False))
    target.chmod(0o600)
    print('构建参数校验通过；密码不会写入构建摘要。')

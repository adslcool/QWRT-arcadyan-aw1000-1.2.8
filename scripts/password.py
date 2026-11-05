#!/usr/bin/env python3
"""v1.50 SN-v3 compatible derivation. The default key is recoverable from firmware."""
import argparse
import hashlib
import hmac
import os
import re

DEFAULT_KEY = 'ee559d578652553f5cb396befb736c3f111e659a82985f8ed50f82ded222c515'


def derive(serial, key=DEFAULT_KEY):
    if not re.fullmatch(r'ARC[0-9]{10}', serial):
        raise ValueError('序列号须为 ARC 加 10 位数字')
    if not re.fullmatch(r'[0-9a-fA-F]{64}', key):
        raise ValueError('派生密钥须为 64 位十六进制')
    alphabet = 'abcdefghijklmnopqrstuvwxyz0123456789'
    for counter in range(100):
        message = f'aw1000:root-password:sn-v3:{serial}:{counter}'.encode()
        digest = hmac.new(bytes.fromhex(key), message, hashlib.sha256).digest()
        result = ''.join(alphabet[b % 36] for b in digest if b < 252)[:8]
        if len(result) == 8 and any(c.isdigit() for c in result) and any(c.isalpha() for c in result):
            return result
    raise ValueError('派生失败')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='根据机身 SN 计算 root 密码（SN-v3）')
    parser.add_argument('serial')
    args = parser.parse_args()
    print(derive(args.serial, os.getenv('BOARD_STATE_KEY') or DEFAULT_KEY))

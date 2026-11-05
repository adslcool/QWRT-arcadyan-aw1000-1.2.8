#!/usr/bin/env python3
"""Publish factory, sysupgrade and SHA256 directly to Releases. No Artifacts."""
import json
import os
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def publish():
    version=(ROOT/'VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+',version):raise ValueError('VERSION 格式错误')
    result=json.loads((ROOT/'work/build/result.json').read_text())
    if result['version']!=version:raise ValueError('成品与工程版本不一致')
    run=os.environ['GITHUB_RUN_NUMBER']
    if not run.isdigit():raise ValueError('Invalid run number')
    tag=f'v{version}-build.{run}'
    expected={'factory':f'QWRT-arcadyan-aw1000-{version}-factory.ubi',
              'sysupgrade':f'QWRT-arcadyan-aw1000-{version}-sysupgrade.bin',
              'checksums':f'QWRT-arcadyan-aw1000-{version}.sha256'}
    if any(result.get(k)!=v for k,v in expected.items()):raise ValueError('交付命名与版本不一致')
    assets=[ROOT/'out'/result[k] for k in ('factory','sysupgrade','checksums')]
    actual=sorted(p.name for p in (ROOT/'out').iterdir() if p.is_file())
    if actual!=sorted(p.name for p in assets):raise ValueError('发布目录中存在未审核的额外文件')
    title=f'QWRT-arcadyan-aw1000-{version}'
    subprocess.run(['gh','release','create',tag,*map(str,assets),'--target',os.environ['GITHUB_SHA'],
                    '--title',title,'--notes-file',str(ROOT/'work/build/release-notes.md')],check=True)


if __name__=='__main__':publish()

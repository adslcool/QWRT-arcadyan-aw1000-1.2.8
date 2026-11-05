#!/usr/bin/env python3
"""Unpack an explicitly supplied relocatable prepared SDK, with path/link guards."""
import argparse
from pathlib import Path
import sys
import tarfile
from build import download
from settings import url

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('url');p.add_argument('destination',type=Path);a=p.parse_args()
    target=a.destination.resolve()
    if target.exists():raise SystemExit('SDK 目录已存在')
    target.mkdir(parents=True)
    archive=target.parent/'prepared-sdk.tar'
    download(url(a.url),archive,8*1024**3)
    with tarfile.open(archive,'r:*') as t:
        members=t.getmembers()
        if sum(m.size for m in members)>16*1024**3:raise SystemExit('SDK 解压大小超限')
        t.extractall(target,members=members,filter='data')

#!/usr/bin/env python3
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import phone
import arpbind
from customize import modemdata_sources, MODEMDATA_AT, MODEMDATA_CACHE, MODEMDATA_SERIAL, MODEMDATA_DEFAULTS
from package_source import source_files
ROOT=Path(__file__).resolve().parents[1]


def check():
    lock=json.loads((ROOT/'sources.lock.json').read_text())
    upstream=phone.upstream_files()
    phone.library_files()
    arpbind.upstream_files()
    modemdata_sources()
    for body in (MODEMDATA_CACHE,MODEMDATA_SERIAL,MODEMDATA_DEFAULTS):subprocess.run(['sh','-n'],input=body,text=True,check=True)
    for text in (arpbind.INIT,arpbind.HOTPLUG):subprocess.run(['sh','-n'],input=text,text=True,check=True)
    luac=shutil.which('luac5.1') or shutil.which('luac')
    if not luac:raise ValueError('需要 luac5.1 进行 ARP 绑定语法检查')
    subprocess.run([luac,'-p','-'],input=arpbind.WORKER,text=True,check=True)
    subprocess.run([luac,'-p','-'],input=MODEMDATA_AT,text=True,check=True)
    for name,digest in lock['files'].items():
        data=upstream[name[len('phone/upstream/'):]] if name.startswith('phone/upstream/') else (ROOT/name).read_bytes()
        if hashlib.sha256(data).hexdigest()!=digest:
            raise ValueError('供应文件校验失败：'+name)
    for name,digest in lock.get('phone_git_blobs',{}).items():
        b=upstream[name]
        if hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()!=digest:
            raise ValueError('上游 Git blob 校验失败：'+name)
    for p in (ROOT/'files').rglob('*'):
        if not p.is_file():continue
        b=p.read_bytes()
        if b.startswith(b'#!/bin/sh'):subprocess.run(['sh','-n',str(p)],check=True)
        if p.suffix=='.lua':
            luac=shutil.which('luac5.1') or shutil.which('luac')
            if not luac:raise ValueError('需要 luac5.1 进行 Lua 语法检查')
            subprocess.run([luac,'-p',str(p)],check=True)
        if p.suffix=='.json':json.loads(p.read_text())
        if p.suffix=='.js':subprocess.run(['node','--check',str(p)],check=True)
    for p in (ROOT/'.github/workflows').glob('*.yml'):
        if re.search(r'uses:\s*actions/(?:upload|download)-artifact',p.read_text()):
            raise ValueError('不允许 Actions Artifacts：'+p.name)
    for p in (ROOT/'scripts').glob('*.py'):
        compile(p.read_text(),str(p),'exec')
    if not re.fullmatch(r'\d+\.\d+\.\d+\n?',(ROOT/'VERSION').read_text()):raise ValueError('VERSION 格式错误')
    count=len(source_files(ROOT))
    if count>100:raise ValueError('上传源码文件数量超过 100：'+str(count))
    print('上传源码文件数量：'+str(count)+' / 100；完整电话源码自动解包。')
    print('源码、供应文件校验、Shell/Lua 语法及无 Artifacts 规则检查通过。')


if __name__=='__main__':check()

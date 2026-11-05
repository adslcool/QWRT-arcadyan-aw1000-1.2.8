#!/usr/bin/env python3
"""Build only against a prepared, matching QSDK kernel; never fakes module ABI."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import phone
from build import KERNEL_ABI, VERMAGIC, KERNEL_IMAGE_SHA256


def config(text):
    result={}
    for line in text.splitlines():
        if line.startswith('CONFIG_') and '=' in line:
            k,v=line.split('=',1);result[k]=v
        elif line.startswith('# CONFIG_') and line.endswith(' is not set'):
            result[line[2:-11]]='n'
    return result


def validate_kernel(kernel,cross):
    baseline=(ROOT/'phone/qwrt-kernel.config').read_text()
    current=(kernel/'.config').read_text()
    if config(current)!=config(baseline):raise ValueError('内核配置与原 QWRT 不一致，拒绝生成伪匹配模块')
    release=(kernel/'include/generated/utsrelease.h').read_text()
    if '"5.4.213"' not in release:raise ValueError('准备好的内核版本不是 5.4.213')
    if not (kernel/'Module.symvers').is_file() or not (kernel/'Module.symvers').stat().st_size:
        raise ValueError('缺少原构建导出符号 Module.symvers')
    image=kernel/'arch/arm64/boot/Image'
    if not image.is_file():raise ValueError('缺少原内核 arch/arm64/boot/Image，无法确认 QWRT 构建身份')
    if hashlib.sha256(image.read_bytes()).hexdigest()!=KERNEL_IMAGE_SHA256:
        raise ValueError('SDK 内核 Image 与上传 QWRT 不同；同为 5.4.213 不能视为匹配')
    compiler=subprocess.check_output([cross+'gcc','-dumpfullversion'],text=True).strip()
    if compiler!='7.5.0':raise ValueError('原 QWRT 使用 GCC 7.5.0；请提供匹配的 QSDK 工具链')
    banner=subprocess.check_output([cross+'gcc','--version'],text=True)
    expected=json.loads((ROOT/'sources.lock.json').read_text())['base_kernel_compiler']
    if expected not in banner:raise ValueError('工具链构建标识不匹配，需要 '+expected)
    return compiler


def build(kernel,cross,output):
    baseline=(ROOT/'phone/qwrt-kernel.config').read_text()
    compiler=validate_kernel(kernel,cross)
    work=ROOT/'work/phone-driver'
    if work.exists():raise ValueError('work/phone-driver 已存在，请先清理')
    source=phone.prepare(work)
    module=source/'kernel/aw1000-fxs'
    env=dict(os.environ)
    env.pop('KBUILD_MODPOST_WARN',None)
    env.pop('KBUILD_EXTRA_SYMBOLS',None)
    subprocess.run(['make','-C',str(kernel),'M='+str(module),'ARCH=arm64','CROSS_COMPILE='+cross,'modules'],check=True,env=env)
    binary=(module/'aw1000-fxs.ko').read_bytes()
    vm=re.search(rb'vermagic=([^\0]+)',binary)
    if not vm or vm[1].decode().strip()!=VERMAGIC:raise ValueError('生成模块的 vermagic 不符合原固件')
    undefined=subprocess.check_output([cross+'nm','-u',str(module/'aw1000-fxs.ko')],text=True)
    imports=sorted({parts[1] for line in undefined.splitlines() if len(parts:=line.split())==2 and parts[0]=='U'})
    exports={parts[1] for line in (kernel/'Module.symvers').read_text().splitlines() if len(parts:=line.split())>=2}
    missing=set(imports)-exports
    if missing:raise ValueError('原内核未导出驱动需要的符号：'+', '.join(sorted(missing)))
    manifest={'kernel_package':KERNEL_ABI,'phone_commit':phone.COMMIT,'compiler':compiler,
              'sha256':hashlib.sha256(binary).hexdigest(),
              'kernel_config_sha256':hashlib.sha256(baseline.encode()).hexdigest(),
              'kernel_image_sha256':KERNEL_IMAGE_SHA256,
              'kernel_compiler':json.loads((ROOT/'sources.lock.json').read_text())['base_kernel_compiler'],
              'module_symvers_sha256':hashlib.sha256((kernel/'Module.symvers').read_bytes()).hexdigest(),
              'undefined_symbols':imports,
              'hardware_verified':False}
    output.parent.mkdir(parents=True,exist_ok=True)
    with tarfile.open(output,'w:gz') as t:
        for name,data in [('aw1000-fxs.ko',binary),('manifest.json',json.dumps(manifest,indent=2).encode())]:
            item=tarfile.TarInfo(name);item.size=len(data);item.mode=0o644;t.addfile(item,io.BytesIO(data))
    print('电话驱动包已生成；仍需实机加载与通话验收。')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kernel',required=True,type=Path)
    p.add_argument('--cross',required=True,help='包含完整路径的 CROSS_COMPILE 前缀，以 - 结尾')
    p.add_argument('--output',type=Path,default=ROOT/'out/aw1000-phone-driver.tar.gz')
    a=p.parse_args()
    try:build(a.kernel.resolve(),a.cross,a.output.resolve())
    except (ValueError,FileNotFoundError) as e:raise SystemExit(str(e)) from None

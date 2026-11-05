#!/usr/bin/env python3
"""Build the explicitly experimental AW1000 module using pinned public QSDK."""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import phone
from build import download,get_ubi,sha,KERNEL_ABI,VERMAGIC,KERNEL_IMAGE_SHA256
from build_driver import config
from dissect.squashfs import SquashFS
from fdt import parse
from kernel_exports import prel32_exports,write_symvers,elf_section_size,original_kallsyms
from settings import url

QSDK_REPO='https://github.com/everything411/qsdk.git'
QSDK_BRANCH='12.2.5.751.897'
QSDK_COMMIT='12fde49c2e2f5ed85e27be973a8c2ee9130a586a'
TOOLCHAIN_DIR='gcc-linaro-7.5.0-2019.12-x86_64_aarch64-linux-gnu'
TOOLCHAIN_URL='https://huggingface.co/csukuangfj/sherpa-ncnn-toolchains/resolve/main/'+TOOLCHAIN_DIR+'.tar.xz?download=true'
TOOLCHAIN_SHA256='3b6465fb91564b54bbdf9578b4cc3aa198dd363f7a43820eab06ea2932c8e0bf'
PROFILE='public-qsdk-experimental'
CRITICAL=('CONFIG_ARM64','CONFIG_ARM64_PAGE_SHIFT','CONFIG_ARM64_VA_BITS','CONFIG_SMP',
          'CONFIG_PREEMPT','CONFIG_PREEMPT_COUNT','CONFIG_HZ','CONFIG_MODULES',
          'CONFIG_MODULE_UNLOAD','CONFIG_MODVERSIONS','CONFIG_HAVE_ARCH_PREL32_RELOCATIONS',
          'CONFIG_MODULES_TREE_LOOKUP','CONFIG_DEBUG_LOCK_ALLOC','CONFIG_DEBUG_MUTEXES',
          'CONFIG_LOCKDEP','CONFIG_GCC_PLUGIN_RANDSTRUCT')


def run(args,**kwargs):
    subprocess.run([str(x) for x in args],check=True,**kwargs)


def config_changes(original,current):
    return {key:{'original':original.get(key),'public':current.get(key)}
            for key in sorted(original.keys()|current.keys()) if original.get(key)!=current.get(key)}


def check_critical(original,current):
    changed=[key for key in CRITICAL if original.get(key,'n')!=current.get(key,'n')]
    if changed:raise ValueError('关键配置变化，停止构建：'+', '.join(changed))
    if original.get('CONFIG_MODVERSIONS','n')!='n':
        raise ValueError('不支持从 Image 重建启用 MODVERSIONS 的 CRC 表')


def baseline(source,work):
    ubi=work/'base.ubi';get_ubi(source,ubi)
    if sha(ubi)!='d8ac7f92d17af2c202b6114c8494cee27ede084b0fcba462e267988e2345ef39':
        raise ValueError('需要本项目最初提供的 QWRT UBI 内容')
    volumes=work/'base-volumes'
    run(['ubireader_extract_images','-o',volumes,ubi])
    kernels=list(volumes.rglob('*vol-kernel.ubifs'));roots=list(volumes.rglob('*vol-rootfs.ubifs'))
    if len(kernels)!=1 or len(roots)!=1:raise ValueError('原固件卷不明确')
    fit,_=parse(kernels[0].read_bytes())
    images=[n for n in fit.get('/images').children if n.props.get('type')==b'kernel\0']
    if len(images)!=1 or images[0].props.get('compression')!=b'gzip\0':
        raise ValueError('原 FIT 内核不是预期的 gzip image')
    image=gzip.decompress(images[0].props['data'])
    if hashlib.sha256(image).hexdigest()!=KERNEL_IMAGE_SHA256:
        raise ValueError('原固件 Linux Image 身份不匹配')
    (work/'original-Image').write_bytes(image)
    with roots[0].open('rb') as f:
        sq=SquashFS(f)
        module=sq.get('/lib/modules/5.4.213/gpio-button-hotplug.ko').open().read()
    return image,elf_section_size(module,'.gnu.linkonce.this_module')


def compiler(work,cross):
    if not cross:
        archive=work/'gcc-linaro.tar.xz';download(TOOLCHAIN_URL,archive,256*1024*1024)
        if sha(archive)!=TOOLCHAIN_SHA256:raise ValueError('工具链归档 SHA256 不匹配')
        target=work/'toolchain';target.mkdir()
        with tarfile.open(archive,'r:xz') as t:t.extractall(target,filter='data')
        cross=str(target/TOOLCHAIN_DIR/'bin/aarch64-linux-gnu-')
    version=subprocess.check_output([cross+'gcc','-dumpfullversion'],text=True).strip()
    if version!='7.5.0':raise ValueError('公开 QSDK 实验构建要求实际 GCC 7.5.0')
    return cross,subprocess.check_output([cross+'gcc','--version'],text=True).splitlines()[0]


def build(args):
    work=args.work.resolve()
    if work.exists():raise ValueError('请使用全新工作目录：'+str(work))
    work.mkdir(parents=True)
    source=args.input
    if source is None:
        if not args.firmware_url:raise ValueError('需要原固件 --input 或 --firmware-url')
        source=work/'firmware.bin';download(url(args.firmware_url),source)
    image,original_module_size=baseline(source,work)
    exports=prel32_exports(image)
    relative_base,kallsyms=original_kallsyms(image)
    if len(exports)<1000:raise ValueError('原内核导出记录不足，拒绝继续')
    qsdk=args.qsdk.resolve() if args.qsdk else work/'qsdk'
    if not args.qsdk:run(['git','clone','--depth','1','--single-branch','--branch',QSDK_BRANCH,QSDK_REPO,qsdk])
    commit=subprocess.check_output(['git','-C',str(qsdk),'rev-parse','HEAD'],text=True).strip()
    if commit!=QSDK_COMMIT:raise ValueError('公开 QSDK 提交不匹配')
    kernel=qsdk/'qca/src/linux-5.4'
    if subprocess.check_output(['git','-C',str(qsdk),'status','--porcelain','--','qca/src/linux-5.4'],text=True).strip():
        raise ValueError('公开 QSDK 内核源码有本地修改')
    cross,banner=compiler(work,args.cross)
    out=work/'kernel-build';out.mkdir()
    original_path=ROOT/'phone/qwrt-kernel.config'
    shutil.copyfile(original_path,out/'.config')
    original=config(original_path.read_text())
    env=dict(os.environ)
    for key in ('KBUILD_MODPOST_WARN','KBUILD_EXTRA_SYMBOLS'):env.pop(key,None)
    make=['make','-C',kernel,'O='+str(out),'ARCH=arm64','CROSS_COMPILE='+cross]
    run(make+['olddefconfig','modules_prepare'],env=env)
    current=config((out/'.config').read_text());check_critical(original,current)
    differences=config_changes(original,current)
    write_symvers(exports,out/'Module.symvers')
    prepared=phone.prepare(work/'phone')
    module=prepared/'kernel/aw1000-fxs'
    run(make+['M='+str(module),'modules'],env=env)
    path=module/'aw1000-fxs.ko';binary=path.read_bytes()
    vm=re.search(rb'vermagic=([^\0]+)',binary)
    if not vm or vm[1].decode().strip()!=VERMAGIC:raise ValueError('模块 vermagic 不匹配')
    if b'license=GPL\0' not in binary:raise ValueError('此重建索引仅供 GPL 模块使用')
    size=elf_section_size(binary,'.gnu.linkonce.this_module')
    if size!=original_module_size:raise ValueError('struct module section 大小与原模块不同')
    nm=subprocess.check_output([cross+'nm','-u',str(path)],text=True)
    imports=sorted({p[-1] for line in nm.splitlines() if (p:=line.split()) and p[0]=='U'})
    missing=set(imports)-exports.keys()
    if missing:raise ValueError('原内核未找到导出符号：'+', '.join(sorted(missing)))
    if any(exports[name]['namespace'] for name in imports):raise ValueError('导入了未适配的 namespace')
    checked=[name for name in imports if name in kallsyms]
    if any(kallsyms[name]!=relative_base+exports[name]['value_offset'] for name in checked):
        raise ValueError('导出记录与 kallsyms 中的函数地址不一致')
    version=(ROOT/'VERSION').read_text().strip()
    meta={'schema':2,'version':version,'profile':PROFILE,'compatibility_verified':False,
          'hardware_verified':False,'kernel_package':KERNEL_ABI,'phone_commit':phone.COMMIT,
          'sha256':hashlib.sha256(binary).hexdigest(),'vermagic':VERMAGIC,
          'qsdk_repository':QSDK_REPO,'qsdk_commit':commit,'compiler':banner,
          'kernel_config_sha256':sha(out/'.config'),'original_kernel_config_sha256':sha(original_path),
          'reference_original_image_sha256':KERNEL_IMAGE_SHA256,
          'config_changes':differences,'struct_module_size':size,
          'original_struct_module_size':original_module_size,
          'kallsyms_address_checks':checked,
          'export_index':{'source':'Original QWRT Image PREL32 export records; reconstructed GPL-only modpost index',
                          'count':len(exports),'modversions':False,'original_Module_symvers_available':False},
          'import_checks':[exports[name] for name in imports],
          'limitations':['公开 QSDK 有不同补丁/配置，未证明 C 类型和布局 ABI 完全一致',
                         'Linaro GCC 7.5.0 不是原 QWRT r47815 工具链',
                         '导出记录可证明符号存在，不能证明函数签名或运行正确',
                         '尚未在 QWRT 5.4.213 加载或完成 RJ11 双向通话验收']}
    output=args.output.resolve();output.parent.mkdir(parents=True,exist_ok=True)
    with tarfile.open(output,'w:gz') as t:
        for name,data in [('aw1000-fxs.ko',binary),('manifest.json',json.dumps(meta,ensure_ascii=False,indent=2).encode())]:
            item=tarfile.TarInfo(name);item.size=len(data);item.mode=0o644;t.addfile(item,io.BytesIO(data))
    (output.parent/'sha256sums').write_text(sha(output)+'  '+output.name+'\n')
    (work/'result.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n')
    notes=f'AW1000 RJ11 公开 QSDK 实验驱动 v{version}\n\nLinux 5.4.213；GCC 7.5.0；原 Image 导出符号检查 {len(imports)}/{len(imports)}。\n公开配置差异 {len(differences)} 项；未进行目标 QWRT 加载和通话验收。不能声明与原 QWRT ABI 已匹配。\n'
    (work/'release-notes.md').write_text(notes)
    print(notes+'输出：'+str(output))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path)
    p.add_argument('--firmware-url')
    p.add_argument('--qsdk',type=Path,help='可选，已克隆的固定 QSDK 仓库')
    p.add_argument('--cross',help='可选，现有 GCC 7.5.0 完整 CROSS_COMPILE 前缀')
    p.add_argument('--work',type=Path,default=ROOT/'work/public-driver')
    p.add_argument('--output',type=Path,default=ROOT/'out'/('aw1000-phone-driver-public-v'+(ROOT/'VERSION').read_text().strip()+'.tar.gz'))
    a=p.parse_args()
    try:build(a)
    except (ValueError,FileNotFoundError,subprocess.CalledProcessError) as e:raise SystemExit(str(e)) from None

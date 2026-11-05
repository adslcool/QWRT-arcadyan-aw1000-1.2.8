#!/usr/bin/env python3
"""Repack only the inspected QWRT family. Never flashes the build host or router."""
import argparse
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import stat
import struct
import subprocess
import tarfile
import time
import urllib.request
import zipfile
from dissect.squashfs import SquashFS
from fdt import parse, prepare_led_fit, prepare_phone_fit
import phone
import internet
import arpbind
import led_hardware
from customize import remove_apps, patch_qmodem, patch_interface_names, purge_paths, patch_wifi_scan, install_modemdata
from build_stamp import stamp
from upgrade_image import create_sysupgrade

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT/'VERSION').read_text().strip()
LEB, PEB, MIN_IO = 253952, 262144, 4096
KERNEL_ABI = '5.4.213-1-606f983e9fed006b50a91d394d80f588'
VERMAGIC = '5.4.213 SMP preempt mod_unload aarch64'
KERNEL_IMAGE_SHA256 = '2cf34abbdeff69411f4fa70ed000de28ea83165586441f8315eb75ea25344d9c'


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1024*1024), b''): h.update(b)
    return h.hexdigest()


def run(args, **kw):
    subprocess.run([str(x) for x in args], check=True, **kw)


def download(url, target, maximum=512*1024*1024):
    request = urllib.request.Request(url, headers={'User-Agent': 'AW1000-QWRT-Mod/'+VERSION})
    # urllib honors redirects, including GitHub Releases signed storage URLs.
    with urllib.request.urlopen(request, timeout=90) as src, target.open('wb') as out:
        count=0
        while True:
            b=src.read(1024*1024)
            if not b: break
            count += len(b)
            if count > maximum: raise ValueError('下载文件超过大小上限')
            out.write(b)


def safe_name(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name:
        raise ValueError('压缩包包含不安全路径')
    return str(path)


def get_ubi(source, destination):
    if source.read_bytes()[:4] == b'UBI#':
        shutil.copyfile(source, destination); return
    if not zipfile.is_zipfile(source): raise ValueError('输入只支持 UBI 或 ZIP')
    with zipfile.ZipFile(source) as z:
        entries = [i for i in z.infolist() if not i.is_dir()]
        for i in entries:
            safe_name(i.filename)
            if stat.S_ISLNK(i.external_attr >> 16): raise ValueError('ZIP 符号链接被拒绝')
        candidates = [i for i in entries if i.filename.lower().endswith('.ubi')]
        if len(candidates) != 1: raise ValueError('ZIP 必须只含一个明确的 UBI 固件')
        i = candidates[0]
        if i.file_size > 512*1024*1024: raise ValueError('UBI 文件过大')
        with z.open(i) as src, destination.open('wb') as out: shutil.copyfileobj(src, out)
    if destination.read_bytes()[:4] != b'UBI#': raise ValueError('解压结果不是 UBI')


def extract_squash(source, root):
    """Defer all symlinks; never follows archive-controlled links while writing."""
    links=[]; special=[]
    with open(source, 'rb') as stream:
        sq = SquashFS(stream)
        def walk(node, target, prefix=''):
            target.mkdir(parents=True, exist_ok=True)
            for n in node.iterdir():
                if n.name in ('.','..') or '/' in n.name or '\\' in n.name: raise ValueError('Invalid inode name')
                rel = prefix+'/'+n.name; dest=target/n.name
                if n.uid != 0 or n.gid != 0: raise ValueError('仅支持已验证的 root-owned 根文件系统')
                is_link=n.is_symlink()
                if is_link: links.append((dest,n.link))
                elif n.is_dir(): walk(n, dest, rel)
                elif n.is_file():
                    with n.open() as inp, dest.open('wb') as out: shutil.copyfileobj(inp,out)
                    dest.chmod(stat.S_IMODE(n.mode))
                elif rel == '/dev/console' and n.is_character_device() and n.header.rdev==0x501:
                    special.append('dev/console c 0600 0 0 5 1')
                else: raise ValueError('不支持的设备节点：'+rel)
                if not is_link and (n.is_file() or n.is_dir()):
                    stamp=int(n.mtime.timestamp());os.utime(dest,(stamp,stamp))
            target.chmod(stat.S_IMODE(node.mode))
        walk(sq.root, root)
    for dest, link in links: dest.symlink_to(link)
    return special


def guarded_path(root, name):
    p = root/safe_name(name)
    if not p.resolve().is_relative_to(root.resolve()):
        raise ValueError('输出路径指向根目录之外：'+name)
    return p


def extract_tar_files(blob, root):
    names=[]
    with tarfile.open(fileobj=io.BytesIO(blob),mode='r:*') as t:
        for m in t:
            name=safe_name(m.name)
            p=guarded_path(root,name)
            if m.isdir(): p.mkdir(parents=True,exist_ok=True)
            elif m.isfile():
                p.parent.mkdir(parents=True,exist_ok=True)
                p.write_bytes(t.extractfile(m).read());p.chmod(m.mode&0o777);names.append('/'+name)
            else: raise ValueError('供应包只允许普通文件和目录')
    return names


def install_argon(root):
    package=ROOT/'vendor/luci-theme-argon_2.4.3-r20250722_all.ipk'
    if sha(package) != '4af8715a854511c9b5613b9b7067ff78033670bdbe655e0ad4c012c97a925984':
        raise ValueError('Argon IPK 校验失败')
    with tarfile.open(package,'r:gz') as t:
        data=t.extractfile('./data.tar.gz').read(); control=t.extractfile('./control.tar.gz').read()
    names=extract_tar_files(data,root)
    # Keep opkg's package database consistent; do not execute a package maintainer script on the host.
    info=root/'usr/lib/opkg/info';info.mkdir(parents=True,exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(control),mode='r:gz') as t:
        metadata=t.extractfile('./control').read().decode()
        for m in t:
            if not m.isfile(): continue
            name=PurePosixPath(safe_name(m.name)).name
            if name not in ('control','conffiles','postinst','postinst-pkg','prerm'): raise ValueError('未知 IPK 控制文件')
            p=info/('luci-theme-argon.'+name);p.write_bytes(t.extractfile(m).read());p.chmod(m.mode&0o777)
    (info/'luci-theme-argon.list').write_text('\n'.join(names)+'\n')
    status=root/'usr/lib/opkg/status'
    old=status.read_text() if status.exists() else ''
    records=[s for s in old.split('\n\n') if s.strip() and not s.startswith('Package: luci-theme-argon\n')]
    records.append(metadata.strip()+'\nStatus: install user installed\nInstalled-Time: '+str(int(time.time())))
    status.write_text('\n\n'.join(records)+'\n\n')


def install_librespeed(root):
    """Install the supplied IPK offline, including its required service account."""
    package=ROOT/'vendor/librespeed-go_1.1.5-r5_aarch64_cortex-a53.ipk'
    digest='f907ba56b3e4593354c368a37eecf6c7283b7a8bc25cbc67117871809ca83e5f'
    if sha(package)!=digest:raise ValueError('LibreSpeed IPK SHA256 不符')
    with tarfile.open(package,'r:gz') as t:
        control=t.extractfile('./control.tar.gz').read()
        data=t.extractfile('./data.tar.gz').read()
    with tarfile.open(fileobj=io.BytesIO(control),mode='r:gz') as t:
        metadata=t.extractfile('./control').read().decode()
        fields=dict(line.split(': ',1) for line in metadata.splitlines() if ': ' in line and not line.startswith(' '))
        if (fields.get('Package'),fields.get('Version'),fields.get('Architecture'),fields.get('Depends')) != ('librespeed-go','1.1.5-r5','aarch64_cortex-a53','libc'):
            raise ValueError('LibreSpeed 包身份或依赖与已验证版本不同')
        info=root/'usr/lib/opkg/info';info.mkdir(parents=True,exist_ok=True)
        for m in t:
            if m.isdir():continue
            name=PurePosixPath(safe_name(m.name)).name
            if not m.isfile() or name not in ('control','conffiles','postinst','prerm'):
                raise ValueError('不支持的 LibreSpeed 控制文件')
            p=info/('librespeed-go.'+name);p.write_bytes(t.extractfile(m).read());p.chmod(m.mode&0o777)
    status=root/'usr/lib/opkg/status'
    blocks=[b for b in status.read_text().split('\n\n') if b.strip()]
    if not any(b.startswith('Package: libc\n') for b in blocks):raise ValueError('固件缺少 libc')
    names=extract_tar_files(data,root)
    binary=root/'usr/bin/librespeed-go';elf=binary.read_bytes()
    if elf[:6]!=b'\x7fELF\x02\x01' or struct.unpack_from('<H',elf,18)[0]!=183:
        raise ValueError('LibreSpeed 不是 AArch64 ELF64')
    (info/'librespeed-go.list').write_text('\n'.join(names)+'\n')
    blocks=[b for b in blocks if not b.startswith('Package: librespeed-go\n')]
    blocks.append(metadata.strip()+'\nStatus: install user installed\nInstalled-Time: '+str(int(time.time())))
    status.write_text('\n\n'.join(blocks)+'\n\n')
    cfg=root/'etc/config/librespeed-go';text=cfg.read_text()
    for key,value in [('enabled','1'),('listen_port','8989'),('database_type','none')]:
        text,count=re.subn(r"(?m)^(\s*option\s+"+key+r"\s+).*",lambda m:m[1]+"'"+value+"'",text)
        if count!=1:raise ValueError('LibreSpeed 配置锚点不符：'+key)
    cfg.write_text(text);cfg.chmod(0o600)
    passwd=root/'etc/passwd';group=root/'etc/group';shadow=root/'etc/shadow'
    users=[line.split(':') for line in passwd.read_text().splitlines()]
    groups=[line.split(':') for line in group.read_text().splitlines()]
    user=next((r for r in users if r[0]=='librespeed'),None)
    grp=next((r for r in groups if r[0]=='librespeed'),None)
    def next_id(rows):
        used={int(r[2]) for r in rows};value=65536
        while value in used:value+=1
        return value
    if grp and int(grp[2])==0:raise ValueError('librespeed 组不允许使用 GID 0')
    gid=int(grp[2]) if grp else (int(user[3]) if user else next_id(groups))
    if not grp:
        if gid==0 or any(int(r[2])==gid for r in groups):raise ValueError('librespeed GID 冲突')
        group.write_text(group.read_text()+f'librespeed:x:{gid}:\n')
    if user:
        if int(user[2])==0 or int(user[3])!=gid:raise ValueError('现有 librespeed 账户不安全或组不一致')
    else:
        uid=next_id(users)
        passwd.write_text(passwd.read_text()+f'librespeed:x:{uid}:{gid}:LibreSpeed:/var/run/librespeed-go:/bin/false\n')
        shadow.write_text(shadow.read_text()+'librespeed:!:0:0:99999:7:::\n');shadow.chmod(0o600)
    start=root/'etc/rc.d/S99librespeed-go';start.parent.mkdir(exist_ok=True)
    if start.is_symlink() or start.exists():start.unlink()
    start.symlink_to('../init.d/librespeed-go')
    return {'version':fields['Version'],'ipk_sha256':digest,'enabled':True,'listen_port':8989,
            'database_type':'none','user':'librespeed','hardware_verified':False}


def check_base(root,kernel):
    release=(root/'etc/openwrt_release').read_text()
    for required in ("DISTRIB_ID='QWRT'", "DISTRIB_RELEASE='25.12.2'", "DISTRIB_TARGET='ipq807x/generic'", "DISTRIB_ARCH='aarch64_cortex-a53'"):
        if required not in release: raise ValueError('不支持的原固件：'+required)
    if 'Version: '+KERNEL_ABI not in (root/'usr/lib/opkg/info/kernel.control').read_text():
        raise ValueError('内核 ABI 与已验证基线不同')
    fit,_=parse(kernel.read_bytes())
    for node in fit.get('/images').children:
        if 'data' not in node.props: continue
        for child in node.children:
            algo=child.props.get('algo',b'').rstrip(b'\0').decode()
            data=node.props['data']
            if algo=='crc32':
                import zlib
                digest=struct.pack('>I',zlib.crc32(data))
            elif algo in ('sha1','sha256'):digest=hashlib.new(algo,data).digest()
            else:raise ValueError('未知 FIT 校验类型')
            if digest!=child.props.get('value'):raise ValueError('原 FIT 校验失败')
    required=['usr/bin/openssl','usr/bin/jq','usr/lib/lua/ubus.so','usr/lib/lua/uci.so','usr/lib/lua/nixio.so',
              'usr/share/ucode/luci/dispatcher.uc','usr/share/qmodem/modem_dial.sh']
    for name in required:
        # An absolute firmware symlink cannot be tested against the host filesystem.
        p=root/name
        if not p.is_file() and not p.is_symlink(): raise ValueError('缺少依赖：'+name)


def check_password_runtime(root):
    """Exercise the original firmware's crypt() through its own Lua/nixio."""
    password='AW1000-runtime-test'
    hashed=subprocess.check_output(['openssl','passwd','-1','-salt','aw1000t1','-stdin'],
                                   input=password+'\n',text=True).strip()
    script=('package.cpath='+json.dumps(str(root/'usr/lib/lua/?.so'))+';'
            'local n=require("nixio");local p=io.read("*l");local h=io.read("*l");'
            'if not p or not h or n.crypt(p,h)~=h then os.exit(1) end')
    try:
        subprocess.run(['qemu-aarch64-static','-L',str(root),str(root/'lib/libc.so'),
                        '--library-path',str(root/'lib')+':'+str(root/'usr/lib'),
                        str(root/'usr/bin/lua'),'-e',script],input=password+'\n'+hashed+'\n',
                       text=True,check=True)
    except (OSError,subprocess.CalledProcessError):
        raise ValueError('原 QWRT libc/nixio 的管理密码校验失败；需要 qemu-user-static 和支持的 MD5-crypt 格式') from None
    print('原 QWRT libc/nixio 实际校验 MD5-crypt 通过。',flush=True)


def overlay(root,settings):
    for p in (ROOT/'files').rglob('*'):
        if not p.is_file():continue
        name=str(p.relative_to(ROOT/'files'));dest=guarded_path(root,name)
        dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
        dest.chmod(0o755 if p.read_bytes().startswith(b'#!') else 0o644)
    network=root/'etc/config/network'
    network.write_text(network.read_text().replace("option ipaddr '192.168.1.1'", "option ipaddr '"+settings['lan_ip']+"'"))
    # hotplug.json already dispatches buttons through /etc/rc.button. Remove
    # the parallel hotplug.d path and the vendor handlers (including reset).
    for name in ('50-aw1000-wps','50-wps','51-wps-reset','52-wps-supplicant','54-wps-extender'):
        (root/'etc/hotplug.d/button'/name).unlink(missing_ok=True)
    state=root/'etc/aw1000-mod';state.mkdir(exist_ok=True);state.chmod(0o700)
    (state/'derivation.key').write_text(settings['board_state_key']+'\n');(state/'derivation.key').chmod(0o600)
    if settings.get('router_serial'):
        (state/'serial.hint').write_text(settings['router_serial']+'\n');(state/'serial.hint').chmod(0o600)
    mapping={'ROUTER_NAME':'hostname','LAN_IP':'lan_ip','SSID_24G':'ssid_24g','SSID_5G':'ssid_5g',
             'WIFI_PASSWORD':'wifi_password','ROOT_PASSWORD_HASH':'root_password_hash'}
    conf=root/'etc/aw1000-build.conf'
    conf.write_text(''.join(name+'='+shlex.quote(settings[key])+'\n' for name,key in mapping.items()));conf.chmod(0o600)
    # Lock initial root until the serial-derived or explicit hash is applied by uci-defaults.
    shadow=root/'etc/shadow'; rows=shadow.read_text().splitlines()
    shadow.write_text('\n'.join(':'.join([r.split(':')[0], '!']+r.split(':')[2:]) if r.startswith('root:') else r for r in rows)+'\n');shadow.chmod(0o600)
    for start,service in (('18','aw1000-ports'),('99','aw1000-provision'),('99','aw1000-leds'),('99','fxsd'),('97','mwan3'),('98','aw1000-internet')):
        p=root/'etc/rc.d'/('S'+start+service);p.parent.mkdir(exist_ok=True)
        if p.exists() or p.is_symlink():p.unlink()
        p.symlink_to('../init.d/'+service)
    keep=root/'etc/sysupgrade.conf'
    text=keep.read_text() if keep.exists() else ''
    if '/etc/aw1000-mod/' not in text:text+='\n/etc/aw1000-mod/\n'
    for path in ('/etc/config/aw1000','/etc/config/mwan3','/etc/aw1000-internet-pending/'):
        if path not in text:text+=path+'\n'
    keep.write_text(text)
    # Set the default theme in the image as well as first-boot provisioning.
    p=root/'etc/config/luci'
    text=p.read_text();text=re.sub(r'(option\s+mediaurlbase\s+).*',r'\1/luci-static/argon',text,count=1)
    p.write_text(text)
    (root/'etc/aw1000-mod-version').write_text(VERSION+'\n')


def driver_bundle(path,root,*,allow_experimental=False):
    with tarfile.open(path,'r:*') as t:
        files={}
        for m in t:
            name=safe_name(m.name)
            if m.isdir():continue
            if not m.isfile() or name not in ('manifest.json','aw1000-fxs.ko') or name in files:
                raise ValueError('电话驱动包只能包含 manifest.json 和 aw1000-fxs.ko')
            if m.size>16*1024*1024:raise ValueError('电话驱动包过大')
            files[name]=t.extractfile(m).read()
    meta=json.loads(files['manifest.json']); b=files['aw1000-fxs.ko']
    experimental=meta.get('profile')=='public-qsdk-experimental'
    expected_phone_commit=phone.COMMIT
    if experimental:
        if not allow_experimental:
            raise ValueError('公开 QSDK 实验驱动尚未证明 ABI 匹配，不能按严格匹配驱动自动集成')
        lock=json.loads((ROOT/'sources.lock.json').read_text())
        expected=lock['files'][lock['bundled_phone_driver']['path']]
        if sha(path)!=expected:raise ValueError('内置实验驱动包 SHA256 不匹配')
        driver=lock['bundled_phone_driver']
        expected_phone_commit=driver.get('phone_commit',phone.COMMIT)
        if expected_phone_commit!=phone.COMMIT:
            kernel_sources={name:hashlib.sha256(data).hexdigest()
                            for name,data in phone.upstream_files().items() if name.startswith('kernel/')}
            if not kernel_sources or kernel_sources!=driver.get('kernel_source_sha256'):
                raise ValueError('复用电话驱动的内核源码已变化，必须重新编译驱动')
    if meta.get('kernel_package')!=KERNEL_ABI or meta.get('phone_commit')!=expected_phone_commit:
        raise ValueError('电话包目标内核或源码版本不匹配')
    config_key='original_kernel_config_sha256' if experimental else 'kernel_config_sha256'
    if meta.get(config_key)!=sha(ROOT/'phone/qwrt-kernel.config'):
        raise ValueError('电话包内核配置指纹不匹配')
    image_key='reference_original_image_sha256' if experimental else 'kernel_image_sha256'
    if meta.get(image_key)!=KERNEL_IMAGE_SHA256:
        raise ValueError('电话包未通过原 QWRT 内核 Image 身份校验')
    if hashlib.sha256(b).hexdigest()!=meta.get('sha256'):raise ValueError('电话模块 SHA256 不匹配')
    if len(b)<64 or b[:6]!=b'\x7fELF\x02\x01' or struct.unpack_from('<H',b,18)[0]!=183 or struct.unpack_from('<H',b,16)[0]!=1:
        raise ValueError('电话模块不是 AArch64 ELF64 relocatable')
    vm=re.search(rb'vermagic=([^\0]+)',b)
    if not vm or vm[1].decode().strip()!=VERMAGIC:raise ValueError('电话模块 vermagic 不匹配')
    p=root/'lib/modules/5.4.213/aw1000-fxs.ko';p.write_bytes(b);p.chmod(0o644)
    return meta


def build(source,settings,work,output,cc):
    if settings['phone_mode'] not in ('bundled','deferred') or settings.get('phone_bundle_url'):
        raise ValueError('电话驱动只允许使用源码包内置 bundled 或关闭 deferred')
    if work.exists():raise ValueError('工作目录已存在，请指定新的 --work 或手动清理')
    phone.check_toolchain(cc)
    work.mkdir(parents=True,mode=0o700)
    raw=work/'input.bin'
    if source:shutil.copyfile(source,raw)
    else:
        if not settings['firmware_url']:raise ValueError('请填写原固件 URL 或 --input')
        download(settings['firmware_url'],raw)
    digest=sha(raw)
    if settings['firmware_sha256'] and digest!=settings['firmware_sha256']:raise ValueError('原固件 SHA256 不匹配')
    ubi=work/'base.ubi';get_ubi(raw,ubi)
    info=subprocess.check_output(['ubireader_display_info',str(ubi)],text=True)
    for name,value in [('Min I/O',MIN_IO),('LEB Size',LEB),('PEB Size',PEB)]:
        if not re.search(re.escape(name)+r':\s*'+str(value)+r'\b',info):raise ValueError('UBI 几何不同：'+name)
    volumes=work/'volumes'
    run(['ubireader_extract_images','-o',volumes,ubi])
    kernels=list(volumes.rglob('*vol-kernel.ubifs'));roots=list(volumes.rglob('*vol-rootfs.ubifs'))
    if len(kernels)!=1 or len(roots)!=1:raise ValueError('需要一个 kernel 卷和一个 rootfs 卷')
    kernel=kernels[0]; original_kernel_sha=sha(kernel)
    root=work/'root';special=extract_squash(roots[0],root)
    check_base(root,kernel)
    release_meta=stamp(root, VERSION)
    check_password_runtime(root)
    removed_paths=['/etc/banner','/etc/hotplug.d/button/50-aw1000-wps','/usr/bin/3proxy']
    removed=remove_apps(root,removed_paths)
    patch_qmodem(root)
    patch_interface_names(root)
    scan_meta=patch_wifi_scan(root)
    install_argon(root)
    overlay(root,settings)
    network_meta=internet.install(root,work,cc)
    librespeed_meta=install_librespeed(root)
    run(['qemu-aarch64-static','-L',root,root/'usr/bin/librespeed-go','-h'],timeout=15)
    librespeed_meta['original_qwrt_runtime_load_verified']=True
    arp_meta=arpbind.install(root)
    modemdata_meta=install_modemdata(root)
    # Reintroduced upstream modemdata paths are owned by the new packages.
    modemdata_paths=set()
    for package in ('modemdata','luci-app-modemdata'):
        modemdata_paths.update((root/('usr/lib/opkg/info/'+package+'.list')).read_text().splitlines())
    removed_paths[:]=[p for p in removed_paths if p not in modemdata_paths]
    adapted=phone.prepare(work/'phone')
    phone.install(root,adapted,cc)
    has_phone=False;phone_meta=None
    if settings['phone_mode']=='bundled':
        lock=json.loads((ROOT/'sources.lock.json').read_text())
        bundle=ROOT/lock['bundled_phone_driver']['path']
        phone_meta=driver_bundle(bundle,root,allow_experimental=True);has_phone=True
    if has_phone:
        if phone_meta.get('profile')=='public-qsdk-experimental':
            fit,_=parse(kernel.read_bytes())
            images=[n for n in fit.get('/images').children if n.props.get('type')==b'kernel\0']
            if len(images)!=1 or images[0].props.get('compression')!=b'gzip\0':
                raise ValueError('内置实验驱动要求原 gzip Linux Image')
            if hashlib.sha256(gzip.decompress(images[0].props['data'])).hexdigest()!=KERNEL_IMAGE_SHA256:
                raise ValueError('内置实验驱动与当前原 Linux Image 不匹配')
        kernel.write_bytes(prepare_phone_fit(kernel.read_bytes()))
        p=root/'etc/config/fxs';p.write_text(p.read_text().replace("option enabled '0'","option enabled '1'"))
    kernel.write_bytes(prepare_led_fit(kernel.read_bytes()))
    led_meta=led_hardware.install(root)
    experimental=bool(phone_meta and phone_meta.get('profile')=='public-qsdk-experimental')
    message=('已集成内置实验驱动并启用电话；ABI、加载及通话待实机验证' if experimental
             else '已集成严格身份校验驱动，待实机验收' if has_phone
             else '已选择关闭电话，应用和补丁保留')
    state={'version':VERSION,'phone_driver_integrated':has_phone,'phone_runtime_verified':False,
           'phone_mode':settings['phone_mode'],'phone_driver_experimental':experimental,
           'phone_abi_verified':False,'phone_driver_build_version':phone_meta.get('version') if phone_meta else None,
           'phone_config_difference_count':len(phone_meta.get('config_changes',{})) if phone_meta else None,
           'phone_commit':phone.COMMIT,'kernel_package':KERNEL_ABI,
           'phone_message':message,'led_hardware':led_meta}
    (root/'etc/aw1000-feature-status.json').write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')
    phone.hide_build_status(root)
    pseudo=work/'devices.pseudo';pseudo.write_text('\n'.join(special)+'\n')
    squash=work/'rootfs.squashfs'
    # Final ownership-based pass after all overlay/package assembly steps.
    purge_paths(root,removed_paths)
    run(['mksquashfs',root,squash,'-noappend','-comp','xz','-b','262144','-all-root','-no-xattrs',
         '-pf',pseudo,'-processors',str(min(os.cpu_count() or 2,4)),'-no-progress','-wildcards','-e',
         'usr/bin/.hysteria.*','usr/bin/.mosdns.*','usr/bin/.mihomo.*','usr/bin/.clash.*'])
    kernel_pebs=math.ceil(kernel.stat().st_size/LEB);root_pebs=math.ceil(squash.stat().st_size/LEB)
    cfg=work/'ubinize.cfg'
    cfg.write_text(f'''[kernel]
mode=ubi
image={kernel.resolve()}
vol_id=0
vol_type=dynamic
vol_name=kernel
vol_size={kernel_pebs*LEB}

[rootfs]
mode=ubi
image={squash.resolve()}
vol_id=1
vol_type=dynamic
vol_name=rootfs
vol_size={root_pebs*LEB}

[rootfs_data]
mode=ubi
vol_id=2
vol_type=dynamic
vol_name=rootfs_data
vol_size={5*LEB}
vol_flags=autoresize
''')
    output.mkdir(parents=True,exist_ok=True)
    name='QWRT-arcadyan-aw1000-'+VERSION+'-factory.ubi';result=output/name
    run(['ubinize','-o',result,'-p',str(PEB),'-m',str(MIN_IO),'-s',str(MIN_IO),cfg])
    verify=work/'verify';run(['ubireader_extract_images','-o',verify,result])
    vk=next(verify.rglob('*vol-kernel.ubifs'));vr=next(verify.rglob('*vol-rootfs.ubifs'))
    if vk.read_bytes()[:kernel.stat().st_size]!=kernel.read_bytes():raise ValueError('重打包 kernel 校验失败')
    if vr.read_bytes()[:squash.stat().st_size]!=squash.read_bytes():raise ValueError('重打包 rootfs 校验失败')
    with open(vr,'rb') as f:
        sq=SquashFS(f)
        for path in removed_paths:
            try:sq.get(path)
            except FileNotFoundError:pass
            else:raise ValueError('成品仍含被删除软件文件：'+path)
        for node in sq.get('/usr/bin').iterdir():
            if re.fullmatch(r'\.(?:hysteria|mosdns|mihomo|clash)\..+',node.name):
                raise ValueError('成品仍含异常隐藏可执行文件：/usr/bin/'+node.name)
        for p in ('/etc/aw1000-mod-version','/etc/init.d/aw1000-leds','/etc/rc.button/wlan','/etc/rc.button/wps',
                  '/usr/bin/md_serial_ecm','/usr/libexec/aw1000-modemdata-at',
                  '/www/luci-static/resources/view/modem/modempreview.js',
                  '/lib/upgrade/zz-aw1000.sh',
                  '/www/luci-static/argon/css/cascade.css','/usr/sbin/fxsd','/etc/fxs/si3226x_patch.bin'):
            if not sq.get(p).is_file():raise ValueError('成品缺少文件：'+p)
        if sq.get('/etc/aw1000-mod-version').open().read().decode().strip()!=VERSION:raise ValueError('成品版本错误')
    upgrade=create_sysupgrade(root,kernel,squash,output,work,VERSION)
    checksums='QWRT-arcadyan-aw1000-'+VERSION+'.sha256'
    (output/checksums).write_text(sha(result)+'  '+name+'\n'+sha(upgrade)+'  '+upgrade.name+'\n')
    expected_assets={name,upgrade.name,checksums}
    if {p.name for p in output.iterdir()} != expected_assets:
        raise ValueError('发布目录不是三个已校验资产；请检查额外文件或残留临时文件')
    summary={'version':VERSION,'release':release_meta,'source_sha256':digest,'factory':name,'factory_sha256':sha(result),
             'password_hash_format':'md5-crypt','original_password_runtime_verified':True,
             'sysupgrade':upgrade.name,'sysupgrade_sha256':sha(upgrade),'checksums':checksums,'sysupgrade_format':'NAND TAR + fwtool metadata',
             'sysupgrade_original_fwtool_and_tar_verified':True,
             'sysupgrade_payload_hardware_verified':False,
             'sysupgrade_manual_power_cycle_boot_verified':False,
             'historical_sysupgrade_validation':{'version':'1.0.17','payload_boot_after_manual_power_cycle':True},
             'sysupgrade_auto_reset_fix_hardware_verified':False,
             'removed_packages':removed,
             'sysupgrade_status':'current-payload-and-auto-reset-awaiting-hardware-validation',
             'led_hardware':led_meta,
             'wifi_scan':scan_meta,
             'internet':network_meta,
             'arpbind':arp_meta,
             'modemdata':modemdata_meta,
             'librespeed':librespeed_meta,
             'original_kernel_sha256':original_kernel_sha,'output_kernel_sha256':sha(kernel),
             'phone':state,'phone_driver_metadata':phone_meta,
             'ubi':{'peb_size':PEB,'min_io':MIN_IO,'kernel_pebs':kernel_pebs,'rootfs_pebs':root_pebs,
                    'minimum_partition_bytes_before_bad_block_reserve':(kernel_pebs+root_pebs+7)*PEB}}
    # Build reports go to the run summary, not Release assets or Artifacts. No secrets/SSID/URL are stored.
    (work/'result.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    notes=f'''QWRT-arcadyan-aw1000-{VERSION}

基础系统：QWRT 25.12.2 r47986-d312c785fd，Linux 5.4.213。
LuCI 固件版本：{release_meta['firmware_display']}（编译日期，{release_meta['build_timezone']}）。
br-lan 绑定 lan1–lan4（原 eth0–eth3）；有线物理 wan（原 eth4）；wwan/wwan_v6 由 QModem 管理。
默认 Argon；独占式原厂语义 LED 服务；Wi-Fi/WPS 按钮脚本修复；SN-v3 管理密码策略。
网络 → IP/MAC 绑定提供中文静态 ARP 管理，默认 br-lan；电话页面已移除改造状态入口。
完整集成 4IceG luci-app-modemdata 五个页面与后端，提供简体中文、默认自动 AT 端口和 wwan 信息。
串口读取统一使用 at-daemon；动态信息至少10秒共享缓存、身份信息300秒缓存；无新增后台串口服务，隐藏页面暂停查询。诊断、模块定义、配置及更新/支持入口均保留；QModem继续管理拨号。
集成用户提供的 LibreSpeed Go 1.1.5-r5，默认开机启动、HTTP 8989、database_type=none；浏览器访问 http://路由器LAN地址:8989/ 测试客户端与路由器之间的速度。

RJ11：{state['phone_message']}。未经过本项目实机通话验收。
电话更新：fxsd/LuCI 0.7.0，上游固定 f26f9f5，新增免打扰、允许号码、重复来电响铃、话机连接检测、本地号码格式和 FSK/RP-AS 来电显示修复。9 个内核驱动源文件与 1.2.5 构建基线逐字节一致，继续集成原 Linux 5.4.213 驱动 0.3.0，并核验源码身份。新增设置和状态全部汉化。
SIP桥接默认关闭；当前QModem/at-daemon 2.8.10不满足完整SIP条件，仍需另行匹配QModem voice/qmodem-sipd及at-daemon>=3.4.0。电话灯继续独占常亮，共用AT队列；未将本次更新视为杂音已修复。
提供 factory.ubi 和 sysupgrade.bin（kernel/root TAR + 机型元数据），使用相同的 kernel/rootfs。
升级收尾沿用 1.2.6 已通过真机验证的 reboot -f 优先顺序。命令若返回，等待 5 秒后尝试 SysRq，最后交给 upgraded。U-Boot 工厂刷写使用 factory.ubi。
已移除 3proxy（含 /usr/bin/3proxy、启动脚本、默认配置和 opkg 登记）、旧 modem-data、SSR Plus、OpenClash/Mihomo、Design、CoreMark、IPTV/xupnpd、动态 DNS、FTP 服务、ZeroTier 及其独占依赖。电话页面外部项目链接已移除。
已删除 /etc/banner；电源灯由面板服务常亮，电话灯由 RJ11 服务正常运行时常亮，其余灯按原厂图示语义控制。
网口在 netifd 启动前改名，冲突/失败回退旧名；按钮只由 /etc/rc.button 分发，避免 rc.button 与 hotplug.d 串行重复执行。Wi-Fi 关闭/开启及双频 WPS PBC 已实机确认，WPS 客户端完成配对仍待验收。
上网设置精简为四种线路模式、状态和120秒确认回退；WAN协议、账号、VLAN/MTU继续在网络→接口→wan配置，切换模式保留这些字段。
本版QCA扫描仅手动读取缓存，不创建临时STA或执行强制主动扫描，缓存可能为空或过期；新发生的驱动确认AP掉线可按条件单频恢复一次。\nDNS先于独立IPv6工作进程协调，失败限时退避；IPv6规则/路由/过滤器完整性自愈，备用地址变化不重置当前RA/NAT，仅清理旧LAN前缀的连接。\nLAN 默认使用路由器 DNS；自动上游跟随已发布的当前出口 IPv4，追加两组公共容错，IPv6 报错不再阻断 DNS 更新；临时 DNS 配置缺失时自动重建。
修正本机 quectel-CM 不支持的 -D 选项和 QModem 嵌套服务事务；保留原 APN/PDP/锁频配置逻辑。
QConnectManager 辅助 udhcpc 不再添加无 metric 的移动默认路由或删除有线默认路由；默认路由统一由 netifd/mwan3 管理，并在 WAN 已就绪但路由丢失时限频自愈。
IPv6 例外前缀拆成单条规则，兼容目标内核未启用 NF_TABLES_SET；无线状态兼容 iwinfo，QCA LED 输出 helper 定向适配，统计功能保留。
采用 fw3 兼容的 mwan3 2.11.7；保留现有 QModem 拨号。管理期间暂停 ECM/流量卸载以保证策略经过内核选路，吞吐量可能降低。
双线切换、IPv6/NAT66、NSS 控制仍需目标设备验收；切换公网地址时已有连接可能需要重建。
保留配置升级会保留旧网络/密码，首次应用全新布局应不保留配置。

原输入 SHA256：{digest}
工厂镜像 SHA256：{sha(result)}
升级镜像 SHA256：{sha(upgrade)}
镜像最低分区占用（未计坏块/额外 UBI 保留）：{summary['ubi']['minimum_partition_bytes_before_bad_block_reserve']} 字节。
'''
    (work/'release-notes.md').write_text(notes)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write(notes+'\n')
    print('完成 '+name+' 和 '+upgrade.name+'；RJ11：'+state['phone_message'])
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',type=Path)
    parser.add_argument('--settings',type=Path,default=ROOT/'.private/settings.json')
    parser.add_argument('--work',type=Path,default=ROOT/'work/build')
    parser.add_argument('--output',type=Path,default=ROOT/'out')
    parser.add_argument('--cc',default=os.getenv('CC_AARCH64','aarch64-linux-gnu-gcc'))
    args=parser.parse_args()
    try:build(args.input,json.loads(args.settings.read_text()),args.work.resolve(),args.output.resolve(),args.cc)
    except (ValueError,KeyError,subprocess.CalledProcessError) as exc:raise SystemExit(str(exc)) from None

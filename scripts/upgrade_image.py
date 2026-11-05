"""NAND sysupgrade TAR, with metadata appended/read by the original fwtool."""
import hashlib
import io
import json
import os
import subprocess
import tarfile
import time

BOARD='arcadyan_aw1000'
DEVICE='arcadyan,aw1000'


def target_command(root,executable):
    return ['qemu-aarch64-static','-L',str(root),str(root/'lib/libc.so'),'--library-path',str(root/'lib')+':'+str(root/'usr/lib'),str(root/executable)]


def write_tar(path,kernel,squash,epoch=None):
    epoch=int(time.time()) if epoch is None else epoch
    directory='sysupgrade-'+BOARD
    with tarfile.open(path,'w',format=tarfile.USTAR_FORMAT) as archive:
        entry=tarfile.TarInfo(directory+'/');entry.type=tarfile.DIRTYPE;entry.mode=0o755;entry.mtime=epoch
        archive.addfile(entry)
        for name,source in [('CONTROL',None),('kernel',kernel),('root',squash)]:
            data=('BOARD='+BOARD+'\n').encode() if source is None else source.read_bytes()
            entry=tarfile.TarInfo(directory+'/'+name);entry.mode=0o644;entry.mtime=epoch;entry.size=len(data)
            archive.addfile(entry,io.BytesIO(data))


def verify_tar(path,kernel,squash):
    directory='sysupgrade-'+BOARD
    with tarfile.open(path,'r:') as archive:
        entries=archive.getmembers()
        if [e.name.rstrip('/') for e in entries]!=[directory,directory+'/CONTROL',directory+'/kernel',directory+'/root']:
            raise ValueError('sysupgrade TAR 结构不符')
        if not entries[0].isdir() or any(not e.isfile() for e in entries[1:]):raise ValueError('sysupgrade 成员类型不符')
        if archive.extractfile(directory+'/CONTROL').read()!=('BOARD='+BOARD+'\n').encode():raise ValueError('sysupgrade CONTROL 不符')
        for name,source in [('kernel',kernel),('root',squash)]:
            if hashlib.sha256(archive.extractfile(directory+'/'+name).read()).digest()!=hashlib.sha256(source.read_bytes()).digest():
                raise ValueError('sysupgrade '+name+' 回读失败')


def create_sysupgrade(root,kernel,squash,output,work,version):
    filename='QWRT-arcadyan-aw1000-'+version+'-sysupgrade.bin'
    path=work/filename;write_tar(path,kernel,squash)
    metadata={'metadata_version':'1.1','compat_version':'1.0','supported_devices':[DEVICE],
              'version':{'dist':'QWRT','version':'25.12.2','revision':'r47986-d312c785fd','target':'ipq807x/generic','board':BOARD},
              'aw1000_mod_version':version}
    meta=work/'sysupgrade-metadata.json';meta.write_text(json.dumps(metadata,separators=(',',':'))+'\n')
    tool=target_command(root,'usr/bin/fwtool');subprocess.run(tool+['-I',str(meta),str(path)],check=True)
    extracted=work/'sysupgrade-metadata-readback.json'
    subprocess.run(tool+['-i',str(extracted),str(path)],check=True)
    if json.loads(extracted.read_text())!=metadata:raise ValueError('sysupgrade 元数据回读失败')
    verify_tar(path,kernel,squash)
    directory='sysupgrade-'+BOARD;bb=target_command(root,'bin/busybox')
    listing=subprocess.check_output(bb+['tar','tf',str(path)],text=True).splitlines()
    if listing!=[directory+'/',directory+'/CONTROL',directory+'/kernel',directory+'/root']:raise ValueError('原 QWRT tar 无法识别 sysupgrade 结构')
    for name,source in [('kernel',kernel),('root',squash)]:
        readback=work/('sysupgrade-readback-'+name)
        with readback.open('wb') as f:subprocess.run(bb+['tar','xf',str(path),directory+'/'+name,'-O'],stdout=f,check=True)
        if hashlib.sha256(readback.read_bytes()).digest()!=hashlib.sha256(source.read_bytes()).digest():raise ValueError('原 QWRT tar 回读不符：'+name)
        readback.unlink()
    destination=output/filename
    os.replace(path,destination)
    return destination

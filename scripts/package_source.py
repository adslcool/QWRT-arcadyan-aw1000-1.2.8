#!/usr/bin/env python3
"""Deliver a source ZIP with no more than 100 uploadable files."""
import argparse
import hashlib
import re
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
EXCLUDED={'.private','work','out','.venv','.git','__pycache__'}


def source_name():
    return 'QWRT-arcadyan-aw1000-'+(ROOT/'VERSION').read_text().strip()


def source_files(root=ROOT):
    return sorted(p for p in root.rglob('*') if p.is_file()
                  and not any(part in EXCLUDED for part in p.relative_to(root).parts)
                  and p.relative_to(root).parts[:2] != ('phone','upstream')
                  and p.suffix not in ('.pyc','.pyo'))


def package(output):
    files=[p for p in source_files() if p.name!='SOURCE_SHA256SUMS']
    if output.is_relative_to(ROOT):
        raise ValueError('源码 ZIP 请写到工程目录外，以免把旧交付包再次打包')
    if len(files)+1>100:raise ValueError('上传文件数超过 100：'+str(len(files)+1))
    checksums=ROOT/'SOURCE_SHA256SUMS'
    checksums.write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.relative_to(ROOT).as_posix()+'\n'
                                 for p in files))
    files.append(checksums)
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        prefix=source_name()+'/'
        for p in sorted(files):z.write(p,prefix+p.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(output) as z:
        if z.testzip() is not None:raise ValueError('交付 ZIP 校验失败')
        if len(z.namelist())>100:raise ValueError('交付 ZIP 成员超过 100')
    print(str(output)+'；上传文件 '+str(len(files))+' / 100')
    print('SHA256 '+hashlib.sha256(output.read_bytes()).hexdigest())


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version',help='打包前递增工程版本号；请同步更新 CHANGELOG')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.version:
        old=(ROOT/'VERSION').read_text().strip()
        if not re.fullmatch(r'\d+\.\d+\.\d+',args.version) or tuple(map(int,args.version.split('.')))<=tuple(map(int,old.split('.'))):
            raise SystemExit('新版本必须为递增的 x.y.z')
        (ROOT/'VERSION').write_text(args.version+'\n')
    if args.output is None:args.output=ROOT.parent/(source_name()+'.zip')
    try:package(args.output.resolve())
    except ValueError as e:raise SystemExit(str(e)) from None

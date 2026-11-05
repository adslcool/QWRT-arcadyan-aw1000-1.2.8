"""Read PREL32 export records from the inspected ARM64 QWRT Image.

These are symbol-name availability checks, not a proof of C type/layout ABI.
No CRCs can be recovered or invented when CONFIG_MODVERSIONS is disabled.
"""
import re
import struct

NAME=re.compile(rb'[A-Za-z_][A-Za-z0-9_]*')


def _string(data,offset):
    if not 0<=offset<len(data):return None
    end=data.find(b'\0',offset,offset+160)
    if end<0 or not NAME.fullmatch(data[offset:end]):return None
    return data[offset:end].decode('ascii')


def prel32_exports(data,minimum_run=8):
    """Find contiguous 12-byte kernel_symbol records with valid relative pointers."""
    groups=[]
    for offset in range(0,len(data)-11,4):
        value_rel,name_rel,namespace_rel=struct.unpack_from('<iii',data,offset)
        value=offset+value_rel;name_offset=offset+4+name_rel
        if not (0<=value<len(data)+32*1024*1024 and offset+12<=name_offset<len(data)
                and name_rel<2*1024*1024):continue
        name=_string(data,name_offset)
        if name is None:continue
        namespace=_string(data,offset+8+namespace_rel) if namespace_rel else ''
        if namespace is None:continue
        record={'name':name,'namespace':namespace,'entry_offset':offset,
                'value_offset':value,'name_offset':name_offset}
        if groups and offset==groups[-1][-1]['entry_offset']+12:groups[-1].append(record)
        else:groups.append([record])
    records=[record for group in groups if len(group)>=minimum_run for record in group]
    if len({r['name'] for r in records})!=len(records):
        raise ValueError('原内核导出表存在重复名称，拒绝不确定的解析结果')
    return {r['name']:r for r in records}


def write_symvers(exports,path):
    # All symbols are conservatively GPL-only for this GPL module's modpost.
    # This synthetic index is not the original Module.symvers or a CRC table.
    path.write_text(''.join('0x00000000\t'+name+'\tvmlinux\tEXPORT_SYMBOL_GPL\t'+exports[name]['namespace']+'\n'
                            for name in sorted(exports)))


def original_kallsyms(data):
    """Read this exact baseline Image's uncompressed, base-relative name table.

    Offset was independently checked with vmlinux-to-elf at commit
    29eac31a38db48b51c0dc6aae3be0162237601eb. Call only after Image hash validation.
    """
    names_offset=0x936188
    count=struct.unpack_from('<Q',data,names_offset-8)[0]
    base=struct.unpack_from('<Q',data,names_offset-16)[0]
    if count!=32701 or base!=0xffffffc010080000:
        raise ValueError('原内核 kallsyms 布局不同')
    start=names_offset-16-((count*4+7)&~7)
    offsets=struct.unpack_from('<'+str(count)+'I',data,start)
    result={};position=names_offset
    for value in offsets:
        length=data[position]
        if length<2 or position+1+length>len(data):raise ValueError('kallsyms 名称表损坏')
        name=data[position+2:position+1+length].decode('ascii')
        result[name]=base+value
        position+=length+1
    return base,result


def elf_section_size(data,wanted):
    if data[:6]!=b'\x7fELF\x02\x01' or struct.unpack_from('<H',data,18)[0]!=183:
        raise ValueError('需要 AArch64 ELF64 little-endian 模块')
    offset=struct.unpack_from('<Q',data,40)[0]
    size,count,names_index=struct.unpack_from('<HHH',data,58)
    if size!=64 or names_index>=count or offset+size*count>len(data):
        raise ValueError('ELF section table 不完整')
    headers=[struct.unpack_from('<IIQQQQIIQQ',data,offset+i*size) for i in range(count)]
    table=headers[names_index]
    names=data[table[4]:table[4]+table[5]]
    for h in headers:
        end=names.find(b'\0',h[0])
        if end>=0 and names[h[0]:end].decode()==wanted:return h[5]
    raise ValueError('ELF 缺少 section：'+wanted)

"""Isolated UMS fixtures; original maps and bot binaries are never edited.

MPQ layout follows StormLib's documented v1 format; CHK records follow the
OpenBW loader and Chkdraft mapping_core/chk.h. Used only by validation scripts.
"""
import struct
from pathlib import Path
MASK=0xffffffff
CRYPT=[0]*1280
seed=0x100001
for i in range(256):
    for j in range(5):
        seed=(seed*125+3)%0x2aaaab;a=(seed&0xffff)<<16
        seed=(seed*125+3)%0x2aaaab;CRYPT[j*256+i]=a|(seed&0xffff)
def hash_name(name,kind):
    a=0x7fed7fed;b=0xeeeeeeee
    for c in name.replace('/','\\').upper().encode('ascii'):
        a=CRYPT[kind*256+c]^((a+b)&MASK);b=(c+a+b+(b<<5)+3)&MASK
    return a
def crypt(data,key,decrypt=False):
    b=0xeeeeeeee;out=[]
    for (word,) in struct.iter_unpack('<I',data):
        b=(b+CRYPT[1024+(key&255)])&MASK
        converted=word^((key+b)&MASK);out.append(converted)
        plain=converted if decrypt else word
        key=(((~key<<21)+0x11111111)|(key>>11))&MASK
        b=(plain+b+(b<<5)+3)&MASK
    return struct.pack('<'+'I'*len(out),*out)
def archive_entry(path,name):
    data=Path(path).read_bytes()
    magic,header,total,ver,shift,hashpos,blockpos,nh,nb=struct.unpack_from('<4sIIHHIIII',data)
    assert magic==b'MPQ\x1a' and ver==0
    table=crypt(data[hashpos:hashpos+nh*16],hash_name('(hash table)',3),True)
    entries=list(struct.iter_unpack('<IIHHI',table))
    found=[e for e in entries if e[0]==hash_name(name,1) and e[1]==hash_name(name,2)]
    if not found:raise ValueError(('missing MPQ entry',name))
    blocks=crypt(data[blockpos:blockpos+nb*16],hash_name('(block table)',3),True)
    return struct.unpack_from('<IIII',blocks,found[0][-1]*16),data,512<<shift
def write_archive(path,chk):
    name='staredit\\scenario.chk';nh=8
    hashes=[(MASK,MASK,0xffff,0xffff,MASK)]*nh
    hashes[hash_name(name,0)&(nh-1)]=(hash_name(name,1),hash_name(name,2),0,0,0)
    h=crypt(b''.join(struct.pack('<IIHHI',*e) for e in hashes),hash_name('(hash table)',3))
    block=crypt(struct.pack('<IIII',32,len(chk),len(chk),0x80000000),hash_name('(block table)',3))
    hashpos=32+len(chk);blockpos=hashpos+len(h)
    header=struct.pack('<4sIIHHIIII',b'MPQ\x1a',32,blockpos+16,0,3,hashpos,blockpos,nh,1)
    Path(path).write_bytes(header+chk+h+block)
def sections(data):
    result=[];offset=0
    while offset+8<=len(data):
        tag,size=struct.unpack_from('<4sI',data,offset);offset+=8
        assert offset+size<=len(data)
        result.append((tag,data[offset:offset+size]));offset+=size
    return result
def unit(kind,owner,x,y,resources=0,invincible=False):
    unit.serial+=1
    return struct.pack('<I6H4BI2H2I',unit.serial,x,y,kind,0,24,
                       2 if resources else 0,owner,100,100,100,resources,0,16 if invincible else 0,0,0)
unit.serial=0x30000000
def action(kind,group=0,number=0,unit_type=0,secondary=0,location=0):
    return struct.pack('<6IH4BH',location,0,0,0,group,number,unit_type,kind,secondary,16,0,0)
def trigger(actions,seconds=0,owner=0):
    condition=struct.pack('<IIIH4BH',0,0,seconds,0,0,12 if seconds else 22,0,0,0)
    return condition+bytes(20*15)+b''.join(actions)+bytes(32*(64-len(actions)))+struct.pack('<I',0)+bytes([int(i==owner) for i in range(28)])
def location(x,y,r=64):return struct.pack('<4I2H',x-r,y-r,x+r,y+r,0,0)
def make_fixture(path,kind):
    base=dict(sections((Path(__file__).parent/'destination-base.chk').read_bytes()))
    records=[base[b'UNIT'][i:i+36] for i in range(0,len(base[b'UNIT']),36)]
    # Retain terrain/resources/critters and both canonical start locations.
    units=[]
    for r in records:
        v=struct.unpack('<I6H4BI2H2I',r)
        units.append(r)
    units.append(unit(154,0,1056,272))
    for i in range(0 if kind in ('worker_wait','worker_defend','worker_rally') else 10):units.append(unit(64,0,960+(i%3)*24,208+(i//3)*32))
    if kind=='worker_defend':units.append(unit(154,0,2080,656))
    army=0 if kind in ('mining','probe') else 24
    for i in range(army):units.append(unit(65 if i<8 else 66,0,1280+(i%4)*32,272+(i//4)*32))
    units.append(unit(154,1,2112,3824))
    units.append(unit(156,1,2304,3680))
    units.append(unit(154,1,992,3472))
    # Loc1 home; loc2 mine enemy; loc3 proxy; loc4 bait destination; loc64 anywhere.
    locs=[location(1056,272,192),location(800,272,48),location(1488,448,128),location(2208,1024,96),location(1328,352,64),location(2080,656,96),location(974,206,64)]
    locs+= [bytes(20)]*(255-len(locs));locs[63]=struct.pack('<4I2H',0,0,3072,4096,0,0)
    triggers=[trigger([action(26,group=0,number=2500,unit_type=0,secondary=7)])]
    if kind in ('mining','probe'):
        units.append(unit(64,1,1000,400))
        triggers.append(trigger([action(46,group=1,number=1,unit_type=64,secondary=2,location=64)]))
        triggers.append(trigger([action(46,group=1,number=4,unit_type=64,secondary=0,location=64)],seconds=15))
    if kind in ('worker_wait','worker_mixed','raid_workers'):
        units.append(unit(64,1,1408,352))
        triggers.append(trigger([action(46,group=1,number=5,unit_type=64,secondary=2,location=64)],seconds=5 if kind=='worker_wait' else 0))
        triggers.append(trigger([action(46,group=1,number=4,unit_type=64,secondary=0,location=64)],seconds=20))
    if kind=='worker_defend':
        units.append(unit(64,1,2224,656))
        triggers.append(trigger([action(46,group=1,number=6,unit_type=64,secondary=2,location=64)]))
        triggers.append(trigger([action(46,group=1,number=4,unit_type=64,secondary=0,location=64)],seconds=35))
    if kind=='worker_rally':
        units.append(unit(64,1,974,330,invincible=True))
        triggers.append(trigger([action(46,group=1,number=7,unit_type=64,secondary=2,location=64)],seconds=5))
        triggers.append(trigger([action(46,group=1,number=4,unit_type=64,secondary=0,location=64)],seconds=20))
    if kind in ('near','clear','rally','worker_mixed','raid_workers'):
        units.append(unit(156,1,1440,448))
        units.append(unit(162,1,1504,448))
        if kind in ('clear','rally','worker_mixed'):
            for x,y in [(1408,336),(1440,368)]:units.append(unit(39,1,x,y))
            triggers.append(trigger([action(46,group=1,number=4,unit_type=39,secondary=0,location=64)],seconds=1))
    if kind in ('outer','main'):
        units.append(unit(101,0,992,3472));units.append(unit(101,0,2112,3824))
    base[b'OWNR']=bytes([6,5]+[0]*10);base[b'SIDE']=bytes([2,2]+[7]*9+[4])
    base[b'UNIT']=b''.join(units);base[b'MRGN']=b''.join(locs);base[b'TRIG']=b''.join(triggers);base[b'MBRF']=b''
    base[b'FORC']=bytes([0,1]+[1]*6)+base[b'FORC'][8:16]+bytes(4)
    # Preserve every original string index (unit names, WAV and scenario fields).
    payload=b''.join(tag+struct.pack('<I',len(data))+data for tag,data in base.items())
    write_archive(path,payload)
    return {'kind':kind,'army':army,'home':[1056,272],'main':[2112,3824],'outer':[992,3472]}
if __name__=='__main__':
    print(archive_entry('E:/StarCraftAI/benchmark/maps/(2)Destination.scx','staredit\\scenario.chk')[0])

"""Original-engine UMS scenarios; telemetry only, no capture or recordings."""
import hashlib,json,struct,sys,threading,time
from pathlib import Path
BASE=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(BASE/'app'),str(BASE/'validation')]
import matches
import v11_fixture_map as f

scene=sys.argv[1]; key='5' if scene=='route5' else '6' if scene=='route6' else '9' if scene=='defend' else '8' if scene=='rally' else '7'
limit=5000 if scene=='route6' else 2400
run=matches.new_run(matches.VALIDATION)
fixture=BASE/'validation'/('combat-v12-'+scene+'.scx')
kind='worker_mixed' if scene=='route5' else 'main' if scene=='route6' else 'worker_defend' if scene=='defend' else 'worker_rally' if scene=='rally' else 'worker_wait'
f.make_fixture(fixture,kind)
entry,data,_=f.archive_entry(fixture,'staredit/scenario.chk')
parts=dict(f.sections(data[entry[0]:entry[0]+entry[1]]))
if scene=='route6':
    parts[b'UNIT']+=b''.join(f.unit(t,1,x,y) for t,x,y in [(39,1408,336),(39,1440,368),(64,1408,352),(156,1440,448),(162,1504,448)])
    parts[b'TRIG']+=f.trigger([f.action(44,group=0,unit_type=66,secondary=2,location=1)],seconds=12)
if scene=='late':
    # Two real waves after a long empty guard interval. 44=Create Units;
    # action secondary is the amount byte, group is the owning player.
    parts[b'TRIG']+=f.trigger([f.action(44,group=1,unit_type=66,secondary=2,location=3)],seconds=12)
    parts[b'TRIG']+=f.trigger([f.action(44,group=1,unit_type=65,secondary=2,location=3)],seconds=35)
if scene=='fog':
    records=[parts[b'UNIT'][i:i+36] for i in range(0,len(parts[b'UNIT']),36)]
    kept=[];melee=0
    for r in records:
        v=struct.unpack('<I6H4BI2H2I',r)
        if v[7]==0 and v[3] in (65,66):
            if v[3]!=65 or melee>=2:continue
            melee+=1
        kept.append(r)
    parts[b'UNIT']=b''.join(kept)+f.unit(66,1,1504,448)
    locs=bytearray(parts[b'MRGN']);locs[3*20:4*20]=f.location(2080,656,96);parts[b'MRGN']=bytes(locs)
    # Move Units teleports a real, attackable enemy out of vision and back;
    # no invincibility (BWAPI correctly refuses attacks on invincible units).
    parts[b'TRIG']+=f.trigger([f.action(39,group=1,number=4,unit_type=66,secondary=1,location=64)],seconds=1)
    parts[b'TRIG']+=f.trigger([f.action(39,group=1,number=3,unit_type=66,secondary=1,location=64)],seconds=30)
f.write_archive(fixture,b''.join(tag+struct.pack('<I',len(p))+p for tag,p in parts.items()))
commands=run.parent/('combat-v12-'+scene+'-commands.txt');commands.write_text('1 '+key)
original=matches.prepare
def prepare(*args,**kwargs):
    env=original(*args,**kwargs)
    (run/'maps/(2)Destination.scx').write_bytes(fixture.read_bytes())
    ini=run/'bwapi-data/bwapi.ini';ini.write_text(ini.read_text().replace('game_type=MELEE','game_type=USE_MAP_SETTINGS'))
    env['COMMANDER_TEST_COMMANDS']=str(commands);env['COMMANDER_SMOKE_FRAMES']=str(limit)
    return env
matches.prepare=prepare
done=threading.Event()
def feed():
    # A second identical command must retain task and combat state.
    pending=[(24 if scene=="route5" else 240,key)];seq=1
    while pending and not done.wait(.01):
        p=run/'bwapi-data/write/commander-layer.jsonl'
        try:rows=[json.loads(x) for x in p.read_text(encoding='utf8').splitlines() if x.endswith('}')]
        except (OSError,ValueError):continue
        if rows and max(x['frame'] for x in rows)>=pending[0][0]:
            _,k=pending.pop(0);seq+=1;commands.write_text(str(seq)+' '+k)
thread=threading.Thread(target=feed);thread.start()
print('RUN '+str(run),flush=True)
try: result=matches.playing(run,'Destination',matches.BUILTIN,speed=0,fullscreen=False,keep_results=False)
finally:done.set();thread.join()
meta=json.loads((run/'match.json').read_text(encoding='utf-8-sig'))
meta.update(validation_fixture='combat-v12-'+scene,canonical_map_sha256=meta.get('map_sha256'),map_sha256=hashlib.sha256(fixture.read_bytes()).hexdigest())
(run/'match.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf8')
rows=[json.loads(x) for x in (run/'bwapi-data/write/commander-layer.jsonl').read_text(encoding='utf8').splitlines()]
ev=lambda e:[r for r in rows if r['event']==e]
attacks=[(r['frame'],r['selected'],next((c['type'] for c in r['candidates'] if c['id']==r['selected']),-1)) for r in ev('target_selection') if r['selected']>=0]
report={'run':str(run),'scene':scene,'commands':ev('command'),'complete':ev('building_complete'),
        'phases':sorted({r['phase'] for r in ev('battle_state')}),'attacks':attacks,'result':result,
        'bad_orders':len([r for r in ev('battle_order') if not r['accepted'] or not r['verified']])}
(BASE/'validation'/('combat-v12-'+scene+'-result.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({k:v for k,v in report.items() if k!='attacks'},ensure_ascii=False),flush=True)
print('ATTACKS '+str(attacks[:10])+' ... '+str(attacks[-6:]),flush=True)


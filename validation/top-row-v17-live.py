"""Native Windows message-hook test; checks only logs and game data."""
import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import sys
import threading

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE/'app'))
import matches

run=matches.new_run(matches.VALIDATION)
fixture=BASE/'validation/assembly-v14-keys.scx'
original=matches.prepare

def prepare(*args,**kwargs):
    env=original(*args,**kwargs)
    (run/'maps/(2)Destination.scx').write_bytes(fixture.read_bytes())
    ini=run/'bwapi-data/bwapi.ini'
    ini.write_text(ini.read_text().replace('game_type=MELEE','game_type=USE_MAP_SETTINGS'))
    env.pop('COMMANDER_TEST_COMMANDS',None)
    env.update(COMMANDER_SMOKE_FRAMES='2400',COMMANDER_DATA_ONLY='0')
    return env

matches.prepare=prepare
done=threading.Event()
posted=[]
errors=[]
timeline=[(24,'2'),(180,'8'),(440,'9'),(740,'0'),(1040,'-'),(1320,'alt8'),(1520,'numpad-minus'),(1800,'8')]

def post(key):
    from match_windows import Windows
    api=Windows()
    pid,_=api.owner(int((run/'bwapi-data/write/game-window.txt').read_text()))
    hwnd=next(w['hwnd'] for w in api.windows({pid:'self'}) if w['window_class']=='SWarClass')
    user=ctypes.WinDLL('user32',use_last_error=True)
    user.PostMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
    vk=0xBD if key=='-' else 0x6D if key=='numpad-minus' else ord('8' if key=='alt8' else key)
    alt=key=='alt8'
    down,up=(0x104,0x105) if alt else (0x100,0x101)
    flags=1|((1<<29) if alt else 0)
    for message,param in ((down,flags),(down,flags|(1<<30)),(up,flags|(1<<30)|(1<<31))):
        assert user.PostMessageW(hwnd,message,vk,param)
    posted.append(key)

def feed():
    reader=None
    pending=b''
    try:
        while timeline and not done.wait(.005):
            if reader is None:
                path=run/'bwapi-data/write/commander-layer.jsonl'
                if not path.exists():continue
                reader=path.open('rb')
            pending+=reader.read()
            lines=pending.split(b'\n')
            pending=lines.pop()
            for line in lines:
                frame=json.loads(line)['frame']
                if timeline and frame>=timeline[0][0]:
                    _,key=timeline.pop(0)
                    post(key)
    except BaseException as error:
        errors.append(repr(error))
    finally:
        if reader:reader.close()

thread=threading.Thread(target=feed)
thread.start()
print('RUN '+str(run),flush=True)
try:
    result=matches.playing(run,'Destination',matches.BUILTIN,speed=0,fullscreen=False,keep_results=False,render_profile='standard',smoke_frames=2400)
finally:
    done.set()
    thread.join()
if errors:raise RuntimeError(errors)
meta=matches.read_json(run/'match.json')
meta.update(validation_fixture='top-row-v17-keys',canonical_map_sha256=meta['map_sha256'],map_sha256=hashlib.sha256(fixture.read_bytes()).hexdigest())
(run/'match.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf8')
rows=[json.loads(line) for line in (run/'bwapi-data/write/commander-layer.jsonl').read_text(encoding='utf8').splitlines()]
commands=[r for r in rows if r['event']=='command']
hud=[r for r in rows if r['event']=='hud']
checks={
    'all_inputs_posted':not timeline,
    'physical_key_sequence':[r['key'] for r in commands]==['2','8','9','0','-','8'],
    'actions':[r['action'] for r in commands]==['mine_lock','rally','retreat','defend','restore_auto','rally'],
    'versioned_plain_keys':all(r['keymap']=='toprow_commands_v17' and r['modifier']=='none' for r in commands),
    'defence_keeps_mining_lock':any(r['key']=='0' and r['global_defence'] and r['mining_lock'] for r in commands),
    'minus_resets_everything':any(r['key']=='-' and r['status']=='auto_restored' and not r['mining_lock'] and not r['main_task'] and not r['squad_task'] and r['evade']=='idle' for r in commands),
    'hud_new_keys':bool(hud) and all('- 全局重置' in r['lines'][0] and '9 撤离集结  0 回防' in r['lines'][1] for r in hud),
    'hud_physical_minus':any(r['last_key']=='-' for r in hud),
    'no_capture':not list(run.rglob('*.mp4')) and not list(run.rglob('*.png')),
}
report={'run':str(run),'result':result,'layer_sha256':meta['layer_sha256'],'posted':posted,'checks':checks,'commands':commands,'hud_samples':len(hud),'bad_orders':sum(not r['accepted'] or not r['verified'] for r in rows if r['event']=='battle_order')}
(BASE/'validation/top-row-v17-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({k:v for k,v in report.items() if k!='commands'},ensure_ascii=True),flush=True)
assert all(checks.values()),checks

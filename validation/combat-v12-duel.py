"""Final integration duel against the unchanged Steamhammer DLL. No capture."""
import json,sys,threading,time
from pathlib import Path
BASE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(BASE/'app'))
import matches
matches.validate();run=matches.new_run(matches.VALIDATION)
commands=BASE/'validation/combat-v12-duel-commands.txt';commands.write_text('1 4')
original=matches.prepare
def prepare(*args,**kwargs):
    env=original(*args,**kwargs)
    opponent=kwargs.get('opponent') or (len(args)>5 and args[5])
    if not opponent:env.update(COMMANDER_SMOKE_FRAMES='10800',COMMANDER_TEST_COMMANDS=str(commands))
    env["COMMANDER_DATA_ONLY"]="1"
    return env
matches.prepare=prepare
done=threading.Event();errors=[];result={}
def play():
    try:result.update(matches.playing(run,'Benzene','Steamhammer',speed=1,fullscreen=False,keep_results=False,render_profile='standard'))
    except BaseException as e:errors.append(str(e))
    finally:done.set()
thread=threading.Thread(target=play);thread.start();seq=1;reader=None;pending='';last=0
timeline=[(4800,6),(6200,6),(7600,7),(9000,9)]
print('RUN '+str(run),flush=True)
while not done.wait(.015):
    p=run/'bwapi-data/write/commander-layer.jsonl'
    if reader is None:
        if not p.exists():continue
        reader=p.open(encoding='utf8')
    pending+=reader.read();lines=pending.split('\n');pending=lines.pop()
    for line in lines:
        row=json.loads(line);frame=row['frame']
        if frame>=last+2400:last=frame;print('FRAME '+str(frame),flush=True)
        if timeline and frame>=timeline[0][0]:
            _,key=timeline.pop(0);seq+=1
            tmp=commands.with_suffix('.tmp');tmp.write_text(f'{seq} {key}');tmp.replace(commands)
            print('KEY '+str(key)+' after '+str(frame),flush=True)
thread.join()
if reader:reader.close()
report={'run':str(run),'result':result,'errors':errors}
(BASE/'validation/combat-v12-duel-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps(report,ensure_ascii=False),flush=True)
if errors:raise RuntimeError(errors)

"""Build a Windows runtime release from explicitly supplied local assets.

Never copies the installed game, personal settings, matches, or replay media.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--desktop-source',type=Path,required=True)
    parser.add_argument('--lab',type=Path,required=True)
    parser.add_argument('--display',type=Path,required=True)
    parser.add_argument('--skip-freeze',action='store_true')
    parser.add_argument('--version',default='v17-20261119')
    parser.add_argument('--output-name',default='StarCraft-Commander-v17')
    args=parser.parse_args()
    freeze=ROOT/'build/frozen'
    if not args.skip_freeze:
        subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--windowed',
            '--name','星际指挥台','--distpath',str(freeze),'--workpath',str(ROOT/'build/pyinstaller'),
            '--specpath',str(ROOT/'build'),'--paths',str(ROOT/'app'),'--noupx',
            str(ROOT/'app/desktop.pyw')],check=True)
    package=ROOT/'dist'/args.output_name
    if package.parent.resolve()!=(ROOT/'dist').resolve():
        raise ValueError('Output name must be a direct child of dist')
    if package.exists():
        raise FileExistsError('Use a fresh output directory; do not merge in personal settings or match caches: '+str(package))
    shutil.copytree(freeze/'星际指挥台',package)
    for name in ['app','docs','LICENSES']:
        shutil.copytree(ROOT/name,package/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ['README.md','使用说明.md','对局复盘与按键迭代指南.md','THIRD_PARTY.md']:
        if (ROOT/name).is_file():copy(ROOT/name,package/name)
    for name in ['CommanderLayer.dll','ReplayControl.dll','WideView.dll','CommanderLocalPC.snp','ResultsWatcher.dll','LegacyStorm.dll']:
        copy(ROOT/'build'/name,package/'build'/name)
    for name in ['Bot.cpp','Baseline.dll']:
        copy(args.desktop_source/'baseline'/name,package/'baseline'/name)
    manifest=json.loads((ROOT/'baseline/manifest.json').read_text(encoding='utf8'))
    manifest.update(included=True,note='Frozen files are included unchanged in the runtime release.')
    (package/'baseline/manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
    copy(ROOT/'opponents/recorded-roster.json',package/'opponents/recorded-roster.json')
    roster=json.loads((ROOT/'opponents/recorded-roster.json').read_text(encoding='utf8'))
    for bot in roster:
        shutil.copytree(args.desktop_source/'opponents'/bot['name']/'bwapi-data',package/'opponents'/bot['name']/'bwapi-data')
    for name in ['(2)Benzene.scx','(2)Heartbreak Ridge.scx','(2)Destination.scx']:
        copy(args.lab/'benchmark/maps'/name,package/'runtime/maps'/name)
    for name in ['ddraw.dll','Shaders/xbr/xbr-lv2-noblend.glsl']:
        copy(args.display/name,package/'runtime/cnc-ddraw'/name)
    for name in ['ResolutionExpander.dll','README.md']:
        copy(args.desktop_source/'vendor/resolution-expander'/name,package/'vendor/resolution-expander'/name)
    copy(args.lab/'StarCraft1161/injectory.x86.exe',package/'runtime/engine/injectory.x86.exe')
    copy(args.lab/'StarCraft1161/bwapi-data/BWAPI.dll',package/'runtime/engine/BWAPI.dll')
    copy(args.lab/'opponents/C/Skynet/BWAPI.dll',package/'runtime/engine/BWAPI-3.7.4.dll')
    redist=sorted((args.lab/'tools/VSBuildTools/VC/Redist/MSVC').glob('*/x86/Microsoft.VC143.CRT'))[-1]
    for library in redist.glob('*.dll'):copy(library,package/'runtime/engine/crt'/library.name)
    settings=json.loads((args.lab/'local.settings.json').read_text(encoding='utf-8-sig'))
    java=(args.lab/settings['java']).resolve().parent.parent
    shutil.copytree(java,package/'runtime/jre')
    (package/'validation').mkdir()
    for name in ['compatibility.json','20261117-combat-v12-acceptance.json','top-row-v17-acceptance.json']:
        copy(ROOT/'validation'/name,package/'validation'/name)
    files=[p for p in package.rglob('*') if p.is_file()]
    forbidden=[str(p.relative_to(package)) for p in files if p.name.lower()=='starcraft.exe' or p.suffix.lower() in ('.mpq','.rep','.mp4','.webm') or p.name in ('local.settings.json','preferences.json')]
    if forbidden:raise RuntimeError('Forbidden game/media/personal files: '+str(forbidden))
    hashes={p.relative_to(package).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    (package/'PACKAGE_MANIFEST.json').write_text(json.dumps({'version':args.version,'game_included':False,
        'replay_media_included':False,'capture_enabled':False,'files':hashes},ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'package':str(package),'files':len(files),'MiB':round(sum(p.stat().st_size for p in files)/1024**2,1)}),flush=True)

if __name__=='__main__':main()

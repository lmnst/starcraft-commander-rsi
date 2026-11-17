"""Optional diagnostics used to verify the packaged application, without capture."""
import argparse
import json
from pathlib import Path
import time
import traceback

def main(desktop_type=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--smoke-replay", type=Path)
    parser.add_argument("--game-dir", type=Path)
    parser.add_argument("--view", choices=("all", "self"), default="all")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {"passed": False, "capture": False}
    try:
        import matches
        from portable_runtime import ROOT, game_error
        if args.game_dir:
            matches.GAME = args.game_dir.resolve()
        error = game_error(matches.GAME)
        if error:
            raise RuntimeError(error)
        matches.validate()
        required = ["build/CommanderLayer.dll", "build/ReplayControl.dll", "build/WideView.dll",
                    "build/CommanderLocalPC.snp", "build/ResultsWatcher.dll", "build/LegacyStorm.dll",
                    "runtime/engine/injectory.x86.exe", "runtime/engine/BWAPI.dll",
                    "runtime/engine/BWAPI-3.7.4.dll", "runtime/cnc-ddraw/ddraw.dll",
                    "vendor/resolution-expander/ResolutionExpander.dll", "runtime/jre/bin/java.exe"]
        for name in required:
            if not (ROOT / name).is_file():
                raise FileNotFoundError(name)
        report.update(root=str(ROOT), game=str(matches.GAME), native_files={name: matches.digest(ROOT/name) for name in required},
                      maps=len(matches.MAPS), opponents=len(matches.OPPONENTS))
        if args.self_check:
            import tkinter as tk
            import native_replay, replay_review, replay_perspective, replay_delete
            root = tk.Tk()
            root.withdraw()
            report['tk_version'] = root.tk.call('info', 'patchlevel')
            if desktop_type:
                desktop = desktop_type(root)
                root.update_idletasks()
                report['full_replay_button'] = desktop.replay_button.cget('text')
                report['own_replay_button'] = desktop.own_replay_button.cget('text')
                assert desktop.tree.heading('state')['text'] == '结果'
            root.destroy()
            report['replay_modules'] = ['native_replay', 'replay_review', 'replay_perspective', 'replay_layout', 'replay_delete']
        else:
            from native_replay import NativeReplay
            stages = []; started = time.monotonic(); stage = 0
            def update(state):
                nonlocal stage
                if time.monotonic()-started > 150:
                    session.close()
                    raise TimeoutError('Packaged replay verification timed out')
                if state.get('phase') != 'ready':
                    return
                frame, paused = state['frame'], state.get('paused', False)
                if stage == 0 and frame >= 24:
                    stage = 1; session.request('state', paused=True, speed=0)
                elif stage == 1 and paused:
                    stages.append({'operation':'pause','frame':frame})
                    stage = 2; session.request('seek', 600, paused=True, speed=0)
                elif stage == 2 and paused and frame == 600:
                    stages.append({'operation':'seek_forward','frame':frame})
                    stage = 3; session.request('seek', 120, paused=True, speed=0)
                elif stage == 3 and paused and frame == 120:
                    stages.append({'operation':'seek_backward_reload','frame':frame})
                    stage = 4; session.request('state', paused=False, speed=0)
                elif stage == 4 and not paused and frame > 180:
                    stages.append({'operation':'resume_at_fast_speed','frame':frame})
                    stage = 5; session.close()
            session = NativeReplay(args.smoke_replay, False, update, view_mode=args.view)
            session.run()
            assert stage == 5, stages
            report.update(view=args.view, operations=stages, playback=str(session.directory))
        report['passed'] = True
    except Exception as error:
        report.update(error=str(error), traceback=traceback.format_exc())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if report['passed'] else 1

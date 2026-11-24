import argparse
from contextlib import contextmanager, nullcontext
import ctypes
from ctypes import wintypes
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

from portable_runtime import ROOT, discover_game, settings

LAB = Path(os.environ.get("STARCRAFT_LAB", "E:/StarCraftAI"))
GAME = discover_game() or LAB / "StarCraft1161"
DISPLAY = Path(os.environ.get("STARCRAFT_DISPLAY", str(ROOT / "runtime/cnc-ddraw" if (ROOT / "runtime/cnc-ddraw").exists() else ROOT.parent / "human_commander_local/vendor/cnc-ddraw-v7.1")))
MAP_ROOT = ROOT / "runtime/maps" if (ROOT / "runtime/maps").is_dir() else LAB / "benchmark/maps"
ENGINE = ROOT / "runtime/engine"
RUNS = ROOT / "matches"
VALIDATION = ROOT / "validation"
BASELINE = "v6-fix-r1-20261110"
SOURCE_HASH = "8ef8ae16cf2f0bbc474b8b2b4cbc23ad7239a3ab1a6551c58a397e4a438fca00"
BASELINE_HASH = "4528078cda6d98d202559aea4d9fbf6e401c64c15fb2703b6a8de38f1a54e468"
VIEWPORTS = {"standard": (640, 480), "expanded": (800, 600)}
EXPANDER_HASH = "05b50dd3e0257219362a8060a51deb916659a25d316119bea20a2b040e232bd7"
# Configurable copy of the baseline for RSI (app/MacroBot.cpp, scripts/build_macrobot.ps1).
MACROBOT = ROOT / "build/MacroBot.dll"
MAPS = {
    "Benzene": ("(2)Benzene.scx", "45764b9baa72f83a5eb2eab49ddea46c6af21da75bc42e687e6dab93e4e4044e"),
    "Heartbreak Ridge": ("(2)Heartbreak Ridge.scx", "64527ae691307756dbc2700c68ab5878476eed27c3dfc9a64411955f4107f50b"),
    "Destination": ("(2)Destination.scx", "0241ae7e8b43632e3421444aa3ca3bb10d28b4796f4ccc138103c3b75e5158a5"),
}
OPPONENTS = {bot["name"]: bot for bot in json.loads(
    (ROOT / "opponents/recorded-roster.json").read_text(encoding="utf-8"))}
BUILTIN = "原版电脑（练习）"
_legacy_settings = LAB / "local.settings.json"
_legacy = json.loads(_legacy_settings.read_text(encoding="utf-8-sig")) if _legacy_settings.is_file() else {}
JAVA = os.environ.get("STARCRAFT_JAVA") or (ROOT / "runtime/jre/bin/java.exe" if (ROOT / "runtime/jre/bin/java.exe").is_file() else LAB / _legacy["java"] if "java" in _legacy else "java")
CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW

def digest(path):
    with path.open("rb") as stream:
        result = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()

def verify(path, expected):
    if digest(path) != expected.lower():
        raise RuntimeError(f"文件版本不匹配：{path.name}")

def store(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)

def newest_replay(run):
    return next((p for p in sorted((run / "maps/replays").glob("*.rep"))
                 if p.stat().st_size > 0), None)

def entry_name(bot):
    return bot["entry"].replace("\\", "/").rsplit("/", 1)[-1]

def validate():
    verify(ROOT / "baseline/Bot.cpp", SOURCE_HASH)
    verify(ROOT / "baseline/Baseline.dll", BASELINE_HASH)
    for file_name, expected in MAPS.values():
        verify(MAP_ROOT / file_name, expected)
    for name, bot in OPPONENTS.items():
        folder = ROOT / "opponents" / name / "bwapi-data/AI"
        verify(folder / entry_name(bot), bot["binary_sha256"])
        if bot["kind"] == "java":
            verify(folder / "PurpleWave.jar", bot["payload_sha256"])

def prepare(slot, map_name, room, speed, control, opponent=None, fullscreen=True, replay=None,
            opponent_hosts=False, replay_size=None, render_profile="standard", build_config=None,
            delegate_dll=None):
    """build_config: text of build_config.json. When given, the host runs MacroBot.dll (or delegate_dll)
    with it instead of the frozen Baseline.dll."""
    macro_dll = Path(delegate_dll) if delegate_dll is not None else MACROBOT
    host = opponent is None
    if render_profile not in VIEWPORTS:
        raise ValueError("未知视野设置。")
    expanded = host and render_profile == "expanded"
    creates_lobby = host != opponent_hosts
    for directory in ("bwapi-data/AI", "bwapi-data/read", "bwapi-data/write", "bwapi-data/data",
                      "bwapi-data/BWTA", "bwapi-data/BWTA2", "maps/replays", "characters", "tmp"):
        (slot / directory).mkdir(parents=True, exist_ok=True)
    for path in GAME.iterdir():
        if path.is_file() and (path.suffix.lower() in (".mpq", ".snp", ".dll") or
                               path.name in ("StarCraft.exe", "injectory.x86.exe")) and path.name.lower() != "ddraw.dll":
            target = slot / path.name
            if path.suffix.lower() == ".mpq":
                try:
                    os.link(path, target)
                except OSError:
                    shutil.copy2(path, target)
            else:
                shutil.copy2(path, target)
    if room:
        shutil.copy2(ROOT / "build/CommanderLocalPC.snp", slot / "SNP_DirectIP.snp")
    injector = ENGINE / "injectory.x86.exe"
    if injector.is_file():
        shutil.copy2(injector, slot / "injectory.x86.exe")
    for library in (ENGINE / "crt").glob("*.dll"):
        shutil.copy2(library, slot / library.name)
    for file in (GAME / "characters").glob("*"):
        if file.suffix.lower() in (".mpc", ".spc"):
            shutil.copy2(file, slot / "characters" / file.name)
    if replay:
        map_file, source_map = "input.rep", replay
    else:
        map_file, map_hash = MAPS[map_name]
        source_map = MAP_ROOT / map_file
        verify(source_map, map_hash)
    shutil.copy2(source_map, slot / "maps" / map_file)
    bwapi = ENGINE / "BWAPI.dll" if (ENGINE / "BWAPI.dll").is_file() else GAME / "bwapi-data/BWAPI.dll"
    java = False
    if replay:
        module = "bwapi-data/AI/ReplayControl.dll"
        shutil.copy2(ROOT / "build/ReplayControl.dll", slot / module)
    elif host:
        verify(ROOT / "baseline/Bot.cpp", SOURCE_HASH)
        verify(ROOT / "baseline/Baseline.dll", BASELINE_HASH)
        if build_config is None:
            shutil.copy2(ROOT / "baseline/Baseline.dll", slot / "bwapi-data/AI")
        else:
            shutil.copy2(macro_dll, slot / "bwapi-data/AI" / macro_dll.name)
            (slot / "bwapi-data/AI/build_config.json").write_text(build_config, encoding="utf-8", newline="\n")
        shutil.copy2(ROOT / "build/CommanderLayer.dll", slot / "bwapi-data/AI")
        shutil.copy2(ROOT / "build/ResultsWatcher.dll", slot / "bwapi-data/AI")
        module = "bwapi-data/AI/CommanderLayer.dll"
    else:
        package = ROOT / "opponents" / opponent["name"] / "bwapi-data"
        verify(package / "AI" / entry_name(opponent), opponent["binary_sha256"])
        java = opponent["kind"] == "java"
        if java:
            verify(package / "AI/PurpleWave.jar", opponent["payload_sha256"])
        shutil.copytree(package, slot / "bwapi-data", dirs_exist_ok=True)
        if opponent["backend"] == "legacy":
            bwapi = ENGINE / "BWAPI-3.7.4.dll" if (ENGINE / "BWAPI-3.7.4.dll").is_file() else LAB / "opponents/C/Skynet/BWAPI.dll"
            verify(bwapi, "ef9de2f6557a3ccb108aa206df054a59b4b834f5a85e501bbb0edb9b7c9a5438")
            (slot / "storm.dll").rename(slot / "StormBase.dll")
            shutil.copy2(ROOT / "build/LegacyStorm.dll", slot / "storm.dll")
        elif (package / "BWAPI.dll").exists():
            bwapi = package / "BWAPI.dll"
        module = "NULL" if java else "bwapi-data/AI/" + entry_name(opponent)
        if not java and opponent["backend"] != "legacy":
            shutil.copy2(ROOT / "build/CommanderLayer.dll", slot / "bwapi-data/AI")
            module = "bwapi-data/AI/CommanderLayer.dll"
    shutil.copy2(bwapi, slot / "bwapi-data/BWAPI.dll")
    if expanded:
        source = ROOT / "vendor/resolution-expander/ResolutionExpander.dll"
        verify(source, EXPANDER_HASH)
        shutil.copy2(source, slot / "bwapi-data/ResolutionExpander.dll")
        shutil.copy2(ROOT / "build/WideView.dll", slot / "bwapi-data/WideView.dll")
        width, height = VIEWPORTS[render_profile]
        # The legacy DLL reads its INI beside itself. Keep every legacy +/-
        # slot at the selected size so incidental keys cannot change the view.
        (slot / "bwapi-data/ResSettings.ini").write_text("[Settings]\n" + "".join(
            f"ScreenWidth{i}={width}\nScreenHight{i}={height}\n" for i in range(9)), encoding="ascii")
    shutil.copy2(DISPLAY / "ddraw.dll", slot / "ddraw.dll")
    if host:
        shader = "Shaders/xbr-lv2-noblend.glsl"
        (slot / "Shaders").mkdir()
        shutil.copy2(DISPLAY / "Shaders/xbr/xbr-lv2-noblend.glsl", slot / shader)
    else:
        shader = ""
    (slot / "ddraw.ini").write_text(
        "[ddraw]\nwindowed=true\n"
        f"fullscreen={'true' if host and fullscreen else 'false'}\n"
        f"width={replay_size[0] if replay and replay_size else 0 if host and fullscreen else 960 if host else 640}\n"
        f"height={replay_size[1] if replay and replay_size else 0 if host and fullscreen else 720 if host else 480}\n"
        f"renderer={'opengl' if host else 'direct3d9'}\nshader={shader}\n"
        f"maintas=true\nboxing={'false' if expanded else 'true'}\nborder=false\nsavesettings=0\n"
        f"singlecpu={'false' if room or opponent_hosts else 'true'}\n"
        + ("devmode=true\nposX=-30000\nposY=-30000\ncenter_window=0\n" if not host else "")
        + ("devmode=true\nposX=0\nposY=-32000\ncenter_window=0\n" if replay else "")
        + "[StarCraft]\ngame_handles_close=true\n", encoding="ascii")
    configuration = (
        f"[ai]\nai={module}\nai_dbg={module}\n"
        f"[auto_menu]\nauto_menu={'LAN' if room else 'SINGLE_PLAYER'}\n"
        f"character_name={'StarterBot' if host and room else 'UAlbertaBot' if room else 'StarBench'}\n"
        "lan_mode=Local PC\nauto_restart=OFF\n"
        f"map={'maps/' + map_file if creates_lobby else ''}\n"
        f"game={('UAlbertaBot' if opponent_hosts else 'StarterBot') if room and not creates_lobby else ''}\n"
        "race=Protoss\n"
        "game_type=MELEE\nwait_for_min_players=2\nwait_for_max_players=2\nwait_for_time=2000\n"
        "enemy_count=1\nenemy_race=Protoss\n"
        f"save_replay={'maps/replays/match.rep' if host and not replay else ''}\n"
        f"[config]\nshared_memory={'ON' if java else 'OFF'}\nconsole_attach_auto=FALSE\nconsole_alloc_auto=FALSE\n"
        "[window]\nwindowed=OFF\n"
        "[starcraft]\nsound=OFF\n"
        + (f"speed_override={speed}\n" if not replay else ""))
    (slot / "bwapi-data/bwapi.ini").write_text(configuration, encoding="ascii")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("BWAPI_CONFIG_", "OPENBW_", "COMMANDER_"))}
    env.update(BWAPI_CONFIG_INI=str(slot / "bwapi-data/bwapi.ini"),
               TEMP=str(slot / "tmp"), TMP=str(slot / "tmp"), COMMANDER_CONTROL=control,
               COMMANDER_RENDER_PROFILE=render_profile if host else "standard",
               COMMANDER_NETWORK_SCOPE=room)
    if host and not replay:
        env['COMMANDER_COLOR_POLICY']='self_blue_enemy_red_v1'
    if room:
        env['COMMANDER_DIAGNOSTICS']='1'
    if not host:
        env.update(COMMANDER_ROLE="opponent", COMMANDER_DELEGATE="bwapi-data/AI/" + entry_name(opponent))
    elif build_config is not None and not replay:
        env["COMMANDER_DELEGATE"] = "bwapi-data/AI/" + macro_dll.name
    return env

class GameProcesses:
    def __init__(self):
        self.children = []
        self.files = []

    def start(self, args, directory, env, name):
        output = (directory / name).open("wb")
        self.files.append(output)
        process = subprocess.Popen(args, cwd=directory, env=env, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW)
        self.children.append(process)
        return process

    def launch(self, directory, env):
        libraries = ["bwapi-data\\BWAPI.dll"]
        if env.get("COMMANDER_RENDER_PROFILE") == "expanded":
            libraries.insert(0, "bwapi-data\\WideView.dll")
        return self.start([str(directory / "injectory.x86.exe"), "--verbose", "--print-pid", "--launch",
                           "StarCraft.exe", "--inject", *libraries, "--wait-for-exit"],
                          directory, env, "launcher.log")

    def close(self):
        # Scope cleanup to the exact process trees launched for this match.
        for process in reversed(self.children):
            if process.poll() is None:
                subprocess.run(["taskkill.exe", "/T", "/F", "/PID", str(process.pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=CREATE_NO_WINDOW)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        for output in self.files:
            output.close()

def verify_hwnd(path):
    try:
        hwnd = int(path.read_text())
    except (OSError, ValueError):
        return None
    return hwnd if hwnd and ctypes.windll.user32.IsWindow(wintypes.HWND(hwnd)) else None

def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError):
        return None

@contextmanager
def game_lock():
    lock = ctypes.windll.kernel32.CreateMutexW
    lock.restype = wintypes.HANDLE
    handle = lock(None, False, "Local\\StarCraftCommanderDesktopMatch")
    if not handle:
        raise RuntimeError("无法创建本地对局锁。")
    held = False
    try:
        held = ctypes.windll.kernel32.WaitForSingleObject(wintypes.HANDLE(handle), 0) in (0, 128)
        if not held:
            raise RuntimeError("已有一局正在运行。")
        if subprocess.run(["tasklist.exe", "/FI", "IMAGENAME eq StarCraft.exe", "/NH"],
                          capture_output=True, creationflags=CREATE_NO_WINDOW).stdout.lower().find(b"starcraft.exe") >= 0:
            raise RuntimeError("请先关闭已经打开的 StarCraft 游戏。")
        yield
    finally:
        if held:
            ctypes.windll.kernel32.ReleaseMutex(wintypes.HANDLE(handle))
        ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(handle))

def playing(run, map_name, opponent_name, control="human", speed=42, fullscreen=True,
            update=lambda value: None, smoke_frames=0, opponent_hosts=None, keep_results=None,
            render_profile="expanded", *, data_only=False, match_timeout=None, visible_telemetry=False,
            hold_lock=True, build_config=None, delegate_dll=None, stall_timeout=240):
    if keep_results is None:
        keep_results = not smoke_frames
    if opponent_hosts is None:
        report = read_json(ROOT / "validation/compatibility.json") or {}
        opponent_hosts = f"{opponent_name}|{map_name}" in report.get("opponent_hosts", [])
    # A caller that plays several matches at once holds game_lock() itself for all of them.
    with game_lock() if hold_lock else nullcontext():
        return run_match(run, map_name, opponent_name, control, speed, fullscreen, update,
                         smoke_frames, opponent_hosts, keep_results, render_profile, data_only, match_timeout,
                         visible_telemetry, build_config, delegate_dll=delegate_dll, stall_timeout=stall_timeout)

def run_match(run, map_name, opponent_name, control, speed, fullscreen, update, smoke_frames,
              opponent_hosts, keep_results, render_profile, data_only, match_timeout, visible_telemetry,
              build_config=None, *, delegate_dll=None, stall_timeout=240):
    from replay_info import save_match_labels
    from match_windows import MatchWindows, POLICY as WINDOW_POLICY
    windows = None
    games = GameProcesses()
    peers = GameProcesses()
    macro_dll = Path(delegate_dll) if delegate_dll is not None else MACROBOT
    status = dict(map=map_name, opponent=opponent_name, baseline=BASELINE, baseline_sha256=BASELINE_HASH,
                  source_sha256=SOURCE_HASH, control=control, started_at=datetime.now().astimezone().isoformat(),
                  state="starting", opponent_hosts=opponent_hosts, self_replay_camera="free",
                  player_color_policy="self_blue_enemy_red_v1", render_profile=render_profile,
                  render_size=list(VIEWPORTS[render_profile]))
    if build_config is not None:
        status.update(delegate=macro_dll.stem, delegate_sha256=digest(macro_dll),
                      build_config=json.loads(build_config),
                      build_config_sha256=hashlib.sha256(build_config.encode("utf-8")).hexdigest())

    def save_record():
        replay = newest_replay(run)
        if replay:
            status["replay"] = replay.relative_to(run).as_posix()
        save_match_labels(run, status, replay)
        store(run / "match.json", status)

    try:
        run.mkdir(parents=True)
        store(run / "match.json", status)
        opponent = None if opponent_name == BUILTIN else OPPONENTS[opponent_name]
        room = "SC" + uuid.uuid4().hex[:10] if opponent else ""
        host_env = prepare(run, map_name, room, speed, control, fullscreen=fullscreen,
                           opponent_hosts=opponent_hosts, render_profile=render_profile,
                           build_config=build_config, delegate_dll=delegate_dll)
        host_env["COMMANDER_RESULTS_WATCH"] = "1" if keep_results else "0"
        if data_only:
            host_env["COMMANDER_DATA_ONLY"] = "1"
        if visible_telemetry:
            # The native telemetry switch also polls a test-input file. Leave that
            # path absent: live commands use the normal game window and key hook.
            host_env["COMMANDER_TEST_COMMANDS"] = str(run / "unused-test-input")
        if smoke_frames:
            host_env["COMMANDER_SMOKE_FRAMES"] = str(smoke_frames)
        if opponent:
            enemy = run / "opponent"
            enemy_env = prepare(enemy, map_name, room, speed, control, opponent, fullscreen=False,
                                opponent_hosts=opponent_hosts)
            status["opponent_sha256"] = opponent["binary_sha256"]
        status["layer_sha256"] = digest(run / "bwapi-data/AI/CommanderLayer.dll")
        if opponent:
            status["transport_sha256"] = digest(run / "SNP_DirectIP.snp")
            status["transport_policy"] = "snapshot_lobby_packet_lock_v3"
            status["diagnostic_policy"] = "delegate_transport_logs_v1"
            status["lan_cpu_policy"] = "independent_scheduler_v1"
        if render_profile == "expanded":
            status["display_adapter_sha256"] = digest(run / "bwapi-data/WideView.dll")
            status["display_expander_sha256"] = EXPANDER_HASH
        status["map_sha256"] = MAPS[map_name][1]
        status["window_policy"] = WINDOW_POLICY
        status["window_log"] = "bwapi-data/write/window-focus.jsonl"
        store(run / "match.json", status)
        windows = MatchWindows(run,fullscreen,VIEWPORTS[render_profile],integer=render_profile=="standard")
        windows.start()
        if opponent:
            guest = peers.launch(enemy, enemy_env)
            if opponent["kind"] == "java":
                # JBWAPI reads java.nio.Buffer.address by reflection, which Java 16+ refuses unless
                # java.nio is opened; on the PATH JDK 21 PurpleWave crashed at startup and never moved
                # (matches/rsi/20261124-145321-768219/match-5/opponent/java.log). Java 8 ignores this variable.
                java_env = {**enemy_env, "JDK_JAVA_OPTIONS": "--add-opens=java.base/java.nio=ALL-UNNAMED"}
                peers.start([str(JAVA), "-Xmx768m", "-jar", "bwapi-data\\AI\\PurpleWave.jar"],
                            enemy, java_env, "java.log")
            time.sleep(2)
        host = games.launch(run, host_env)
        output = run / "bwapi-data/write"
        end_file = output / "match-ended.json"
        hwnd_file = output / "game-window.txt"
        deadline = time.monotonic() + 65
        while not verify_hwnd(hwnd_file):
            if windows.failure:
                raise RuntimeError("窗口管理器异常："+windows.failure)
            if host.poll() is not None:
                raise RuntimeError(f"我方客户端在进入对局前已退出（{host.returncode}）。")
            if opponent and guest.poll() is not None:
                raise RuntimeError(f"对手客户端在进入对局前已退出（{guest.returncode}）。")
            if time.monotonic() >= deadline:
                raise RuntimeError("等待双方进入对局超时。详情见对局目录。")
            time.sleep(.15)
        save_match_labels(run, status)
        status["state"] = "playing"
        store(run / "match.json", status)
        update("正在对战。结束后自动保存 .rep，并保留战绩统计页面供你查看。")
        play_deadline = time.monotonic() + (55 if smoke_frames else match_timeout or float("inf"))
        telemetry_log = output / "commander-layer.jsonl"
        last_telemetry_size = telemetry_log.stat().st_size if telemetry_log.is_file() else -1
        last_telemetry_progress = time.monotonic()
        while host.poll() is None and not (end := read_json(end_file)):
            if windows.failure:
                raise RuntimeError("窗口管理器异常："+windows.failure)
            if time.monotonic() >= play_deadline:
                if smoke_frames:
                    raise RuntimeError("验证局未在预期时间内推进。")
                raise TimeoutError("正式对局在时限内未自然结算。")
            if visible_telemetry and not smoke_frames and stall_timeout:
                cur_size = telemetry_log.stat().st_size if telemetry_log.is_file() else -1
                if cur_size != last_telemetry_size:
                    last_telemetry_size = cur_size
                    last_telemetry_progress = time.monotonic()
                elif time.monotonic() - last_telemetry_progress >= stall_timeout:
                    raise RuntimeError(f"Native client stalled without telemetry progress for {stall_timeout}s")
            if opponent and guest.poll() is not None:
                # A peer leaving can itself end the match. Give the host time to save.
                for _ in range(50):
                    if read_json(end_file) or host.poll() is not None:
                        break
                    time.sleep(.1)
                if not read_json(end_file):
                    raise RuntimeError(f"对手客户端在对局中提前退出（{guest.returncode}）。")
            time.sleep(.15)
        end = read_json(end_file)
        if end:
            # onEnd runs before the native replay writer. Wait until its file is stable.
            deadline = time.monotonic() + 8
            previous = None
            while time.monotonic() < deadline:
                replay = newest_replay(run)
                current = (replay, replay.stat().st_size) if replay else None
                if current and current == previous:
                    break
                previous = current
                time.sleep(.4)
            status.update(state="finished", frame=end["frame"], winner=end["winner"],
                          ended_at=datetime.now().astimezone().isoformat(),
                          results_open=bool(keep_results and host.poll() is None))
            if opponent and end["winner"] and end["frame"] < 120:
                raise RuntimeError("对手在开局阶段退出。本局按异常结束保存。")
            if build_config is not None:
                confirm_build_config(run, status["build_config"])
            # Persist the replay and labels before waiting on the user. Only the
            # visible player client stays open; background opponents are finished.
            save_record()
            peers.close()
            if status["results_open"]:
                update("本局已结束，回放已保存。看完战绩后点击 OK，游戏会自动关闭并返回指挥台。")
                while host.poll() is None:
                    dismissed = read_json(output / "results-dismissed.json")
                    if dismissed:
                        status["results_close_reason"] = dismissed["reason"]
                        status["results_menu_state"] = dismissed
                        break
                    time.sleep(.15)
                status["results_open"] = False
                status["window_closed_at"] = datetime.now().astimezone().isoformat()
        else:
            native_errors = [path for path in (run / "Errors").glob("*.txt") if path.stat().st_size]
            if native_errors:
                status["native_error_files"] = [path.relative_to(run).as_posix() for path in native_errors]
                raise RuntimeError(f"游戏异常退出，未正常结束对局。崩溃日志已保留在：{run / 'Errors'}")
            status["state"] = "closed"
    except TimeoutError as error:
        status.update(state="timed_out", error=str(error))
        raise
    except Exception as error:
        status.update(state="error", error=str(error))
        raise
    finally:
        try:
            update("正在整理本局记录…")
            peers.close()
            games.close()
            if run.is_dir():
                status["results_open"] = False
                status.setdefault("ended_at", datetime.now().astimezone().isoformat())
                save_record()
        finally:
            if windows:
                windows.stop()
            if peers.files and any(not output.closed for output in peers.files):
                peers.close()
            if games.files and any(not output.closed for output in games.files):
                games.close()
    return status

def confirm_build_config(run, expected):
    """MacroBot writes config_loaded (or config_error) right after its start event."""
    trace = run / "bwapi-data/write/astra-trace.jsonl"
    rows = []
    if trace.is_file():
        for line in trace.read_text(encoding="utf-8", errors="replace").splitlines()[:20]:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    loaded = next((row for row in rows if row.get("event") in ("config_loaded", "config_error")), None)
    if not loaded or loaded["event"] != "config_loaded" or loaded.get("values") != expected:
        raise RuntimeError(f"MacroBot 未确认加载本局配置：{loaded}")

def new_run(parent=RUNS):
    return parent / datetime.now().strftime("%Y%m%d-%H%M%S-%f")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--map", choices=MAPS, default="Heartbreak Ridge")
    parser.add_argument("--opponent", choices=(*OPPONENTS, BUILTIN), default="PylonPuller")
    parser.add_argument("--speed", type=int, default=42)
    parser.add_argument("--windowed", action="store_true")
    parser.add_argument("--standard-view", action="store_true")
    parser.add_argument("--no-record", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--smoke-frames", type=int, default=0)
    args = parser.parse_args()
    if args.validate:
        validate()
        print(f"{len(MAPS)} maps, {len(OPPONENTS)} opponents, frozen source and DLL verified")
    else:
        run = new_run(VALIDATION)
        print(run, flush=True)
        result = playing(run, args.map, args.opponent, speed=args.speed,
                         fullscreen=not args.windowed,
                         render_profile="standard" if args.standard_view else "expanded",
                         update=lambda value: print(value, flush=True), smoke_frames=args.smoke_frames)
        print(json.dumps(result, ensure_ascii=False), flush=True)

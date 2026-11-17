"""Local installation discovery; the release never contains game data."""
import ctypes
import json
import os
from pathlib import Path
import sys

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "local.settings.json"

def settings():
    try:
        value = json.loads(SETTINGS.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}

def file_version(executable):
    api = ctypes.windll.version
    size = api.GetFileVersionInfoSizeW(str(executable), None)
    if not size:
        return None
    buffer = ctypes.create_string_buffer(size)
    if not api.GetFileVersionInfoW(str(executable), 0, size, buffer):
        return None
    pointer, length = ctypes.c_void_p(), ctypes.c_uint()
    if not api.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)):
        return None
    info = (ctypes.c_uint32 * 13).from_address(pointer.value)
    return info[2] >> 16, info[2] & 65535, info[3] >> 16, info[3] & 65535

def game_error(folder):
    folder = Path(folder)
    exe = folder / "StarCraft.exe"
    if not exe.is_file():
        return "请选择包含 StarCraft.exe 的游戏安装目录。"
    version = file_version(exe)
    if not version or version[:3] != (1, 16, 1):
        return "需要 StarCraft: Brood War 1.16.1；所选目录的版本不匹配。"
    missing = [name for name in ("StarDat.mpq", "BrooDat.mpq", "patch_rt.mpq", "storm.dll") if not (folder / name).is_file()]
    return "安装目录缺少：" + "、".join(missing) if missing else ""

def discover_game():
    candidates = [os.environ.get("STARCRAFT_GAME"), settings().get("game")]
    lab = Path(os.environ.get("STARCRAFT_LAB", "E:/StarCraftAI"))
    candidates.append(lab / "StarCraft1161")
    try:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
                try:
                    with winreg.OpenKey(hive, r"SOFTWARE\Blizzard Entertainment\Starcraft", 0, winreg.KEY_READ | view) as key:
                        candidates.append(winreg.QueryValueEx(key, "InstallPath")[0])
                except OSError:
                    pass
    except ImportError:
        pass
    for value in candidates:
        if value:
            path = Path(value).expanduser()
            if not game_error(path):
                return path.resolve()
    return None

def choose_game(parent, current=None):
    from tkinter import filedialog, messagebox
    while True:
        folder = filedialog.askdirectory(parent=parent, title="选择 StarCraft 1.16.1 安装目录（包含 StarCraft.exe）",
                                         initialdir=str(current) if current else None, mustexist=True)
        if not folder:
            return None
        error = game_error(folder)
        if error:
            messagebox.showerror("游戏目录不可用", error, parent=parent)
            continue
        value = settings()
        value["game"] = str(Path(folder).resolve())
        SETTINGS.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        return Path(folder).resolve()

def ensure_game(parent):
    return discover_game() or choose_game(parent)

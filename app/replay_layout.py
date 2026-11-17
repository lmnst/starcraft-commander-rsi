"""Arrange our replay client without resizing its game image."""
import ctypes
from ctypes import wintypes


def original_replay_size(fullscreen):
    """Preserve the former fullscreen integer-scaled 640x480 game viewport.

    Windows may expose half-sized logical coordinates at 200% scaling, while
    cnc-ddraw's width/height settings use physical pixels.
    """
    if not fullscreen:
        return 960, 720
    user32 = ctypes.windll.user32
    set_context = user32.SetThreadDpiAwarenessContext
    set_context.argtypes = [ctypes.c_void_p]
    set_context.restype = ctypes.c_void_p
    prior = set_context(ctypes.c_void_p(-4))
    try:
        width, height = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    finally:
        if prior:
            set_context(prior)
    scale = max(1, min(width//640, height//480))
    return 640*scale, 480*scale


def sidebar_geometry(work, game_size):
    left, top, right, bottom = work
    width, height = game_size
    game_top = top + max(0, (bottom-top-height)//2)
    sidebar_left = left + width + 8
    available = right-sidebar_left-8
    if available < 180:
        raise RuntimeError("当前屏幕右侧不足以容纳复盘面板，请使用更宽的显示区域。")
    return (left, game_top), (sidebar_left, top, min(360, available), bottom-top)


def pointer_inside_game(hwnd, process_id):
    """Check outside the game process, where cnc-ddraw cannot clamp coordinates."""
    user32 = ctypes.windll.user32
    point = wintypes.POINT()
    rect = wintypes.RECT()
    foreground_pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(foreground_pid))
    handle = wintypes.HWND(hwnd)
    if foreground_pid.value != process_id or not user32.GetCursorPos(ctypes.byref(point)) or not user32.GetWindowRect(handle, ctypes.byref(rect)):
        return False
    under = user32.WindowFromPoint(point)
    return bool(rect.left <= point.x < rect.right and rect.top <= point.y < rect.bottom and
                (under == hwnd or user32.IsChild(handle, wintypes.HWND(under))))


def dock_game(hwnd, process_id):
    """Use this process's DPI coordinates, shared by Tk and Win32 calls."""
    user32 = ctypes.windll.user32
    handle = wintypes.HWND(hwnd)
    actual_pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(handle, ctypes.byref(actual_pid))
    if not user32.IsWindow(handle) or actual_pid.value != process_id:
        raise RuntimeError("回放窗口已关闭，无法排列复盘布局。")

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                    ("work", wintypes.RECT), ("flags", wintypes.DWORD)]

    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    monitor = user32.MonitorFromWindow(handle, 2)
    info = MonitorInfo()
    info.size = ctypes.sizeof(info)
    rect = wintypes.RECT()
    if not user32.GetMonitorInfoW(wintypes.HANDLE(monitor), ctypes.byref(info)) or not user32.GetWindowRect(handle, ctypes.byref(rect)):
        raise RuntimeError("无法读取回放窗口大小。")
    work = info.work
    position, sidebar = sidebar_geometry((work.left, work.top, work.right, work.bottom),
                                        (rect.right-rect.left, rect.bottom-rect.top))
    # SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE: only change its position.
    if not user32.SetWindowPos(handle, None, *position, 0, 0, 0x0015):
        raise RuntimeError("无法将回放窗口移到屏幕左侧。")
    after = wintypes.RECT()
    if not user32.GetWindowRect(handle, ctypes.byref(after)) or (after.right-after.left, after.bottom-after.top) != (rect.right-rect.left, rect.bottom-rect.top):
        raise RuntimeError("回放窗口尺寸发生变化，已停止排列。")
    return sidebar

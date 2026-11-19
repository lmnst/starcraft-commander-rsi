"""Manage only this match's two native game windows. Geometry/logs, no capture."""
import ctypes
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import threading
import time

POLICY = "isolated_opponent_focus_v1"

def viewport_rect(client, render_size, integer=False):
    left, top, right, bottom = client
    width, height = right-left, bottom-top
    scale = min(width/render_size[0], height/render_size[1])
    if integer and scale >= 1:
        scale = int(scale)
    view_w, view_h = round(render_size[0]*scale), round(render_size[1]*scale)
    x, y = left+(width-view_w)//2, top+(height-view_h)//2
    return [x, y, x+view_w, y+view_h]

class ProcessEntry(ctypes.Structure):
    _fields_ = [("size", W.DWORD), ("usage", W.DWORD), ("pid", W.DWORD),
                ("heap", ctypes.c_size_t), ("module", W.DWORD), ("threads", W.DWORD),
                ("parent", W.DWORD), ("priority", W.LONG), ("flags", W.DWORD),
                ("exe", W.WCHAR*260)]

class MonitorInfo(ctypes.Structure):
    _fields_ = [("size", W.DWORD), ("monitor", W.RECT), ("work", W.RECT), ("flags", W.DWORD)]

class Windows:
    def __init__(self):
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        def bind(lib, name, args, result):
            fn = getattr(lib, name);fn.argtypes=args;fn.restype=result
            return fn
        self.context = bind(self.user,"SetThreadDpiAwarenessContext",[ctypes.c_void_p],ctypes.c_void_p)
        self.enum_callback = ctypes.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
        bind(self.user,"EnumWindows",[self.enum_callback,W.LPARAM],W.BOOL)
        bind(self.user,"GetForegroundWindow",[],W.HWND)
        bind(self.user,"GetWindowThreadProcessId",[W.HWND,ctypes.POINTER(W.DWORD)],W.DWORD)
        bind(self.user,"GetWindow",[W.HWND,W.UINT],W.HWND)
        bind(self.user,"GetClassNameW",[W.HWND,W.LPWSTR,ctypes.c_int],ctypes.c_int)
        for name in ("IsWindow","IsWindowVisible","IsIconic"):
            bind(self.user,name,[W.HWND],W.BOOL)
        for name in ("GetWindowRect","GetClientRect"):
            bind(self.user,name,[W.HWND,ctypes.POINTER(W.RECT)],W.BOOL)
        bind(self.user,"ClientToScreen",[W.HWND,ctypes.POINTER(W.POINT)],W.BOOL)
        bind(self.user,"GetClipCursor",[ctypes.POINTER(W.RECT)],W.BOOL)
        bind(self.user,"ClipCursor",[ctypes.POINTER(W.RECT)],W.BOOL)
        bind(self.user,"ShowWindowAsync",[W.HWND,ctypes.c_int],W.BOOL)
        bind(self.user,"SetForegroundWindow",[W.HWND],W.BOOL)
        bind(self.user,"SetWindowPos",[W.HWND,W.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,W.UINT],W.BOOL)
        bind(self.user,"AttachThreadInput",[W.DWORD,W.DWORD,W.BOOL],W.BOOL)
        bind(self.user,"MonitorFromWindow",[W.HWND,W.DWORD],W.HANDLE)
        bind(self.user,"GetMonitorInfoW",[W.HANDLE,ctypes.POINTER(MonitorInfo)],W.BOOL)
        bind(self.kernel,"GetCurrentThreadId",[],W.DWORD)
        bind(self.kernel,"CreateToolhelp32Snapshot",[W.DWORD,W.DWORD],W.HANDLE)
        for name in ("Process32FirstW","Process32NextW"):
            bind(self.kernel,name,[W.HANDLE,ctypes.POINTER(ProcessEntry)],W.BOOL)
        bind(self.kernel,"OpenProcess",[W.DWORD,W.BOOL,W.DWORD],W.HANDLE)
        bind(self.kernel,"CloseHandle",[W.HANDLE],W.BOOL)
        bind(self.kernel,"QueryFullProcessImageNameW",[W.HANDLE,W.DWORD,W.LPWSTR,ctypes.POINTER(W.DWORD)],W.BOOL)

    def image(self,pid):
        handle=self.kernel.OpenProcess(0x1000,False,pid)
        # StarCraft's legacy process ACL allows QUERY_INFORMATION, but may
        # reject the newer QUERY_LIMITED_INFORMATION access bit.
        if not handle:handle=self.kernel.OpenProcess(0x0400,False,pid)
        if not handle:return None
        try:
            size=W.DWORD(32768);text=ctypes.create_unicode_buffer(size.value)
            return os.path.normcase(text.value) if self.kernel.QueryFullProcessImageNameW(handle,0,text,ctypes.byref(size)) else None
        finally:self.kernel.CloseHandle(handle)

    def processes(self, paths):
        found={};handle=self.kernel.CreateToolhelp32Snapshot(2,0)
        if handle==ctypes.c_void_p(-1).value:return found
        try:
            entry=ProcessEntry();entry.size=ctypes.sizeof(entry)
            ok=self.kernel.Process32FirstW(handle,ctypes.byref(entry))
            while ok:
                if entry.exe.lower()=="starcraft.exe":
                    path=self.image(entry.pid)
                    if path in paths:found[entry.pid]=paths[path]
                ok=self.kernel.Process32NextW(handle,ctypes.byref(entry))
        finally:self.kernel.CloseHandle(handle)
        return found

    def owner(self,hwnd):
        pid=W.DWORD();thread=self.user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        return pid.value,thread

    def rect(self,hwnd,client=False):
        rect=W.RECT();fn=self.user.GetClientRect if client else self.user.GetWindowRect
        if not fn(hwnd,ctypes.byref(rect)):return None
        return [rect.left,rect.top,rect.right,rect.bottom]

    def client_screen(self,hwnd):
        rect=self.rect(hwnd,True);point=W.POINT(0,0)
        if rect is None or not self.user.ClientToScreen(hwnd,ctypes.byref(point)):return None
        return [point.x,point.y,point.x+rect[2],point.y+rect[3]]

    def monitor(self,hwnd):
        info=MonitorInfo();info.size=ctypes.sizeof(info)
        if not self.user.GetMonitorInfoW(self.user.MonitorFromWindow(hwnd,2),ctypes.byref(info)):return None
        r=info.monitor
        return [r.left,r.top,r.right,r.bottom]

    def clip(self):
        rect=W.RECT()
        if not self.user.GetClipCursor(ctypes.byref(rect)):return None
        return [rect.left,rect.top,rect.right,rect.bottom]

    def set_clip(self,rect=None):
        value=W.RECT(*rect) if rect else None
        return bool(self.user.ClipCursor(ctypes.byref(value) if value else None))

    def windows(self,owned):
        result=[]
        def visit(hwnd,_):
            pid,_thread=self.owner(hwnd)
            if pid in owned:
                text=ctypes.create_unicode_buffer(256);self.user.GetClassNameW(hwnd,text,256)
                result.append(dict(hwnd=int(hwnd),pid=pid,role=owned[pid],window=self.rect(hwnd),
                    client=self.rect(hwnd,True),visible=bool(self.user.IsWindowVisible(hwnd)),
                    iconic=bool(self.user.IsIconic(hwnd)),owner=int(self.user.GetWindow(hwnd,4) or 0),window_class=text.value))
            return True
        callback=self.enum_callback(visit);self.user.EnumWindows(callback,0)
        return result

    def focus(self,hwnd,attach=False):
        current=self.kernel.GetCurrentThreadId();foreground=self.user.GetForegroundWindow()
        _pid,other=self.owner(foreground);linked=False
        try:
            if attach and other and other!=current:linked=bool(self.user.AttachThreadInput(current,other,True))
            if self.user.IsIconic(hwnd):self.user.ShowWindowAsync(hwnd,9)
            return bool(self.user.SetForegroundWindow(hwnd))
        finally:
            if linked:self.user.AttachThreadInput(current,other,False)

class MatchWindows:
    def __init__(self,run,fullscreen,render_size,integer=False):
        self.run=Path(run);self.fullscreen=fullscreen;self.render_size=render_size;self.integer=integer
        self.paths={os.path.normcase(str((self.run/"StarCraft.exe").resolve())):"self",
                    os.path.normcase(str((self.run/"opponent/StarCraft.exe").resolve())):"opponent"}
        self.stopping=threading.Event();self.ready=threading.Event();self.failure=None
        self.thread=threading.Thread(target=self._watch,name="match-windows",daemon=True)

    def start(self):
        self.thread.start()
        if not self.ready.wait(5):raise RuntimeError("窗口管理器启动超时。")
        if self.failure:raise RuntimeError("窗口管理器启动失败："+self.failure)

    def stop(self):
        self.stopping.set();self.thread.join(timeout=5)

    def _watch(self):
        path=self.run/"bwapi-data/write/window-focus.jsonl";started=time.monotonic()
        with path.open("w",encoding="utf8") as log:
            def record(event,**data):
                log.write(json.dumps(dict(event=event,seconds=round(time.monotonic()-started,3),**data),ensure_ascii=False)+"\n");log.flush()
            prior=None
            try:
                api=Windows();prior=api.context(ctypes.c_void_p(-4))
                record("window_manager_start",policy=POLICY,capture=False,physical_pixels=True,fullscreen=self.fullscreen,render_size=self.render_size)
                self.ready.set();previous=None;last=0;activated=False;last_actions={};owned_clip=None;previous_role="other"
                while not self.stopping.is_set():
                    owned=api.processes(self.paths);windows=api.windows(owned)
                    foreground=int(api.user.GetForegroundWindow() or 0);fg_pid,_=api.owner(foreground);role=owned.get(fg_pid,"other")
                    if role=="other" and previous_role in ("self","opponent") and owned_clip and api.clip()==owned_clip:
                        record("release_self_cursor",accepted=api.set_clip(),reason="user_switched_application")
                        owned_clip=None
                    # OpenGL also owns hidden pbuffer windows; their area must
                    # never outrank the actual game when its window shrinks.
                    main=[w for w in windows if w["role"]=="self" and w["window_class"]=="SWarClass" and not w["owner"]]
                    main=max(main,key=lambda w:w["client"][2]*w["client"][3]) if main else None
                    def checked(window):
                        pid,_=api.owner(window["hwnd"])
                        return pid==window["pid"] and self.paths.get(api.image(pid))==window["role"]
                    def action(event,window,**data):
                        record(event,role=window["role"],hwnd=window["hwnd"],pid=window["pid"],**data)
                    # Recover ONLY from our verified peer, never repeatedly
                    # steal focus from an application the user chose via Alt+Tab.
                    if main and main["visible"] and checked(main) and (role=="opponent" or not activated):
                        ok=api.focus(main["hwnd"],attach=role=="opponent")
                        action("activate_self",main,reason="peer_foreground" if role=="opponent" else "startup",accepted=ok)
                        activated=True
                    for window in windows:
                        if window["role"]=="opponent" and window["visible"] and checked(window):
                            key=("hide",window["hwnd"]);now=time.monotonic()
                            if now-last_actions.get(key,-1000)>=.5:
                                ok=bool(api.user.ShowWindowAsync(window["hwnd"],0));last_actions[key]=now
                                action("hide_opponent",window,accepted=ok)
                                if not main and int(api.user.GetForegroundWindow() or 0)==window["hwnd"]:
                                    record("release_peer_cursor",accepted=api.set_clip())
                    # onStart's marker distinguishes the real game surface
                    # from temporary 640x480 menu windows during initialization.
                    marker=self.run/"bwapi-data/write/game-window.txt"
                    try:game_hwnd=int(marker.read_text()) if marker.is_file() else 0
                    except (ValueError,OSError):game_hwnd=0
                    if game_hwnd and api.user.IsWindow(game_hwnd):
                        game_pid,_=api.owner(game_hwnd)
                        if self.paths.get(api.image(game_pid))!="self":
                            raise RuntimeError("无法核验本局我方游戏进程路径，已停止窗口操作。")
                    active=int(api.user.GetForegroundWindow() or 0)
                    if main and checked(main) and game_hwnd==main["hwnd"] and active==game_hwnd and not main["iconic"] and self.fullscreen and not (self.run/"bwapi-data/write/match-ended.json").exists():
                        bounds=api.monitor(game_hwnd);client=api.client_screen(game_hwnd)
                        if bounds and client:
                            if max(abs(a-b) for a,b in zip(client,bounds))>4:
                                key=("fullscreen",game_hwnd);now=time.monotonic()
                                if now-last_actions.get(key,-1000)>=1:
                                    left,top,right,bottom=bounds
                                    ok=bool(api.user.SetWindowPos(game_hwnd,None,left,top,right-left,bottom-top,0x4014))
                                    action("restore_fullscreen",main,before=client,expected=bounds,accepted=ok);last_actions[key]=now
                            else:
                                expected=viewport_rect(client,self.render_size,self.integer);clip=api.clip()
                                if clip and max(abs(a-b) for a,b in zip(clip,expected))>8:
                                    action("repair_mouse_clip",main,before=clip,expected=expected,accepted=api.set_clip(expected))
                                owned_clip=api.clip()
                    state=dict(foreground_role=role,foreground_hwnd=foreground,clip=api.clip(),windows=windows)
                    now=time.monotonic()
                    if state!=previous or now-last>=1:
                        record("window_state",**state);previous=state;last=now
                    previous_role=role
                    self.stopping.wait(.1)
            except Exception as error:
                self.failure=repr(error);record("window_manager_error",error=self.failure);self.ready.set()
            finally:
                if prior:api.context(prior)
                record("window_manager_stop")

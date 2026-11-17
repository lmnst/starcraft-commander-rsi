"""Native replay with a tall review sidebar and movable playback controls."""
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from matches import ROOT, GameProcesses, game_lock, new_run, prepare, read_json, store
from replay_info import describe, load_labels
from replay_review import ReplayReview
from replay_layout import dock_game, original_replay_size, pointer_inside_game
from replay_perspective import load_camera


class NativeReplay:
    def __init__(self, replay, fullscreen, update, view_mode="all"):
        self.replay = Path(replay).resolve(strict=True)
        if self.replay.suffix.lower() != ".rep" or not self.replay.stat().st_size:
            raise ValueError("找不到有效的 .rep 录像。")
        # Remove only the unused fullscreen margins; retain the original
        # physical game-image size rather than falling back to a small window.
        self.replay_size = original_replay_size(fullscreen)
        self.fullscreen = False
        self.update = update
        self.commands = queue.Queue()
        self.stopping = threading.Event()
        self.directory = new_run(ROOT / "playback")
        self.paused = False
        self.speed = 42
        self.labels = load_labels(self.replay)
        # Historical recordings retain their original rendering extent. New
        # recordings carry the display profile without changing the .rep.
        self.render_profile = self.labels.get("render_profile", "standard")
        if self.render_profile not in ("standard", "expanded"):
            raise ValueError("录像的视野设置无效。")
        if view_mode not in ("all", "self"):
            raise ValueError("未知回放视角。")
        self.view_mode = view_mode
        self.camera_mode = self.labels.get("self_replay_camera", "recorded") if view_mode == "self" else "free"
        self.camera_track = load_camera(self.replay) if view_mode == "self" and self.camera_mode != "free" else None
        if view_mode == "self" and self.camera_mode == "free":
            player = self.labels.get("self_player_id")
            if type(player) is not int or not 0 <= player < 12:
                raise ValueError("缺少我方玩家身份，无法确定我方视野。")
        elif view_mode == "self":
            if self.camera_track is None:
                raise ValueError("这局未记录完整的我方镜头，无法播放我方回放。新对局会自动记录。")
            if self.labels.get("self_player_id") != self.camera_track.player_id:
                raise ValueError("我方镜头记录与对局玩家身份不一致，已停止播放。")

    def request(self, action, frame=0, paused=False, speed=42):
        self.commands.put((action, int(frame), bool(paused), int(speed)))

    def close(self):
        self.stopping.set()

    def run(self):
        with game_lock():
            self.directory.mkdir(parents=True)
            store(self.directory / "replay.json", {"source": str(self.replay), "player": "StarCraft 1.16.1", "view_mode": self.view_mode})
            target = 0
            generation = 0
            while not self.stopping.is_set():
                slot = self.directory / f"load-{generation:03d}"
                env = prepare(slot, None, "", self.speed, "baseline",
                              fullscreen=self.fullscreen, replay=self.replay, replay_size=self.replay_size,
                              render_profile=self.render_profile)
                env.update(REPLAY_INITIAL_FRAME=str(target),
                           REPLAY_INITIAL_PAUSED="1" if self.paused else "0",
                           REPLAY_INITIAL_SPEED=str(self.speed))
                env["REPLAY_VIEW_MODE"] = self.view_mode
                env["REPLAY_CAMERA_MODE"] = self.camera_mode
                if self.labels.get('player_color_policy')=='self_blue_enemy_red_v1':
                    env['COMMANDER_COLOR_POLICY']=self.labels['player_color_policy']
                    env['REPLAY_COLOR_SELF']=str(self.labels.get('self_player_id',-1))
                if self.view_mode == "self":
                    env["REPLAY_SELF_PLAYER"] = str(self.labels["self_player_id"])
                if self.camera_track is not None:
                    env["REPLAY_SELF_PLAYER"] = str(self.camera_track.player_id)
                    (slot / "bwapi-data/read/original-camera.txt").write_text(
                        "".join(f"{frame} {x} {y}\n" for frame, x, y in self.camera_track.samples), encoding="ascii")
                games = GameProcesses()
                process = None
                state = None
                self.update({"phase": "loading", "frame": 0, "target": target, "paused": self.paused})
                try:
                    process = games.launch(slot, env)
                    self.record_lifecycle(slot, "launched", process=process)
                    status_file = slot / "bwapi-data/write/replay-status.json"
                    deadline = time.monotonic() + 60
                    sequence = 0
                    state = None
                    identity_loaded = False
                    reload_requested = False
                    last_pointer_state = None
                    while not self.stopping.wait(.08):
                        observed = read_json(status_file)
                        if observed:
                            if observed.get("perspective_error"):
                                raise RuntimeError("无法还原我方视角：" + observed["perspective_error"])
                            if observed.get("window_handle"):
                                inside = pointer_inside_game(observed["window_handle"], observed["process_id"])
                                if inside != last_pointer_state:
                                    pointer_file = slot / "bwapi-data/read/replay-pointer.txt"
                                    temporary = pointer_file.with_suffix(".tmp")
                                    temporary.write_text("1" if inside else "0", encoding="ascii")
                                    temporary.replace(pointer_file)
                                    last_pointer_state = inside
                            if not identity_loaded:
                                identity = read_json(slot / "bwapi-data/write/match-identity.json")
                                if identity:
                                    self.labels.update({key: identity[key] for key in ("map_actual", "players")})
                                    store(self.directory / "replay.json", {"source": str(self.replay),
                                          "player": "StarCraft 1.16.1", "labels": self.labels, "view_mode": self.view_mode})
                                    identity_loaded = True
                            state = observed
                            self.update(dict(observed, phase="ready", labels=self.labels))
                        elif time.monotonic() > deadline:
                            raise RuntimeError("原版游戏未能载入这份录像。")
                        if process.poll() is not None:
                            self.record_lifecycle(slot, "process_exit", process=process, state=state)
                            if state is None:
                                raise RuntimeError(f"原版游戏在载入录像前退出，详情见 {slot / 'launcher.log'}")
                            if process.returncode != 0 and not self.stopping.is_set():
                                raise RuntimeError(f"回放进程异常退出（代码 {process.returncode}），记录保存在 {slot}。")
                            return
                        if not state:
                            continue
                        # Coalesce a series of slider releases to the latest request.
                        pending = None
                        while not self.commands.empty():
                            pending = self.commands.get_nowait()
                        if pending:
                            action, requested, self.paused, self.speed = pending
                            total = max(0, state["total"] - 1)
                            if action == "seek":
                                target = max(0, min(total, requested))
                                reload_requested = target < state["frame"] or state["ended"]
                            elif state["ended"] and not self.paused:
                                target = 0
                                reload_requested = True
                            if reload_requested:
                                break
                            sequence += 1
                            command = slot / "bwapi-data/read/replay-command.txt"
                            temporary = command.with_suffix(".tmp")
                            temporary.write_text(
                                f"{sequence} {action} {target} {int(self.paused)} {self.speed}\n", encoding="ascii")
                            temporary.replace(command)
                        if state.get("reload"):
                            target = state["target"]
                            reload_requested = True
                            break
                    if not reload_requested:
                        self.record_lifecycle(slot, "closed", process=process, state=state)
                        return
                    self.record_lifecycle(slot, "reload", process=process, state=state, target=target)
                except Exception as error:
                    self.record_lifecycle(slot, "error", process=process, state=state, error=str(error))
                    raise
                finally:
                    games.close()
                generation += 1

    def record_lifecycle(self, slot, event, process=None, state=None, **extra):
        """Best-effort diagnostics must never interrupt or kill playback."""
        try:
            value = dict(event=event, slot=slot.name, source=str(self.replay),
                         launcher_pid=process.pid if process else None,
                         exit_code=process.poll() if process else None, state=state, **extra)
            with (self.directory / "session-events.jsonl").open("a", encoding="utf-8") as output:
                import json
                output.write(json.dumps(value, ensure_ascii=False)+"\n")
        except (OSError, ValueError):
            pass


def replay_time(frame):
    seconds = max(0, int(frame)) * 42 // 1000
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


class ReplayPanel:
    def __init__(self, parent, replay, fullscreen, finished, view_mode="all"):
        self.parent, self.finished = parent, finished
        self.events = queue.Queue()
        self.dragging = False
        self.total = self.current = 0
        self.paused = self.closed = self.ended = False
        self.session = NativeReplay(replay, fullscreen, self.events.put, view_mode=view_mode)
        self.review = ReplayReview(replay)
        self.review_expanded = self.review_visible = True
        self.review_ready = False
        self.review_anchor = None
        self.command_index = None
        self.pending_action_frame = None
        self.docked_handle = None
        self.dock_rectangle = None
        self.controls_on_right = False
        self.window = tk.Toplevel(parent)
        self.window.title("StarCraft 我方回放" if view_mode == "self" else "StarCraft 全场回放")
        self.window.resizable(False, False)
        self.window.attributes("-topmost", True)
        self.window.configure(bg="#101923")
        screen_width, screen_height = self.window.winfo_screenwidth(), self.window.winfo_screenheight()
        self.panel_width = min(360, max(180, screen_width-976))
        self.window.geometry(f"{self.panel_width}x{screen_height-40}+{screen_width-self.panel_width-8}+0")
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.status = tk.StringVar(value="正在载入原版游戏录像…")
        self.details = tk.StringVar(value=describe(self.session.labels))
        self.speed = tk.StringVar(value="1×")
        header = tk.Frame(self.window, bg="#192432")
        header.pack(fill="x")
        tk.Label(header, text="我方回放" if view_mode == "self" else "全场回放", bg="#192432", fg="#5edbc3",
                 font=("Microsoft YaHei UI", 12, "bold")).pack(side="left", padx=10, pady=8)
        self.close_button = self.make_button(header, "关闭回放", self.close)
        self.close_button.pack(side="right", padx=6)
        self.controls_placement = self.make_button(self.window, "播放控制：底部 → 放到右侧", self.toggle_controls_placement)
        self.controls_placement.pack(fill="x", padx=8, pady=5)
        self.control_slot = tk.Frame(self.window, bg="#101923", height=112)

        # A scrolling column keeps long text inside the narrow sidebar.
        self.review_canvas = tk.Canvas(self.window, bg="#101923", highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.window, orient="vertical", command=self.review_canvas.yview)
        scrollbar.pack(side="right", fill="y")
        self.review_canvas.pack(side="left", fill="both", expand=True)
        self.review_canvas.configure(yscrollcommand=scrollbar.set)
        self.content = tk.Frame(self.review_canvas, bg="#101923")
        self.content_item = self.review_canvas.create_window(0, 0, anchor="nw", window=self.content)
        self.wrapped_labels = []
        self.review_canvas.bind("<Configure>", self.resize_content)
        self.content.bind("<Configure>", lambda _: self.review_canvas.configure(scrollregion=self.review_canvas.bbox("all")))
        self.window.bind("<MouseWheel>", self.scroll_review)
        self.add_label(self.content, variable=self.details, color="#aebdcc")
        self.add_label(self.content, text=("我方视野 · 可自由移动镜头" if self.session.camera_mode == "free" else "我方视野 · 跟随当时真实镜头") if view_mode == "self" else "全场视野 · 可自由查看", color="#5edbc3")
        self.review_mode = self.add_label(self.content, color="#5edbc3", bold=True)
        self.add_label(self.content, text="当前指令", color="#5edbc3", bold=True)
        self.review_title = self.add_label(self.content, color="#f1f5f9")
        self.review_effect = self.add_label(self.content, color="#f4c577")
        self.action_position = self.add_label(self.content, color="#8396a7")
        navigation = tk.Frame(self.content, bg="#101923")
        navigation.pack(fill="x", padx=8, pady=8)
        self.previous_command = self.make_button(navigation, "上一指令", lambda: self.jump_command(-1))
        self.previous_command.pack(side="left")
        self.next_command = self.make_button(navigation, "下一指令", lambda: self.jump_command(1))
        self.next_command.pack(side="left", padx=5)
        self.add_label(self.content, text="部队与经济", color="#5edbc3", bold=True)
        self.review_stats = self.add_label(self.content, color="#aebdcc")
        self.review_toggle = self.make_button(self.content, "收起最近记录 ▴", self.toggle_review)
        self.review_toggle.pack(fill="x", padx=8, pady=8)
        self.review_history = tk.Frame(self.content, bg="#101923")
        self.review_history.pack(fill="x")
        self.history_labels = [self.add_label(self.review_history, color="#aebdcc") for _ in range(2)]
        self.add_label(self.content, text="说明：收到指令不代表达成目的。\n变化记录不能直接归因于按键。", color="#8396a7")

        # Both control placements share session state; sidebar controls are
        # embedded widgets so they cannot disappear behind another window.
        self.controls_window = tk.Toplevel(self.window)
        self.controls_window.title("StarCraft 原版回放进度")
        self.controls_window.overrideredirect(True)
        self.controls_window.attributes("-topmost", True)
        self.controls_window.configure(bg="#192432")
        self.controls_window.protocol("WM_DELETE_WINDOW", self.close)
        self.layout_controls()
        self.render_review(0)

        def play():
            try:
                self.session.run()
                self.events.put({"phase": "closed"})
            except Exception as error:
                self.events.put({"phase": "error", "message": str(error)})
        threading.Thread(target=play, daemon=False).start()
        self.window.after(100, self.poll)

    @staticmethod
    def make_button(parent, text, command):
        return tk.Button(parent, text=text, command=command, relief="flat", borderwidth=0,
                         bg="#293a4c", fg="#f1f5f9", activebackground="#3b5067", activeforeground="#ffffff",
                         disabledforeground="#697b8c", cursor="hand2", padx=7, pady=5,
                         font=("Microsoft YaHei UI", 9))

    def add_label(self, parent, text="", variable=None, color="#f1f5f9", bold=False):
        label = tk.Label(parent, text=text, textvariable=variable, bg="#101923", fg=color,
                         anchor="w", justify="left", wraplength=self.panel_width-36,
                         font=("Microsoft YaHei UI", 9, "bold" if bold else "normal"))
        label.pack(fill="x", padx=10, pady=5)
        self.wrapped_labels.append(label)
        return label

    def resize_content(self, event):
        self.review_canvas.itemconfigure(self.content_item, width=event.width)
        for label in self.wrapped_labels:
            label.configure(wraplength=max(80, event.width-24))

    def scroll_review(self, event):
        self.review_canvas.yview_scroll(-int(event.delta/120), "units")
        return "break"

    def fit_review(self, initial=False):
        # Keep the sidebar's tall geometry fixed as content changes.
        self.window.update_idletasks()

    def arrange_replay(self, state):
        handle = state.get("window_handle")
        if not handle or handle == self.docked_handle:
            return
        rectangle = dock_game(handle, state["process_id"])
        self.docked_handle, self.dock_rectangle = handle, rectangle
        left, top, width, height = rectangle
        self.panel_width = width
        # Reserve the native title bar and border inside the monitor work area.
        self.window.geometry(f"{width}x{max(200, height-34)}{left:+d}{top:+d}")
        self.window.update_idletasks()
        self.layout_controls()

    def build_controls(self, parent):
        self.control_row = row = tk.Frame(parent, bg="#192432")
        row.pack(fill="both", expand=True, padx=5, pady=4)
        self.grip = tk.Label(row, text="⋮⋮", bg="#192432", fg="#91a4b7", cursor="fleur")
        self.play_button = self.make_button(row, "暂停", self.toggle_pause)
        self.play_button.configure(state="disabled")
        self.speed_box = ttk.Combobox(row, state="readonly", width=4, textvariable=self.speed,
                                     values=("0.5×", "1×", "2×", "4×", "8×"))
        self.speed_box.bind("<<ComboboxSelected>>", lambda _: self.send_playback())
        self.control_status = tk.Label(row, textvariable=self.status, bg="#192432", fg="#f1f5f9",
                                       font=("Microsoft YaHei UI", 8), anchor="w")
        self.progress = ttk.Scale(row, from_=0, to=1, orient="horizontal", state="disabled")
        for handle in (self.grip,):
            handle.bind("<ButtonPress-1>", self.begin_move)
            handle.bind("<B1-Motion>", self.move)
        self.progress.bind("<ButtonPress-1>", self.begin_drag)
        self.progress.bind("<ButtonRelease-1>", self.end_drag)
        self.progress.bind("<B1-Motion>", self.preview)
        self.progress.bind("<KeyRelease-Left>", self.keyboard_seek)
        self.progress.bind("<KeyRelease-Right>", self.keyboard_seek)
        self.progress.configure(to=max(1, self.total), state="normal" if self.total else "disabled")
        self.progress.set(self.current)
        self.play_button.configure(state="normal" if self.total else "disabled",
                                   text="重播" if self.ended else "继续" if self.paused else "暂停")

    def layout_controls(self):
        if hasattr(self, "control_row"):
            self.control_row.destroy()
        if self.controls_on_right:
            self.controls_window.withdraw()
            self.control_slot.pack(fill="x", before=self.review_canvas, padx=8, pady=4)
            self.build_controls(self.control_slot)
        else:
            self.control_slot.pack_forget()
            self.controls_window.deiconify()
            self.build_controls(self.controls_window)
        for col in range(5):
            self.control_row.columnconfigure(col, weight=0)
        if self.controls_on_right:
            self.control_slot.pack(fill="x", before=self.review_canvas, padx=8, pady=4)
            self.window.update_idletasks()
            width = max(160, self.panel_width-16)
            self.grip.grid(row=0, column=0)
            self.play_button.grid(row=0, column=1, padx=4)
            self.speed_box.grid(row=0, column=2)
            self.progress.grid(row=1, column=0, columnspan=4, sticky="ew", pady=5)
            self.control_status.grid(row=2, column=0, columnspan=4, sticky="ew")
            self.control_row.columnconfigure(3, weight=1)
            self.control_status.configure(wraplength=width-12)
        else:
            self.control_slot.pack_forget()
            width, height = 480, 60
            self.grip.grid(row=0, column=0)
            self.play_button.grid(row=0, column=1, padx=4)
            self.speed_box.grid(row=0, column=2)
            self.progress.grid(row=0, column=3, sticky="ew", padx=5)
            self.control_status.grid(row=1, column=0, columnspan=4, sticky="ew")
            self.control_row.columnconfigure(3, weight=1)
            self.control_status.configure(wraplength=width-12)
            if self.dock_rectangle:
                _, work_top, _, work_height = self.dock_rectangle
                # The game's actual rectangle is read in the same DPI context.
                import ctypes
                from ctypes import wintypes
                rect = wintypes.RECT()
                ctypes.windll.user32.GetWindowRect(wintypes.HWND(self.docked_handle), ctypes.byref(rect))
                game_width = rect.right-rect.left
                left = rect.left + max(0, (game_width-width)//2)
                top = max(rect.top, min(rect.bottom-height-16, work_top+work_height-height))
            else:
                left = max(0, (960-width)//2)
                top = max(0, self.window.winfo_screenheight()-height-40)
        if not self.controls_on_right:
            self.controls_window.geometry(f"{width}x{height}{left:+d}{top:+d}")
        self.controls_placement.configure(text="播放控制：右侧 → 放回底部" if self.controls_on_right else "播放控制：底部 → 放到右侧")

    def toggle_controls_placement(self):
        self.controls_on_right = not self.controls_on_right
        self.layout_controls()

    def toggle_review(self):
        self.review_expanded = not self.review_expanded
        if self.review_expanded:
            self.review_history.pack(fill="x", after=self.review_toggle)
        else:
            self.review_history.pack_forget()
        self.review_toggle.configure(text="收起最近记录 ▴" if self.review_expanded else "展开最近记录 ▾")
        self.fit_review()

    def render_review(self, frame, loading=False):
        snapshot = self.review.snapshot(frame, self.command_index)
        self.review_mode.configure(text="模式同步中" if loading else snapshot.get("mode", "旧版按键记录"))
        latest = snapshot["recent"][-1] if snapshot["recent"] else None
        self.review_title.configure(text=("正在定位，等待游戏帧同步…" if loading else
                                         latest["title"] if latest else "截至当前尚无人工指令"))
        self.review_effect.configure(text=("定位完成后显示该时刻记录" if loading else
                                          latest["effect"] if latest else snapshot["notice"]))
        position = f"动作 {snapshot['count']} / {snapshot['total']} · 当前第 {frame} 帧"
        if latest:
            position += f"\n指令记录：第 {latest['frame']} 帧"
        self.action_position.configure(text=position)
        history = list(reversed(snapshot["recent"][:-1]))
        for index, label in enumerate(self.history_labels):
            item = history[index] if index < len(history) else None
            label.configure(text=(item["title"] + "\n" + item["effect"]) if item and not loading else "")
        self.review_stats.configure(text=snapshot["status"].replace("  |  ", "\n") if not loading else "状态同步中")
        for direction, button in ((-1, self.previous_command), (1, self.next_command)):
            available = self.review.neighbor_index(frame, direction, self.command_index) is not None
            button.configure(state="normal" if available and self.review_ready and not loading else "disabled")

    def jump_command(self, direction):
        # The native fast-forward can stop a few frames beyond the request.
        # Navigate from the requested command, so "previous" does not get stuck.
        index = self.review.neighbor_index(self.current, direction, self.command_index)
        if index is not None and self.review_ready:
            self.command_index = index
            frame = self.review.command_frames[index]
            self.pending_action_frame = frame
            self.review_anchor = frame
            self.paused = True
            self.review_ready = False
            self.session.request("seek", frame, True, self.speed_ms())
            self.render_review(self.current, loading=True)
            self.status.set(f"定位动作 {index+1}：第 {frame} 帧…")

    def begin_move(self, event):
        self.move_origin = (event.x_root, event.y_root,
                            self.controls_window.winfo_x(), self.controls_window.winfo_y())

    def move(self, event):
        if self.controls_on_right:
            return
        mouse_x, mouse_y, left, top = self.move_origin
        left += event.x_root - mouse_x
        top += event.y_root - mouse_y
        self.controls_window.geometry(f"{left:+d}{top:+d}")

    def speed_ms(self):
        return max(1, round(42 / float(self.speed.get().rstrip("×"))))

    def send_playback(self):
        self.session.request("playback", paused=self.paused, speed=self.speed_ms())

    def toggle_pause(self):
        self.review_anchor = None
        self.command_index = None
        self.pending_action_frame = None
        if self.ended:
            self.paused = False
            self.session.request("seek", 0, False, self.speed_ms())
            self.status.set("正在从头播放…")
            return
        self.paused = not self.paused
        self.play_button.configure(text="继续" if self.paused else "暂停")
        self.send_playback()

    def begin_drag(self, event):
        if not self.total or self.closed:
            return "break"
        self.dragging = True
        self.preview(event)

    def preview(self, event):
        if not self.dragging:
            return
        width = max(1, self.progress.winfo_width() - 18)
        frame = max(0, min(self.total, round((event.x - 9) / width * self.total)))
        self.progress.set(frame)
        self.status.set(f"{replay_time(frame)} / {replay_time(self.total)}")

    def end_drag(self, event):
        if self.dragging:
            self.preview(event)
            self.dragging = False
            self.seek()

    def keyboard_seek(self, _):
        if self.total:
            self.seek()

    def seek(self):
        self.review_anchor = None
        self.command_index = None
        self.pending_action_frame = None
        frame = max(0, min(self.total, round(self.progress.get())))
        self.session.request("seek", frame, self.paused, self.speed_ms())
        self.review_ready = False
        self.render_review(self.current, loading=True)
        self.status.set("正在定位到 " + replay_time(frame) + "…")

    def close(self):
        if self.closed:
            return
        self.session.close()
        self.close_button.configure(state="disabled")
        self.controls_placement.configure(state="disabled")
        self.play_button.configure(state="disabled")
        self.progress.configure(state="disabled")
        self.previous_command.configure(state="disabled")
        self.next_command.configure(state="disabled")
        self.status.set("正在关闭回放…")

    def poll(self):
        while not self.events.empty():
            state = self.events.get_nowait()
            phase = state["phase"]
            if phase in ("closed", "error"):
                self.closed = True
                self.controls_window.destroy()
                self.window.destroy()
                self.finished(state.get("message"))
                return
            if phase == "loading":
                self.docked_handle = None
                self.review_ready = False
                self.render_review(0, loading=True)
                self.play_button.configure(state="disabled")
                self.status.set("正在重新载入并定位…" if state["target"] else "正在载入原版游戏录像…")
                continue
            if self.session.stopping.is_set():
                continue
            self.total = state["total"]
            try:
                self.arrange_replay(state)
            except RuntimeError as error:
                self.session.close()
                self.status.set(str(error))
                self.events.put({"phase": "error", "message": str(error)})
                continue
            self.details.set(describe(state.get("labels", self.session.labels)))
            self.current = state["frame"]
            if self.review_anchor is not None and self.review_ready and abs(self.current-self.review_anchor) > 24:
                self.review_anchor = None
            self.review_ready = not state["seeking"] and not state.get("reload", False)
            if self.pending_action_frame is not None:
                self.review_ready = self.review_ready and self.current == self.pending_action_frame and state.get("actual_paused", state.get("paused", True))
                if self.review_ready:
                    self.pending_action_frame = None
            self.render_review(self.current, loading=not self.review_ready)
            self.ended = state["ended"]
            self.play_button.configure(state="normal" if self.pending_action_frame is None else "disabled")
            self.play_button.configure(text="重播" if self.ended else "继续" if self.paused else "暂停")
            self.progress.configure(state="normal", to=max(1, self.total))
            if not self.dragging:
                self.progress.set(state["target"] if state["seeking"] else self.current)
                prefix = "正在定位  " if state["seeking"] else "已播放完  " if state["ended"] else ""
                self.status.set(prefix + replay_time(self.current) + " / " + replay_time(self.total) + f" · 帧 {self.current}")
        self.window.after(100, self.poll)

"""Engine boundary for side-visible state, original key receipts and game termination."""

from abc import ABC, abstractmethod
from collections import deque
import json
from pathlib import Path
from threading import Event, Thread

from .model_client import write_json
from .primitives import is_valid_key
from .validator import EVENT_FIELDS, TRANSITIONS


KEYMAP = "toprow_commands_v17"
POLL_SECONDS = 0.15
DEFAULT_SPEED = 42
DEFAULT_MATCH_TIMEOUT = 1800
NATIVE_EXIT_GRACE_SECONDS = 20
TASK_FIELDS = ("frame", "task_id", "scope", "action", "phase", "goal", "arrived")


class EngineAdapter(ABC):
    @abstractmethod
    def run(self, on_observation, on_receipt, on_idle):
        """Deliver ordered callbacks sequentially until the game has ended and return its result."""

    @abstractmethod
    def send_key(self, key):
        """Post a native key and report if the dispatch was accepted. The receipt arrives later."""

    @abstractmethod
    def cutoff(self):
        """Request game termination and report if the request was sent."""


def _rows(reader):
    while True:
        offset = reader.tell()
        line = reader.readline()
        if not line.endswith(b"\n"):
            reader.seek(offset)
            return
        yield json.loads(line)


class _NativeTelemetry:
    def __init__(self, scout_snapshot, type_name, on_observation, on_receipt):
        self.scout_snapshot = scout_snapshot
        self.type_name = type_name
        self.on_observation = on_observation
        self.on_receipt = on_receipt
        self.started = False
        self.end = None
        self.units = None
        self.tasks = {}
        self.events = deque(maxlen=6)
        self.last_frame = None

    def consume(self, row):
        event = row["event"]
        if event == "start":
            if row.get("keymap") != KEYMAP or not row.get("human_layer") or not row.get("delegate_loaded"):
                raise RuntimeError("Native v17 commander layer is not active")
            self.started = True
        elif event == "validation_native":
            self.units = row
        elif event == "battle_state":
            self.tasks[row["task_id"]] = {key: row[key] for key in TASK_FIELDS}
        elif event in TRANSITIONS:
            self.events.append({key: row[key] for key in row.keys() & EVENT_FIELDS})
        elif event == "command" and self.started:
            if row.get("keymap") != KEYMAP:
                raise RuntimeError("Native command receipt uses a different key map")
            self.on_receipt(row)
        elif event == "state":
            self._observe(row)
        elif event == "end":
            self.end = row

    def _observe(self, state):
        frame = state["frame"]
        if (not self.started or state.get("keymap") != KEYMAP or self.units is None
                or self.units["frame"] != frame):
            raise RuntimeError(f"missing matching side-visible v17 telemetry at frame {frame}")
        units = [{**unit, "type_name": self.type_name(unit)} for unit in self.units["units"]]
        active_ids = {state["main_task"], state["squad_task"]}
        observation = {
            **state, **self.scout_snapshot(units, state), "units": units,
            "tasks": [task for task_id, task in self.tasks.items() if task_id in active_ids],
            "recent_events": list(self.events),
        }
        self.last_frame = frame
        self.on_observation(observation)


class NativeV17Adapter(EngineAdapter):
    """Wrap the existing local LAN match launcher and read only host telemetry."""

    def __init__(self, run_dir, map_name="Destination", opponent_name="PylonPuller", speed=DEFAULT_SPEED,
                 *, match_timeout=DEFAULT_MATCH_TIMEOUT, hold_lock=True):
        self.run_dir = Path(run_dir).resolve()
        self.output = self.run_dir / "bwapi-data/write"
        self.end_file = self.output / "match-ended.json"
        self.map_name = map_name
        self.opponent_name = opponent_name
        self.speed = speed
        self.match_timeout = match_timeout
        self.hold_lock = hold_lock
        self._stopping = Event()
        self._watcher = None
        self._watch_error = None
        self._cutoff_sent = False
        self._hwnd = None

    def send_key(self, key):
        if not is_valid_key(key):
            raise ValueError(f"invalid native key {key!r}")
        if self._cutoff_sent or self._stopping.is_set() or self.end_file.exists():
            return False
        from stage1 import game_window, post_key

        if self._hwnd is None:
            self._hwnd = game_window(self.output)
        post_key(self._hwnd, key)
        return True

    @property
    def _sentinel(self):
        return self.run_dir / "unused-test-input"

    def _external_cutoff_present(self):
        return (not self._cutoff_sent and self._sentinel.is_file()
                and self._sentinel.read_text(encoding="ascii").split()[1:] == ["-1"])

    def cutoff(self):
        if self._stopping.is_set() or self.end_file.exists() or self._external_cutoff_present():
            return False
        sentinel = self._sentinel
        staging = sentinel.with_suffix(".tmp")
        staging.write_text("1 -1\n", encoding="ascii")
        staging.replace(sentinel)
        self._cutoff_sent = True
        return True

    def _watch(self, telemetry, on_idle):
        try:
            if not self.end_file.exists():
                on_idle()
            log = self.output / "commander-layer.jsonl"
            while not log.exists():
                if self._stopping.wait(POLL_SECONDS):
                    return
                if not self.end_file.exists():
                    on_idle()
            with log.open("rb") as reader:
                while telemetry.end is None:
                    for row in _rows(reader):
                        telemetry.consume(row)
                        if telemetry.end is not None:
                            return
                    if not self.end_file.exists():
                        on_idle()
                    if self._stopping.wait(POLL_SECONDS):
                        for row in _rows(reader):
                            telemetry.consume(row)
                        return
        except Exception as error:
            self._watch_error = error
            try:
                self.cutoff()
            except OSError as cutoff_error:
                error.add_note(f"Native cutoff request failed: {cutoff_error}")

    def run(self, on_observation, on_receipt, on_idle):
        import matches
        from stage2_live import scout_snapshot, type_name

        telemetry = _NativeTelemetry(scout_snapshot, type_name, on_observation, on_receipt)

        def update(_):
            if self._watcher is None and (matches.read_json(self.run_dir / "match.json") or {}).get(
                    "state") == "playing":
                self._watcher = Thread(target=self._watch, args=(telemetry, on_idle), name="rsi-native-v17")
                self._watcher.start()

        try:
            result = matches.playing(
                self.run_dir, self.map_name, self.opponent_name, speed=self.speed, fullscreen=False,
                update=update, keep_results=False, render_profile="standard", data_only=True,
                match_timeout=self.match_timeout + NATIVE_EXIT_GRACE_SECONDS, visible_telemetry=True,
                hold_lock=self.hold_lock,
            )
        finally:
            self._stopping.set()
            if self._watcher is not None:
                self._watcher.join()
        if self._watch_error is not None:
            raise RuntimeError("Native observation loop failed") from self._watch_error
        native_end = matches.read_json(self.end_file)
        replay = result.get("replay")
        external_cutoff = self._external_cutoff_present()
        if (result.get("state") != "finished" or not native_end or telemetry.end is None
                or (telemetry.last_frame is None and not (self._cutoff_sent or external_cutoff))
                or native_end.get("frame") != telemetry.end["frame"]
                or result.get("frame") != telemetry.end["frame"]
                or native_end.get("winner") is not telemetry.end["winner"]
                or not replay or not (self.run_dir / replay).is_file()):
            raise RuntimeError("Native host end, terminal frame or replay could not be confirmed")
        result.update(speed_ms_per_frame=self.speed)
        if self._cutoff_sent or external_cutoff:
            result.update(state="cutoff", winner=None, natural_outcome="unknown", native_end=native_end)
            if external_cutoff:
                result["external_cutoff"] = True
            labels = (self.run_dir / replay).with_suffix(".rep.json")
            label_data = matches.read_json(labels) or {}
            write_json(labels, {**label_data, "natural_outcome": "unknown"})
        else:
            result["natural_outcome"] = "win" if native_end["winner"] else "loss"
        write_json(self.run_dir / "match.json", result)
        return result

"""Original player camera tracks, never inferred from army positions."""
from dataclasses import dataclass
from pathlib import Path
import json


@dataclass
class CameraTrack:
    player_id: int
    samples: list
    end_frame: int
    source: Path


def camera_path(replay):
    return Path(replay).with_suffix(".rep.camera.jsonl")


def read_camera(path):
    """Require a complete, ordered recording with a known original player."""
    path = Path(path)
    player_id = None
    end_frame = None
    samples = []
    try:
        with path.open(encoding="utf-8-sig") as stream:
            for line in stream:
                row = json.loads(line)
                event = row.get("event")
                if event == "camera_start":
                    if player_id is not None or samples or row.get("schema") != 1:
                        return None
                    player_id = row.get("player_id")
                elif event == "camera":
                    values = [row.get(k) for k in ("frame", "x", "y")]
                    if any(type(v) is not int or v < 0 for v in values) or end_frame is not None:
                        return None
                    if samples and values[0] < samples[-1][0]:
                        return None
                    if samples and values[0] == samples[-1][0]:
                        samples[-1] = values
                    else:
                        samples.append(values)
                elif event == "camera_end":
                    end_frame = row.get("frame")
    except (OSError, ValueError, AttributeError):
        return None
    if type(player_id) is not int or not 0 <= player_id < 12 or not samples or samples[0][0] != 0:
        return None
    if type(end_frame) is not int or end_frame < samples[-1][0]:
        return None
    return CameraTrack(player_id, samples, end_frame, path)


def load_camera(replay):
    return read_camera(camera_path(replay))

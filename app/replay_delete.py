"""Permanent, scoped deletion of a match and its managed replay sessions."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import stat


@dataclass(frozen=True)
class DeletePlan:
    folder: Path
    replay: Path | None
    directories: tuple[Path, ...]
    files: tuple[Path, ...]
    playback_count: int
    shared_legacy: bool


def _absolute(path):
    return Path(os.path.abspath(path))


def _checked(path, root):
    """Check lexical and resolved containment, rejecting junctions/symlinks."""
    path, root = _absolute(path), _absolute(root)
    if path == root or root not in path.parents:
        raise ValueError("删除目标不在指定的对局目录内。")
    if path.resolve() != path or root.resolve() != root:
        raise ValueError("目录包含链接或重定向，已停止删除。")
    for entry in (path, *path.parents):
        try:
            info = entry.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("目录包含链接或重定向，已停止删除。")
    return path


def _check_tree(folder, root):
    _checked(folder, root)
    for current, directories, files in os.walk(folder, followlinks=False):
        for name in directories + files:
            _checked(Path(current) / name, root)


def _read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _legacy_logs(folder, replays, replay):
    # Original multi-game runs use base.jsonl, base-2.jsonl, etc. Missing
    # entries after an earlier deletion retain their original ordering.
    files = []
    index = replays.index(replay)
    for base in ("astra-trace", "commander-state"):
        pattern = re.compile(re.escape(base) + r"(?:-(\d+))?\.jsonl$")
        numbered = []
        for path in (folder / "bwapi-data/write").glob(base + "*.jsonl"):
            match = pattern.fullmatch(path.name)
            if match:
                numbered.append((int(match[1] or 1), path))
        numbered.sort()
        if numbered and len(numbered) != len(replays):
            raise ValueError("旧对局日志与回放数量不一致，无法安全区分，请先检查该对局文件夹。")
        if numbered:
            files.append(numbered[index][1])
    return files


def plan_delete(folder, replay, runs, legacy_runs, playback):
    folder = _absolute(folder)
    runs, legacy_runs, playback = map(_absolute, (runs, legacy_runs, playback))
    legacy = folder.parent == legacy_runs and folder.name.endswith("-user")
    if folder.parent != runs and not legacy:
        raise ValueError("只能删除指挥台历史列表中的对局。")
    record_root = legacy_runs if legacy else runs
    folder = _checked(folder, record_root)
    if not folder.is_dir():
        raise ValueError("该对局已不存在，请刷新列表。")
    replay_root = folder / "maps/replays"
    replays = sorted(replay_root.glob("*.rep"))
    if replay is not None:
        replay = _checked(replay, folder)
        if replay.parent != replay_root or replay.suffix.lower() != ".rep":
            raise ValueError("回放路径不属于所选对局。")
        if replay not in replays:
            raise ValueError("回放已发生变化，请刷新后重试。")
    if len(replays) > 1 and (not legacy or replay is None):
        raise ValueError("该目录包含多份回放，无法按单局清理。")

    shared = legacy and len(replays) > 1
    directories, files = [], []
    if shared:
        files = [replay, replay.with_suffix(".rep.json"), *_legacy_logs(folder, replays, replay)]
        files = [path for path in files if path.exists()]
    else:
        directories.append(folder)

    cache_count = 0
    for cache in sorted(playback.glob("*")):
        if not cache.is_dir():
            continue
        _checked(cache, playback)
        metadata = _checked(cache / "replay.json", playback)
        source = _read_json(metadata).get("source")
        if not isinstance(source, str) or not source:
            continue
        source = _absolute(source)
        related = source == replay if shared else folder in source.parents
        if related:
            directories.append(cache)
            cache_count += 1

    # Validate every recursive target before allowing any mutation.
    for target in directories:
        _check_tree(target, playback if target.parent == playback else record_root)
    for target in files:
        _checked(target, folder)
    return DeletePlan(folder, replay, tuple(directories), tuple(files), cache_count, shared)


def delete_record(plan, runs, legacy_runs, playback):
    # Recheck after the confirmation dialog; never silently expand its scope.
    current = plan_delete(plan.folder, plan.replay, runs, legacy_runs, playback)
    if current != plan:
        raise ValueError("对局文件已发生变化，请刷新并重新确认删除。")
    # Remove copies/caches first, the source last. No recycle bin or backup.
    for target in reversed(plan.directories):
        shutil.rmtree(target)
    for target in plan.files:
        target.unlink()

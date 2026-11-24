"""Cross-version experiment ledger and live PROGRESS.md dashboard for RSI v2.

Separates L0-verified metrics (used for scoring/promotion) from self-reported MacroBot
astra-trace.jsonl diagnostics (review-only reference).
"""

from datetime import datetime, timezone
import json
from pathlib import Path

from . import scoring
from .model_client import write_json


LEDGER_FILE = "experiment-ledger.json"
PROGRESS_FILE = "PROGRESS.md"


def _read_json(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def extract_self_reported_build_diag(run_dir: Path) -> dict:
    """Parse MacroBot's self-reported astra-trace.jsonl defensively (NEVER used for scoring)."""
    trace = Path(run_dir) / "bwapi-data/write/astra-trace.jsonl"
    if not trace.is_file():
        return {"present": False, "malformed_lines": 0}
    malformed = 0
    core_plan = None
    forge_plan = None
    nexus2_plan = None
    first_cannon_done = None
    first_observer_done = None
    range_start = None
    cloak_seen = None
    cannon_skips = 0
    mid_minerals = []
    max_minerals = 0
    for line in trace.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if not isinstance(row, dict):
            malformed += 1
            continue
        ev = row.get("event")
        frame = row.get("frame")
        if ev == "build_plan":
            btype = row.get("type")
            if btype == "Protoss_Cybernetics_Core" and core_plan is None:
                core_plan = frame
            elif btype == "Protoss_Forge" and forge_plan is None:
                forge_plan = frame
            elif btype == "Protoss_Nexus" and nexus2_plan is None:
                nexus2_plan = frame
        elif ev == "unit_complete":
            utype = row.get("type")
            if utype == "Protoss_Photon_Cannon" and first_cannon_done is None:
                first_cannon_done = frame
            elif utype == "Protoss_Observer" and first_observer_done is None:
                first_observer_done = frame
        elif ev == "range_upgrade_started" and range_start is None:
            range_start = frame
        elif ev == "cloak_seen" and cloak_seen is None:
            cloak_seen = frame
        elif ev == "cannon_skip":
            cannon_skips += 1
        elif ev == "state" and type(frame) is int:
            mins = row.get("minerals")
            if type(mins) is int:
                max_minerals = max(max_minerals, mins)
                if 6000 <= frame <= 15000:
                    mid_minerals.append(mins)
    return {
        "present": True,
        "malformed_lines": malformed,
        "core_plan_frame": core_plan,
        "forge_plan_frame": forge_plan,
        "nexus2_plan_frame": nexus2_plan,
        "first_cannon_done_frame": first_cannon_done,
        "first_observer_done_frame": first_observer_done,
        "range_start_frame": range_start,
        "cloak_seen_frame": cloak_seen,
        "cannon_skips": cannon_skips,
        "avg_minerals_6k_15k": round(sum(mid_minerals) / len(mid_minerals)) if mid_minerals else None,
        "max_minerals_bank": max_minerals,
    }


def load_ledger(batch_dir: Path, core_opponents: list[str] | None = None) -> dict:
    batch_dir = Path(batch_dir)
    path = batch_dir / LEDGER_FILE
    data = _read_json(path)
    if data is not None:
        return data
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "core_opponents": list(core_opponents or []),
        "champion": "v1",
        "consecutive_rejects": 0,
        "versions": {
            "v1": {
                "id": "v1",
                "role": "champion",
                "parent": None,
                "status": "champion",
                "changed_files": [],
                "changed_functions": {},
                "build_config_changes": None,
                "cpp_changed_lines": 0,
                "summary": "Initial v2 seed (fixed proactive cannon priority + expansion cooldown)",
                "matches_by_opponent": {},
            }
        },
        "rounds": [],
    }


def save_ledger(batch_dir: Path, ledger: dict) -> None:
    batch_dir = Path(batch_dir)
    ledger["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json(batch_dir / LEDGER_FILE, ledger)
    progress = batch_dir / PROGRESS_FILE
    tmp = progress.with_suffix(".md.tmp")
    tmp.write_text(render_progress_md(ledger), encoding="utf-8", newline="\n")
    tmp.replace(progress)


def record_match_in_ledger(ledger: dict, version_id: str, match: dict, score_report: dict,
                           run_dir: Path) -> dict:
    """Append one match's L0 score + self-reported diagnostics under version_id."""
    entry = ledger["versions"].setdefault(version_id, {
        "id": version_id,
        "role": "champion" if version_id.startswith("v") else "challenger",
        "parent": None,
        "status": "evaluating",
        "changed_files": [],
        "changed_functions": {},
        "build_config_changes": None,
        "cpp_changed_lines": 0,
        "summary": "",
        "matches_by_opponent": {},
    })
    opp = match.get("opponent") or "unknown"
    rec = {
        "match_number": match.get("match_number"),
        "opponent": opp,
        "state": match.get("state"),
        "natural_outcome": match.get("natural_outcome"),
        "adjudicated_outcome": match.get("adjudicated_outcome"),
        "outcome": score_report["outcome"],
        "frame": match.get("frame"),
        "score": score_report["score"],
        "fps": score_report["fps"],
        "l0_milestones": score_report["l0_milestones"],
        "components": score_report["components"],
        "error": match.get("error"),
        "self_reported_build_diag": extract_self_reported_build_diag(run_dir),
    }
    entry["matches_by_opponent"].setdefault(opp, []).append(rec)
    return rec


def register_challenger(ledger: dict, challenger_id: str, parent_id: str, review_result: dict) -> None:
    ledger["versions"][challenger_id] = {
        "id": challenger_id,
        "role": "challenger",
        "parent": parent_id,
        "status": "pending_eval",
        "changed_files": sorted((review_result.get("diff") or {}).keys()),
        "changed_functions": review_result.get("changed_functions") or {},
        "build_config_changes": review_result.get("build_config_changes"),
        "cpp_changed_lines": review_result.get("cpp_changed_lines", 0),
        "distillation_type": review_result.get("distillation_type"),
        "diagnosis": (review_result.get("diagnosis") or "")[:400],
        "hypothesis": (review_result.get("hypothesis") or "")[:400],
        "expectation": (review_result.get("expectation") or "")[:400],
        "matches_by_opponent": {},
    }


def record_promotion(ledger: dict, challenger_id: str, new_champion_id: str, verdict: dict) -> None:
    chal = ledger["versions"][challenger_id]
    chal["status"] = f"promoted_as_{new_champion_id}"
    chal["verdict"] = verdict
    old_champ = ledger.get("champion")
    if old_champ and old_champ in ledger["versions"]:
        ledger["versions"][old_champ]["status"] = "superseded_champion"
    # New champion inherits the Challenger's evaluation records as its starting cumulative record.
    ledger["versions"][new_champion_id] = {
        **chal,
        "id": new_champion_id,
        "role": "champion",
        "promoted_from": challenger_id,
        "status": "champion",
        "matches_by_opponent": {
            opp: list(rows) for opp, rows in chal["matches_by_opponent"].items()
        },
    }
    ledger["champion"] = new_champion_id
    ledger["consecutive_rejects"] = 0


def record_rejection(ledger: dict, challenger_id: str, verdict: dict) -> None:
    chal = ledger["versions"][challenger_id]
    chal["status"] = "rejected"
    chal["verdict"] = verdict
    ledger["consecutive_rejects"] = ledger.get("consecutive_rejects", 0) + 1


def champion_records_by_opp(ledger: dict) -> dict[str, list[dict]]:
    champ = ledger["versions"].get(ledger["champion"], {})
    return champ.get("matches_by_opponent", {})


def compact_for_review(ledger: dict, *, max_versions: int = 12) -> dict:
    """Build a compact cross-version summary for the v2 agentic reviewer (eliminates amnesia)."""
    core = ledger.get("core_opponents", [])
    champ_id = ledger.get("champion", "v1")
    champ_entry = ledger["versions"].get(champ_id, {})
    champ_per_opp = {
        opp: scoring.aggregate_records(champ_entry.get("matches_by_opponent", {}).get(opp, []))
        for opp in core
    }

    history = []
    # Preserve chronological order from versions dict (last max_versions entries + always v1 and champion).
    all_ids = list(ledger["versions"].keys())
    keep_ids = []
    for vid in all_ids:
        if vid in ("v1", champ_id) or vid in all_ids[-max_versions:]:
            if vid not in keep_ids:
                keep_ids.append(vid)

    for vid in keep_ids:
        v = ledger["versions"][vid]
        per_opp = {}
        for opp, rows in v.get("matches_by_opponent", {}).items():
            agg = scoring.aggregate_records(rows)
            latest = rows[-1] if rows else {}
            l0m = latest.get("l0_milestones") or {}
            diag = latest.get("self_reported_build_diag") or {}
            per_opp[opp] = {
                "games": agg["games"],
                "wins": agg["wins"],
                "losses": agg["losses"],
                "mean_score": agg["mean_score"],
                "mean_fps": agg["mean_fps"],
                "l0_core_done": l0m.get("core_done_frame"),
                "l0_base2": l0m.get("second_base_frame"),
                "l0_peak_healthy": l0m.get("peak_healthy_army"),
                "l0_peak_types": l0m.get("peak_army_types"),
                "l0_exchange": l0m.get("exchange_ratio"),
                "l0_lock_frac": l0m.get("lock_defense_fraction"),
                "self_reported_core_plan": diag.get("core_plan_frame"),
                "self_reported_avg_min_6k_15k": diag.get("avg_minerals_6k_15k"),
            }
        history.append({
            "id": vid,
            "parent": v.get("parent"),
            "status": v.get("status"),
            "changed_files": v.get("changed_files"),
            "changed_functions": v.get("changed_functions"),
            "build_config_changes": v.get("build_config_changes"),
            "cpp_changed_lines": v.get("cpp_changed_lines", 0),
            "verdict_reason": (v.get("verdict") or {}).get("reason"),
            "hypothesis": v.get("hypothesis") or v.get("summary"),
            "per_opponent": per_opp,
        })

    return {
        "champion": champ_id,
        "core_opponents": core,
        "consecutive_rejects": ledger.get("consecutive_rejects", 0),
        "champion_cumulative_by_opponent": champ_per_opp,
        "version_history_ledger": history,
        "note": "Scoring and promotion use ONLY L0 metrics (wins/losses/score/fps/l0_*). "
                "self_reported_* fields come from MacroBot's own astra-trace.jsonl and are diagnostic only.",
    }


def render_progress_md(ledger: dict) -> str:
    """Render an executive morning-ready PROGRESS.md summary."""
    core = ledger.get("core_opponents", [])
    champ_id = ledger.get("champion", "v1")
    champ = ledger["versions"].get(champ_id, {})
    lines = [
        "# RSI v2 通宵自进化进度看板（PROGRESS）",
        "",
        f"- **更新时间 (UTC)**：`{ledger.get('updated_at')}`",
        f"- **当前 Champion**：`{champ_id}`（连续 Reject 次数：`{ledger.get('consecutive_rejects', 0)}`）",
        f"- **核心固定对手池（无 PurpleWave）**：`{', '.join(core)}`",
        f"- **已完成轮次**：`{len(ledger.get('rounds', []))}`",
        "",
        "## 1. 当前 Champion 累计成绩（纯 L0 指标）",
        "",
        "| 对手 | 累计局数 | 胜 / 负 / 平 | 胜率 | 平均 L0 分 | 平均 FPS |",
        "|---|---|---|---|---|---|",
    ]
    for opp in core:
        agg = scoring.aggregate_records(champ.get("matches_by_opponent", {}).get(opp, []))
        fps_str = f"{agg['mean_fps']:.1f}" if agg["mean_fps"] is not None else "—"
        lines.append(
            f"| {opp} | {agg['games']} | {agg['wins']} / {agg['losses']} / {agg['inconclusive']} | "
            f"{agg['win_rate']*100:.0f}% | {agg['mean_score']:.1f} | {fps_str} |"
        )

    lines.extend([
        "",
        "## 2. 版本进化谱系（含 Promoted 与 Rejected）",
        "",
        "| 版本 | 父版 | 状态 | 改动文件（C++行数） | 结论原因 | 各对手战绩与均分（L0） |",
        "|---|---|---|---|---|---|",
    ])
    for vid, v in ledger.get("versions", {}).items():
        if str(v.get("status", "")).startswith("promoted_as_"):
            # Already represented by its promoted v{N} row; keep a concise mention.
            pass
        files = ", ".join(v.get("changed_files") or ["—"])
        if v.get("cpp_changed_lines"):
            files += f" (Δ{v['cpp_changed_lines']}L)"
        reason = (v.get("verdict") or {}).get("reason") or "—"
        opp_bits = []
        for opp in core:
            rows = v.get("matches_by_opponent", {}).get(opp, [])
            if not rows:
                continue
            agg = scoring.aggregate_records(rows)
            if agg["games"] == 0:
                opp_bits.append(f"{opp}:ERR")
            else:
                inc = f"/{agg['inconclusive']}I" if agg["inconclusive"] else ""
                opp_bits.append(f"{opp}:{agg['wins']}W/{agg['losses']}L{inc} ({agg['mean_score']:.1f})")
        lines.append(
            f"| `{vid}` | `{v.get('parent') or '—'}` | `{v.get('status')}` | {files} | `{reason}` | "
            f"{'; '.join(opp_bits) or '—'} |"
        )

    lines.extend([
        "",
        "## 3. 逐轮记录",
        "",
        "| 轮次 | 测试对象 | 局号 | 判定 | 下一轮提案 |",
        "|---|---|---|---|---|",
    ])
    for r in ledger.get("rounds", []):
        lines.append(
            f"| R{r.get('round')} | `{r.get('evaluated')}` | {r.get('match_numbers')} | "
            f"`{r.get('adjudication_decision') or 'baseline'}` ({r.get('adjudication_reason') or '—'}) | "
            f"`{r.get('proposed_challenger') or '—'}` ({r.get('review_validation') or '—'}) |"
        )
    lines.append("")
    return "\n".join(lines)

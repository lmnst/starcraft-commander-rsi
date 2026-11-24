"""Strict L0-only match scoring, FPS guard, and Champion vs Challenger adjudication.

SECURITY INVARIANT: Every score, FPS check, and promotion/rejection decision reads ONLY
L0-owned data (match.json, observations.json derived from commander-layer.jsonl, L0 cutoff,
and ResultsWatcher). MacroBot's self-reported astra-trace.jsonl NEVER enters scoring.
"""

from math import dist
from statistics import fmean

from . import situation


TECHNICAL_STATES = frozenset({"model_circuit_open", "manual_stop"})
ACTIVE_SURVIVAL_CAP_FRAMES = 14400  # 10 game minutes: stalling beyond this adds 0 points.
MIN_SCORE_GAIN = 5.0
MAX_SINGLE_OPP_DROP = 12.0
MIN_FPS_RATIO = 0.70
ANTI_REGRESSION_MIN_GAMES = 2
ANTI_REGRESSION_MIN_WINRATE = 0.50


def match_outcome(match: dict) -> str:
    """Classify a match strictly from L0 match.json fields."""
    state = match.get("state")
    if state == "error":
        return "error"
    if state in TECHNICAL_STATES or match.get("degraded") is True:
        return "excluded"
    nat = match.get("natural_outcome")
    adj = match.get("adjudicated_outcome")
    if nat == "win" or adj == "cutoff_win":
        return "win"
    if nat == "loss" or adj == "cutoff_loss":
        return "loss"
    if adj == "inconclusive":
        return "inconclusive"
    return "error"


def is_challenger_crash(match: dict) -> bool:
    """True if a match ended due to DLL/config/native/stall crash (not a LAN pre-frame-0 flake)."""
    if match.get("state") != "error":
        return False
    error = str(match.get("error") or "")
    if match.get("native_error_files"):
        return True
    if any(tok in error for tok in ("MacroBot", "config_loaded", "config_error", "stall", "崩溃", "异常退出")):
        return True
    # A crash after entering gameplay (frame > 0) is a bot crash.
    frame = match.get("frame")
    return type(frame) is int and frame > 0


def is_pre_game_flake(match: dict) -> bool:
    """True if a match failed before frame 0 with no native crash files or config error."""
    if match.get("state") != "error":
        return False
    if is_challenger_crash(match):
        return False
    frame = match.get("frame")
    return frame is None or frame == 0


def compute_fps(match: dict) -> float | None:
    """Effective L0 frames per wall-clock second (None if match too short to measure)."""
    frame = match.get("frame")
    if type(frame) is not int or frame < 240:
        return None
    cutoff = match.get("cutoff") or {}
    seconds = cutoff.get("play_seconds")
    if type(seconds) not in (int, float) or seconds < 1.0:
        seconds = match.get("play_seconds")
    if type(seconds) not in (int, float) or seconds < 1.0:
        seconds = match.get("seconds")
    if type(seconds) not in (int, float) or seconds < 1.0:
        return None
    return round(frame / float(seconds), 3)


def _combat_exchange_metrics(observations: list[dict]) -> dict:
    """Estimate combat engagement share and visible attrition strictly from L0 observations."""
    own_engaged_sum = 0
    enemy_engaged_sum = 0
    combat_frames = 0
    forward_frames = 0
    post_opening_frames = 0

    prev_own_army: dict[int, tuple[int, int]] = {}
    prev_enemy_army: dict[int, tuple[int, int]] = {}
    own_lost = 0
    enemy_killed_est = 0

    for row in observations:
        frame = row.get("frame", 0)
        sit = row.get(situation.KEY) or {}
        army = sit.get("army") or {}
        oe = army.get("engaged") or 0
        ee = army.get("enemy_engaged") or 0
        if oe + ee > 0:
            combat_frames += 1
            own_engaged_sum += oe
            enemy_engaged_sum += ee
        if frame >= 4800:
            post_opening_frames += 1
            if (row.get("healthy_army") or 0) >= 4 and army.get("zone") in ("mid_map", "near_enemy"):
                forward_frames += 1

        units = row.get("units") or []
        own_combat: dict[int, tuple[int, int]] = {}
        own_vision: list[tuple[int, int, int]] = []
        enemy_combat: dict[int, tuple[int, int]] = {}
        for u in units:
            name = situation._name(u)
            pos = u.get("position")
            if not pos or len(pos) < 2:
                continue
            xy = (pos[0], pos[1])
            is_bldg = situation._is_building(u)
            if u.get("self"):
                own_vision.append((xy[0], xy[1], u.get("sight") or 224))
                if u.get("completed") is True and not is_bldg and name not in situation.WORKERS and name not in situation.PROJECTILES and name != "Observer":
                    own_combat[u["id"]] = xy
            else:
                if not is_bldg and name not in situation.WORKERS and name not in situation.PROJECTILES:
                    enemy_combat[u["id"]] = xy

        if prev_own_army:
            own_lost += sum(1 for uid in prev_own_army if uid not in own_combat)
        if prev_enemy_army and own_vision:
            for uid, epos in prev_enemy_army.items():
                if uid in enemy_combat:
                    continue
                # Count as observed kill only if our vision still covers the enemy's last seen spot.
                if any(dist((vx, vy), epos) <= max(64, sight - 32) for vx, vy, sight in own_vision):
                    enemy_killed_est += 1
        prev_own_army = own_combat
        prev_enemy_army = enemy_combat

    engagement_share = (
        own_engaged_sum / (own_engaged_sum + enemy_engaged_sum)
        if (own_engaged_sum + enemy_engaged_sum) > 0 else 0.0
    )
    exchange_ratio = (
        enemy_killed_est / (enemy_killed_est + own_lost)
        if (enemy_killed_est + own_lost) > 0 else 0.0
    )
    forward_share = forward_frames / post_opening_frames if post_opening_frames else 0.0
    return {
        "combat_frames": combat_frames,
        "own_engaged_sum": own_engaged_sum,
        "enemy_engaged_sum": enemy_engaged_sum,
        "engagement_share": round(engagement_share, 4),
        "own_lost_est": own_lost,
        "enemy_killed_est": enemy_killed_est,
        "exchange_ratio": round(exchange_ratio, 4),
        "forward_share": round(forward_share, 4),
    }


def score_match(match: dict, observations: list[dict], *, max_frames: int = 36000) -> dict:
    """Compute anti-turtling 0-100 L0 objective match score (zero bonus for inconclusive)."""
    obs = situation.attach(observations) if observations and situation.KEY not in observations[0] else observations
    outcome = match_outcome(match)
    fps = compute_fps(match)
    if not obs:
        return {
            "score": 0.0, "outcome": outcome, "fps": fps,
            "l0_milestones": {}, "components": {},
        }

    peak_healthy = max((row.get("healthy_army") or 0) for row in obs)
    mid_rows = [row.get("healthy_army") or 0 for row in obs if 4800 <= row.get("frame", 0) <= 16800]
    mid_mean_healthy = fmean(mid_rows) if mid_rows else 0.0

    # Active survival: last frame with meaningful economy AND combat force, strictly capped at 14400.
    active_frames = [row.get("frame", 0) for row in obs
                     if (row.get("workers") or 0) >= 6 and (row.get("army") or 0) >= 4]
    active_survival_frame = max(active_frames) if active_frames else 0

    # L0 tech & economy milestones from observations.json (NEVER astra-trace.jsonl).
    core_done_frame = None
    second_base_frame = None
    first_detector_frame = None
    max_dragoons = 0
    max_minerals = 0
    max_workers = 0
    peak_army_types = {}
    peak_army_count = -1
    for row in obs:
        frame = row.get("frame", 0)
        sit = row.get(situation.KEY) or {}
        own = sit.get("own") or {}
        econ = sit.get("economy") or {}
        tech = own.get("tech") or []
        if core_done_frame is None and "Cybernetics Core" in tech:
            core_done_frame = frame
        if second_base_frame is None and (own.get("bases") or 0) >= 2:
            second_base_frame = frame
        detectors = own.get("detectors") or {}
        if first_detector_frame is None and sum(detectors.values()) > 0:
            first_detector_frame = frame
        army_types = own.get("army_types") or {}
        max_dragoons = max(max_dragoons, army_types.get("Dragoon", 0))
        if (row.get("army") or 0) > peak_army_count:
            peak_army_count = row.get("army") or 0
            peak_army_types = dict(army_types)
        max_minerals = max(max_minerals, econ.get("gathered_minerals") or row.get("gathered_minerals") or 0)
        max_workers = max(max_workers, econ.get("completed_workers") or row.get("completed_workers") or 0)

    combat = _combat_exchange_metrics(obs)

    # 1. L0 Army & Combat Exchange (max 55 pts)
    peak_army_pts = 15.0 * min(peak_healthy, 35) / 35.0
    mid_army_pts = 10.0 * min(mid_mean_healthy, 20.0) / 20.0
    engage_weight = min(combat["combat_frames"], 12) / 12.0
    engage_pts = 10.0 * combat["engagement_share"] * engage_weight
    exchange_weight = min(combat["enemy_killed_est"] + combat["own_lost_est"], 10) / 10.0
    exchange_pts = 10.0 * combat["exchange_ratio"] * exchange_weight
    forward_pts = 10.0 * min(combat["forward_share"], 0.50) / 0.50

    # 2. L0 Tech & Economy Progression (max 30 pts)
    tech_pts = 0.0
    if core_done_frame is not None:
        tech_pts += 3.0 + (2.0 if core_done_frame <= 6600 else 0.0)
    if max_dragoons >= 2:
        tech_pts += 3.0
    if first_detector_frame is not None:
        tech_pts += 3.0
    if second_base_frame is not None:
        tech_pts += 4.0
    econ_pts = 10.0 * min(max_minerals, 8000) / 8000.0 + 5.0 * min(max_workers, 32) / 32.0

    # 3. Capped Active Survival (max 15 pts; inconclusive bonus is explicitly 0)
    cap = min(ACTIVE_SURVIVAL_CAP_FRAMES, max_frames)
    survival_pts = 15.0 * min(active_survival_frame, cap) / float(cap) if cap > 0 else 0.0

    # 4. Anti-turtling defense-lock penalty
    lock_fraction = (match.get("lock_defense") or {}).get("fraction") or 0.0
    turtle_penalty = 20.0 * float(lock_fraction)

    total = max(0.0, min(100.0, (
        peak_army_pts + mid_army_pts + engage_pts + exchange_pts + forward_pts
        + tech_pts + econ_pts + survival_pts - turtle_penalty
    )))
    return {
        "score": round(total, 2),
        "outcome": outcome,
        "fps": fps,
        "l0_milestones": {
            "core_done_frame": core_done_frame,
            "second_base_frame": second_base_frame,
            "first_detector_frame": first_detector_frame,
            "peak_healthy_army": peak_healthy,
            "peak_army_types": peak_army_types,
            "max_dragoons": max_dragoons,
            "max_gathered_minerals": max_minerals,
            "max_completed_workers": max_workers,
            "active_survival_frame": active_survival_frame,
            "lock_defense_fraction": round(float(lock_fraction), 3),
            **combat,
        },
        "components": {
            "peak_army": round(peak_army_pts, 2),
            "mid_army": round(mid_army_pts, 2),
            "engagement": round(engage_pts, 2),
            "exchange": round(exchange_pts, 2),
            "forward_presence": round(forward_pts, 2),
            "tech": round(tech_pts, 2),
            "economy": round(econ_pts, 2),
            "survival_capped": round(survival_pts, 2),
            "inconclusive_bonus": 0.0,
            "turtle_penalty": round(-turtle_penalty, 2),
        },
    }


def aggregate_records(records: list[dict]) -> dict:
    """Compute cumulative average stats over valid scored matches for one version on one opponent."""
    valid = [r for r in records if r.get("outcome") in ("win", "loss", "inconclusive")]
    n = len(valid)
    wins = sum(r["outcome"] == "win" for r in valid)
    losses = sum(r["outcome"] == "loss" for r in valid)
    inconclusive = sum(r["outcome"] == "inconclusive" for r in valid)
    fps_vals = [r["fps"] for r in valid if type(r.get("fps")) in (int, float)]
    scores = [r["score"] for r in valid if type(r.get("score")) in (int, float)]
    return {
        "games": n,
        "wins": wins,
        "losses": losses,
        "inconclusive": inconclusive,
        "win_rate": round(wins / n, 4) if n else 0.0,
        "loss_rate": round(losses / n, 4) if n else 0.0,
        "mean_score": round(fmean(scores), 2) if scores else 0.0,
        "mean_fps": round(fmean(fps_vals), 3) if fps_vals else None,
    }


def adjudicate_challenger(
    champion_records_by_opp: dict[str, list[dict]],
    challenger_records_by_opp: dict[str, list[dict]],
    core_opponents: list[str],
    *,
    min_score_gain: float = MIN_SCORE_GAIN,
    max_single_drop: float = MAX_SINGLE_OPP_DROP,
    min_fps_ratio: float = MIN_FPS_RATIO,
) -> dict:
    """Decide whether Challenger beats Champion across core_opponents using strictly L0 metrics."""
    # 1. Crash / error fail-fast check.
    all_chal = [r for opp in core_opponents for r in challenger_records_by_opp.get(opp, [])]
    if any(r.get("outcome") == "error" for r in all_chal):
        return {"decision": "rejected", "reason": "crashed_reject", "details": {"error_matches": True}}

    champ_stats = {opp: aggregate_records(champion_records_by_opp.get(opp, [])) for opp in core_opponents}
    chal_stats = {opp: aggregate_records(challenger_records_by_opp.get(opp, [])) for opp in core_opponents}

    scored_opps = [opp for opp in core_opponents if chal_stats[opp]["games"] > 0]
    if len(scored_opps) < len(core_opponents):
        return {
            "decision": "rejected",
            "reason": "incomplete_evaluation",
            "details": {"scored_opponents": scored_opps, "expected": list(core_opponents)},
        }

    # 2. FPS regression gate: Challenger FPS < 70% of Champion FPS -> immediate reject.
    champ_fps_vals = [champ_stats[o]["mean_fps"] for o in core_opponents if champ_stats[o]["mean_fps"] is not None]
    chal_fps_vals = [chal_stats[o]["mean_fps"] for o in core_opponents if chal_stats[o]["mean_fps"] is not None]
    champ_fps = fmean(champ_fps_vals) if champ_fps_vals else None
    chal_fps = fmean(chal_fps_vals) if chal_fps_vals else None
    if champ_fps is not None and champ_fps > 5.0 and chal_fps is not None and chal_fps < champ_fps * min_fps_ratio:
        return {
            "decision": "rejected",
            "reason": "fps_regression_reject",
            "details": {
                "champion_fps": round(champ_fps, 2),
                "challenger_fps": round(chal_fps, 2),
                "ratio": round(chal_fps / champ_fps, 3),
                "min_ratio": min_fps_ratio,
            },
        }

    # Aggregate equal-weighted across opponents where Champion has >=1 valid scored game.
    compared_opps = [o for o in core_opponents if champ_stats[o]["games"] > 0]
    if not compared_opps:
        return {
            "decision": "rejected",
            "reason": "champion_baseline_incomplete",
            "details": {"core_opponents": list(core_opponents), "champion": champ_stats, "challenger": chal_stats},
        }
    champ_wr = fmean(champ_stats[o]["win_rate"] for o in compared_opps)
    chal_wr = fmean(chal_stats[o]["win_rate"] for o in compared_opps)
    champ_lr = fmean(champ_stats[o]["loss_rate"] for o in compared_opps)
    chal_lr = fmean(chal_stats[o]["loss_rate"] for o in compared_opps)
    champ_score = fmean(champ_stats[o]["mean_score"] for o in compared_opps)
    chal_score = fmean(chal_stats[o]["mean_score"] for o in compared_opps)

    summary = {
        "compared_opponents": compared_opps,
        "champion_win_rate": round(champ_wr, 4),
        "challenger_win_rate": round(chal_wr, 4),
        "champion_loss_rate": round(champ_lr, 4),
        "challenger_loss_rate": round(chal_lr, 4),
        "champion_mean_score": round(champ_score, 2),
        "challenger_mean_score": round(chal_score, 2),
        "score_delta": round(chal_score - champ_score, 2),
        "champion_fps": round(champ_fps, 2) if champ_fps is not None else None,
        "challenger_fps": round(chal_fps, 2) if chal_fps is not None else None,
        "per_opponent": {
            opp: {"champion": champ_stats[opp], "challenger": chal_stats[opp]}
            for opp in core_opponents
        },
    }

    # 3. Anti-regression guard: only active if Champion has >=2 games AND >=50% win rate on that opp.
    for opp in compared_opps:
        cs = champ_stats[opp]
        if cs["games"] >= ANTI_REGRESSION_MIN_GAMES and cs["win_rate"] >= ANTI_REGRESSION_MIN_WINRATE:
            if chal_stats[opp]["win_rate"] == 0.0 and chal_wr <= champ_wr + 1e-6:
                return {"decision": "rejected", "reason": f"regression_on_{opp}", "details": summary}

    # 4. Primary criterion: strict win rate only (do NOT reward turning a loss into a turtling inconclusive!).
    if chal_wr > champ_wr + 1e-6:
        return {"decision": "promoted", "reason": "win_rate_improved", "details": summary}
    if chal_wr < champ_wr - 1e-6:
        return {"decision": "rejected", "reason": "win_rate_lower", "details": summary}

    # 5. Tie-breaker when win rates are equal: anti-turtling L0 match_score gain with single-opp cap.
    drops = {opp: round(champ_stats[opp]["mean_score"] - chal_stats[opp]["mean_score"], 2)
             for opp in compared_opps}
    max_drop = max(drops.values(), default=0.0)
    summary["max_single_opp_drop"] = max_drop
    if (chal_score - champ_score) >= min_score_gain and max_drop <= max_single_drop:
        return {"decision": "promoted", "reason": "score_improved", "details": summary}
    if max_drop > max_single_drop:
        return {"decision": "rejected", "reason": "single_opponent_score_drop", "details": summary}
    return {"decision": "rejected", "reason": "insufficient_improvement", "details": summary}

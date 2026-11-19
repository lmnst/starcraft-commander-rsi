"""Frame-indexed, read-only review of the original match's commander logs."""
from bisect import bisect_left, bisect_right
import json
import math
from pathlib import Path

NAMES = {"1": "侦察", "2": "小队骚扰", "3": "攻分矿", "4": "就地固守",
         "5": "攻主基地", "6": "保卫基地", "7": "就地集结", "8": "脱战后撤"}
STANCES = {"attack": "进攻", "expansion": "攻分矿", "hold": "固守", "defend": "回防",
           "rally": "集结", "retreat": "撤退", "clear_enemies": "清理敌军", "baseline": "Bot自动控制"}
TASKS = {"idle": "空闲", "active": "执行中", "returning": "返程", "waiting_units": "等待可用单位",
         "builder_released": "让出建造工人", "lost": "单位损失", "blocked": "路线受阻",
         "recalled": "已召回", "not_enough_army": "兵力不足", "not_enough_healthy_units": "健康兵力不足"}
TASKS.update(evading="脱离危险", diverting="转向安全区域探路", home_safe="已回家采矿，拦截原侦察路线")
MODE_NAMES = {"squad": "小队", "all": "全队"}
MODE_KEYS = {"1": "侦察避险", "2": "小队模式", "3": "全队模式", "4": "进攻分矿",
             "5": "进攻主基地", "6": "安全集结", "7": "安全撤退", "8": "回防基地", "9": "清理敌军", "0": "全部恢复自动作战"}
V11_KEYS = {"1":"探机避险", "2":"强制采矿", "3":"小队模式", "4":"全队模式",
            "5":"近敌／分矿建筑", "6":"主建筑", "7":"兵和炮台", "8":"安全集结",
            "9":"全军回防", "0":"全局重置"}
V12_KEYS = dict(V11_KEYS, **{"6":"进攻主基地", "7":"持续区域警戒"})
V14_KEYS = dict(V12_KEYS, **{"8":"原地集结"})
V16_KEYS = dict(V14_KEYS, **{"6":"主基地区域清场"})
V17_KEYS = dict(V16_KEYS, **{"9":"安全撤离集结", "0":"全军回防", "-":"全局重置"})

def reset_key(row):
    return "-" if row.get("keymap")=="toprow_commands_v17" else "0"

def tenkeys(row):
    return row.get("keymap") in ("tenkeys_economy_v11", "tenkeys_combat_v12", "tenkeys_detection_v13", "tenkeys_assembly_v14", "tenkeys_local_combat_v15", "tenkeys_main_area_v16", "toprow_commands_v17")

BATTLE_PHASES = {"patrol_waiting":"持续区域警戒，敌军再现自动接战", "patrol_searching":"敌军丢失视野，短暂局部搜索", "patrol_engaging":"警戒区接战，忽略探机", "active": "执行中", "advancing": "正在推进", "engaging": "正在交战",
                 "assembly_waiting":"原地等待援兵，集结后继续待命", "assembly_joining":"后方援兵前来汇合",
                 "assembly_defending":"集结区就近还击，不主动追击", "assembly_evading":"集结区局部避险，保留原集结点",
                 "evading_cloak":"未侦测隐刀，局部避让并保留原任务", "waiting_detection":"等待反隐支援，到位后自动继续原任务",
                 "detection_skirmishing":"等待反隐期间，优先处理可安全攻击的敌军", "assembly_shared_refuge":"集结区受压，全队前往同一局部避险点",
                 "main_outer_pylon":"清主基地：拆外围水晶", "main_outer_building":"清主基地：拆外围建筑", "main_core":"清主基地：拆主建筑",
                 "main_combat":"清主基地：优先处理敌军和炮台", "main_surveying":"清主基地：扫查区域迷雾", "main_confirming":"清主基地：确认区域已清空",
                 "main_blocked":"清主基地：已知目标暂不可达，任务保留", "main_advancing":"清主基地：从外围逐步推进",
                 "standby_forming": "清理后向空地分散展开", "standby_waiting": "空地待命，等待下一条指令",
                 "standby_engaging": "待命区附近短距离迎敌", "standby_returning": "停止追击，返回各自站位",
                 "standby_blocked": "安全空位不足，等待展开",
                 "gathering": "正在向集合点汇合", "holding": "已集结待命",
                 "retreating": "正在脱离危险", "regrouping": "已到避险点，正在汇合",
                 "safe": "已撤到安全位置", "returning": "正在回防",
                 "guarding": "已到基地防守", "defending": "正在基地附近交战",
                 "intercepting": "已到防区，正在短距离拦截", "reforming": "停止追击，正在收回防线",
                 "rally_withdrawing": "前线脱战，援兵向安全点汇合", "rally_joining": "正在前往共同汇合点",
                 "rally_waiting": "安全点待命，等待援兵和下一条指令",
                 "waiting_units": "等待作战单位", "waiting_reinforcements": "等待自动补兵，先保留回防兵力",
                 "lost": "小队已损失，不自动补兵"}


def stamp(frame):
    seconds = max(0, int(frame)) * 42 // 1000
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def read_rows(path):
    rows, damaged = [], 0
    try:
        with path.open(encoding="utf-8-sig") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict) or type(row.get("frame")) is not int or row["frame"] < 0:
                        continue
                    rows.append(row)
                except ValueError:
                    damaged += 1
    except (OSError, UnicodeError):
        return [], 1
    return sorted(rows, key=lambda row: row["frame"]), damaged


class FrameRows:
    def __init__(self, rows):
        self.rows = rows
        self.frames = [row["frame"] for row in rows]

    def latest(self, frame):
        index = bisect_right(self.frames, frame) - 1
        return self.rows[index] if index >= 0 else None

    def between(self, start, end):
        return self.rows[bisect_left(self.frames, start):bisect_right(self.frames, end)]


class ReplayReview:
    def __init__(self, replay):
        self.commands = []
        self.command_frames = []
        self.states = FrameRows([])
        self.events = FrameRows([])
        self.economy = FrameRows([])
        self.mode_controls = False
        self.dynamic_squad = False
        self.notice = "没有本局指挥日志；仅凭 .rep 无法还原人工按键。"
        replay = Path(replay).resolve()
        # Only bind the canonical replay recorded in a modern single-match run.
        # Shared old run logs may have been overwritten by another game.
        folder = next((p for p in replay.parents if (p / "match.json").is_file()), None)
        if folder is None:
            return
        try:
            match = json.loads((folder / "match.json").read_text(encoding="utf-8-sig"))
            if (folder / match["replay"]).resolve() != replay:
                self.notice = "录像与本局记录不匹配，未关联指挥日志。"
                return
        except (OSError, ValueError, KeyError, TypeError):
            return
        log_dir = folder / "bwapi-data/write"
        path = log_dir / "commander-layer.jsonl"
        if not path.is_file():
            return
        rows, damaged = read_rows(path)
        self.dynamic_squad = any(r.get("squad_policy") == "dynamic_quarter_v1" for r in rows)
        self.mode_controls = any(r.get("schema", 0) >= 3 for r in rows)
        self.commands = [r for r in rows if r.get("event") == "command"]
        self.command_frames = [r["frame"] for r in self.commands]
        self.states = FrameRows([r for r in rows if r.get("event") == "state"])
        self.events = FrameRows([r for r in rows if r.get("event") not in ("state", "command")])
        economy_path = log_dir / "astra-trace.jsonl"
        economy, econ_damaged = read_rows(economy_path) if economy_path.exists() else ([], 0)
        self.economy = FrameRows([r for r in economy if r.get("event") == "state"])
        self.notice = "已关联本局日志；效果为已观察到的变化，不等于战术成功。"
        if match.get("control") == "baseline":
            self.notice = "本局为基线自动对战，没有人工指令。"
        if damaged or econ_damaged:
            self.notice += " 部分日志损坏，已跳过。"

    def neighbor(self, frame, direction):
        index = (bisect_left(self.command_frames, frame) - 1 if direction < 0
                 else bisect_right(self.command_frames, frame))
        return self.command_frames[index] if 0 <= index < len(self.commands) else None

    def neighbor_index(self, frame, direction, selected=None):
        if selected is not None:
            index = selected + direction
        else:
            index = (bisect_left(self.command_frames, frame)-1 if direction < 0
                     else bisect_right(self.command_frames, frame))
        return index if 0 <= index < len(self.commands) else None

    def describe_command(self, index, frame, limit=None):
        command = self.commands[index]
        if command.get("schema", 0) >= 3 and command.get("action") != "scout_evade":
            return self.describe_mode_command(index, frame, limit)
        start = command["frame"]
        future_commands = self.commands[index+1:limit]
        end = min(frame, future_commands[0]["frame"]-1) if future_commands else frame
        if command.get("schema", 0) >= 3:
            # Probe protection is independent of all combat-mode/action keys.
            end = next((min(frame, c["frame"]-1) for c in future_commands
                        if (c.get("action") == "scout_evade" and c.get("status") == "scout_evade_applied") or c.get("action")=="restore_auto" or
                        (c.get("action")=="mine_lock" and c.get("status")=="mining_locked")), frame)
        states = self.states.between(start, end)
        state = states[-1] if states else {}
        events = self.events.between(start, end)
        raw = str(command.get("key", "?"))
        key = raw[1:] if raw.startswith("F") else raw
        name = NAMES.get(key, command.get("stance", "未知指令"))
        evade = key == "1" and command.get("action") == "scout_evade"
        if evade:
            name = "探机避险" if tenkeys(command) else "侦察避险"
        result = command.get("status", "")
        receipt = {"task_confirmed":"原任务继续，保留交战状态", "applied": "已接收", "harass_already_active": "已有骚扰任务，未增派",
                   "not_enough_army": "未派出：兵力不足",
                   "not_enough_healthy_units": "未派出：健康兵力不足",
                   "expansion_unknown_scouting": "等待：未发现敌方分矿",
                   "attacking_known_expansion": "已接收",
                   "scout_evade_applied": "已接收：仅接管侦察探机",
                   "no_scout_probe": "未执行：没有可接管的侦察探机"}.get(result, "接收结果未记录")
        if evade and tenkeys(command):
            receipt="已接收：接管探机避险" if result=="scout_evade_applied" else "未执行：没有可接管的避险探机"
        effect = "等待后续状态采样"
        if evade:
            if result == "no_scout_probe":
                effect = "未派出新单位，主力指令不变"
            else:
                probe = command.get("probe_id")
                changes = [e for e in events if e.get("probe_id") == probe and e.get("event", "").startswith("scout_evade_")]
                latest = changes[-1] if changes else {}
                phase = latest.get("phase", command.get("evade"))
                effect = f"探机 #{probe}：" + TASKS.get(phase, "等待执行记录")
                if latest.get("overrides"):
                    effect += f"；已拦截冲突指令 {latest['overrides']} 次（本局累计）"
                if any(e.get("accepted") is False for e in changes):
                    effect += "；存在下令失败，需检查回放"
                if any(e.get("event") == "scout_evade_lost" and e.get("reason") == str(probe) for e in events):
                    effect += "；探机已损失"
        elif key in ("1", "2"):
            field = "scout" if key == "1" else "raid"
            task = state.get(field)
            effect = ("侦察：" if key == "1" else "骚扰：") + TASKS.get(task, "暂无任务状态")
            if key == "2" and "raid_units" in state:
                effect += f"（{state['raid_units']}名）"
            launches = [e for e in events if e.get("event") == ("scout_start" if key == "1" else "harass_start")]
            if launches:
                effect += "；已记录派出"
            effect += "；收益未统计"
        elif any(e.get("event") == "retreat_complete" for e in events) and key == "8":
            effect = "已记录撤退完成，转为集结；战损未统计"
        elif states:
            effect = "姿态：" + STANCES.get(state.get("stance"), "未记录")
            origin, goal, center = (command.get(k) for k in ("origin", "goal", "center"))
            center = state.get("center", center)
            if all(self.valid_point(p) for p in (origin, goal, center)):
                before, after = math.dist(origin, goal), math.dist(center, goal)
                if after < 192:
                    effect += "；主力中心接近指令目标"
                elif before - after > 64:
                    effect += f"；距原目标缩短 {round(before-after)} 像素"
                elif after - before > 64:
                    effect += "；距原目标变远（需结合路线判断）"
                else:
                    effect += "；距原目标变化较小"
        if result in ("not_enough_army", "not_enough_healthy_units", "harass_already_active"):
            effect = "本次未新派骚扰小队"
        if key == "3" and self.valid_point(command.get("origin")) and self.valid_point(command.get("goal")) and math.dist(command["origin"], command["goal"]) < 32:
            effect = "下令时未发现分矿，先侦察、主力集结；收益未统计"
        if end < frame:
            if effect == "等待后续状态采样":
                effect = "切换前没有状态采样"
            effect += "；后续已下新指令"
            if any(c.get("action")=="restore_auto" and c["frame"]==end+1 for c in future_commands):
                effect += f"；已由{reset_key(command)}全部重置，探机交回Bot"
            if evade and any(c.get("action")=="mine_lock" and c.get("status")=="mining_locked" and c["frame"]==end+1 for c in future_commands):
                effect += "；已由2切回强制采矿"
        return {"frame": start, "title": f"{stamp(start)}  {raw} · {name}  |  {receipt}", "effect": effect}

    def describe_mode_command(self, index, frame, limit=None):
        command = self.commands[index]
        start, key = command["frame"], str(command.get("key", "?"))
        scope = MODE_NAMES.get(command.get("mode"), "未知范围")
        v11 = tenkeys(command)
        name = (V17_KEYS if command.get("keymap")=="toprow_commands_v17" else V16_KEYS if command.get("keymap")=="tenkeys_main_area_v16" else V14_KEYS if command.get("keymap") in ("tenkeys_assembly_v14","tenkeys_local_combat_v15") else V12_KEYS if command.get("keymap") in ("tenkeys_combat_v12", "tenkeys_detection_v13") else V11_KEYS if v11 else MODE_KEYS).get(key, "未知动作")
        if key=="8" and command.get("keymap") in ("tenkeys_assembly_v14","tenkeys_local_combat_v15","tenkeys_main_area_v16") and command.get("modifier")=="alt":
            key="Alt+8";name="安全撤离集结"
        if not v11 and key=="7" and command.get("rendezvous_policy")=="shared_safe_point_v1":name="撤退等援兵"
        if not v11 and key=="8" and command.get("global_defence"):name="全军回防"
        result = command.get("status")
        if command.get("action") == "mine_lock":
            end = next((c["frame"]-1 for c in self.commands[index+1:limit]
                        if c["frame"]<=frame and c.get("action")=="restore_auto"), frame)
            blocked = sum(e.get("event")=="mining_blocked" for e in self.events.between(start,end))
            effect = ("持续接管现有和新造探机的采矿；拦截攻击、追敌和旧侦察指令，保留建造、采气和交资源"
                      if result=="mining_locked" else "未能开启采矿接管，请核对记录")
            if blocked:effect+=f"；已拦截干扰指令 {blocked} 次"
            if end<frame:effect+=f"；已由{reset_key(command)}解除"
            return {"frame":start,"title":f"{stamp(start)}  2 · 强制采矿  |  "+("已开启" if result=="mining_locked" else "未执行"),"effect":effect}
        if command.get("action")=="restore_auto":
            end=next((min(frame,c["frame"]-1) for c in self.commands[index+1:limit] if c["frame"]<=frame),frame)
            events=self.events.between(start,end)
            releases=[e for e in events if e.get("event")=="manual_release" and e["frame"]==start]
            observed={e.get("unit_id") for e in events if e.get("event")=="auto_control" and e.get("released_at")==start and e.get("baseline_untouched")}
            effect="全部人工任务已取消，作战部队和探机交回Bot；已回到全队模式，按1～9"+("、0" if command.get("keymap")=="toprow_commands_v17" else "")+"可重新接管"
            if v11:effect+="；强制采矿锁已解除"
            if observed:effect+=f"；已观察到Bot重新下令 {len(observed)} 个单位"
            if any(not e.get("accepted") or not e.get("verified") for e in releases):effect+="；部分旧命令取消失败，需核对"
            if end<frame:effect+="；后续已重新按键"
            return {"frame":start,"title":f"{stamp(start)}  {key} · "+("全局重置" if v11 else "全部恢复自动作战")+"  |  已重置","effect":effect}
        if command.get("action") == "select_mode":
            return {"frame": start, "title": f"{stamp(start)}  {key} · {name}  |  已切换",
                    "effect": f"后续动作控制{scope}；已有任务继续，切换模式本身不派兵"}
        if command.get("action") == "split_squad":
            title = f"{stamp(start)}  {key} · 小队模式"
            if result == "mode_unchanged":
                if self.dynamic_squad:
                    return {"frame":start, "title":title+"  |  已在小队模式", "effect":"原任务继续；自动补充到健康兵力约四分之一，至少留6名健康兵回防，无需重复按键"}
                return {"frame":start, "title":title+"  |  已在小队模式", "effect":"保持原小队名单与任务；没有再次抽兵或改派主力"}
            if result != "squad_split":
                reason = "原小队已损失" if result == "squad_lost" else "健康兵力不足"
                return {"frame":start, "title":title+"  |  切换未执行："+reason, "effect":"原模式和原任务保持，未建立新小队"}
            end = frame
            for future in self.commands[index+1:limit]:
                if future["frame"]>frame:break
                if future.get("action")=="restore_auto":
                    end=future["frame"]-1
                    break
                if future.get("mode")=="all" and future.get("task_id") and future.get("action")!="split_squad":
                    end=future["frame"]-1
                    break
                if future.get("reserve_task"):
                    end=future["frame"]-1
                    break
            reserve_task=command.get("reserve_task")
            reserve_events=[e for e in self.events.between(start,end) if e.get("task_id")==reserve_task]
            reserve_states=[e for e in reserve_events if e.get("event")=="battle_state"]
            effect=f"固定小队 {len(command.get('units', []))} 名，继续原任务；其余 {len(command.get('reserve_units', []))} 名回基地防守"
            if self.dynamic_squad:
                effect=f"初始小队 {len(command.get('units', []))} 名，继续原任务；其余 {len(command.get('reserve_units', []))} 名回基地防守；后续按健康兵力约四分之一自动增援及补损，至少留6名健康兵回防"
            if reserve_states:
                effect+="；回防部队："+BATTLE_PHASES.get(reserve_states[-1].get("phase"),"执行中")
                if reserve_states[-1].get("flight_policy")=="run_first":effect+="；途中撤离优先，不追敌"
                if reserve_states[-1].get("defence_policy")=="perimeter_bounded_v1":effect+="；外围保护建筑和工人，限距拦截，超限收回"
            conflicts=sum(e.get("conflict") is True for e in reserve_events)
            if conflicts:effect+=f"；已拦截冲突指令 {conflicts} 次"
            if any(e.get("accepted") is False or e.get("verified") is False for e in reserve_events):effect+="；存在下令失败，需检查回放"
            if end<frame and any(c.get("action")=="restore_auto" and c["frame"]==end+1 for c in self.commands[index+1:limit]):effect+=f"；已由{reset_key(command)}全部重置，回防任务取消"
            return {"frame":start, "title":title+"  |  已分队，其余部队回防", "effect":effect}
        receipt = {"patrol_reanchored":"警戒位置已更新，保留有效集火", "task_confirmed":"原任务继续，保留交战状态", "applied": "已接收", "waiting_units": "已记录，等待作战单位",
                   "expansion_unknown": "未执行：尚未发现敌方分矿",
                   "not_enough_healthy_units": "未执行：健康兵力不足",
                   "squad_lost": "未执行：原小队已损失"}.get(result, "接收结果未记录")
        title = f"{stamp(start)}  {key} · {scope} · {name}  |  {receipt}"
        if not command.get("task_id"):
            return {"frame": start, "title": title, "effect": "原任务保持；没有新派兵或补兵"}
        task = command["task_id"]
        end, replaced = frame, False
        for future in self.commands[index+1:limit]:
            if future["frame"] > frame:
                break
            if future.get("action")=="restore_auto":
                end, replaced = future["frame"]-1, True
                break
            if future.get("action")=="split_squad" and future.get("reserve_task") and command.get("mode")=="all":
                end, replaced = future["frame"]-1, True
                break
            if future.get("action")=="split_squad":
                continue
            if future.get("task_id") and future.get("task_id") != command.get("task_id") and (future.get("mode") == "all" or command.get("mode") == "squad"):
                end, replaced = future["frame"]-1, True
                break
        events = [e for e in self.events.between(start, end) if e.get("task_id") == task]
        states = [e for e in events if e.get("event") == "battle_state"]
        state = states[-1] if states else {}
        effect = f"本次接管 {command.get('unit_count', len(command.get('units', [])))} 名"
        if state:
            effect += f"；当前 {len(state.get('units', []))} 名：" + BATTLE_PHASES.get(state.get("phase"), "状态待核对")
        else:
            effect += "；等待执行状态采样"
        completed = next((e for e in events if e.get("event") in ("expansion_complete","building_complete")), None)
        if completed or state.get("auto_withdrawal"):
            label = "目标建筑" if v11 else "目标分矿基地"
            reason = "主基地区域已确认清完" if completed and completed.get("reason")=="main_area_cleared" else label+"已摧毁" if completed and completed.get("reason")=="depot_destroyed" else "目标原址已确认清空" if completed else "建筑任务已完成" if v11 else "分矿任务已完成"
            effect += f"；{reason}，自动安全撤离"
            effect += "，等待下一条指令" if v11 else "，不继续进攻主基地"
        if any(e.get("event")=="clear_complete" for e in events) or state.get("clear_complete"):
            if state.get("standby_in_place") or any(e.get("standby_in_place") for e in events):
                effect+="；按7时没有可清理的兵或炮台，原地待命，保持各单位原位置"
            elif state.get("standby_policy")=="clear_open_posts_v1":
                area="基地外围空地" if state.get("standby_near_base") else "交战区附近安全空地"
                effect+=f"；清理结束，{area}分散待命，避开建筑、矿路和出兵口；限距迎敌后返回站位"
                if state.get("standby_waiting_space"):effect+=f"；{state['standby_waiting_space']} 名等待安全空位"
            else:
                effect += "；附近已无可见作战目标，清理结束，安全集结待命，不继续拆基地"
        if command.get("clear_policy") in ("ignore_workers_v2","persistent_guard_v3","local_guard_v4") or state.get("clear_policy") in ("ignore_workers_v2","persistent_guard_v3","local_guard_v4"):
            effect+="；忽略全部敌方工人，不因探机攻击或逃跑而追逐"
        elif command.get("army_worker_policy")=="ignore_worker_bait_v1":
            effect+="；不因敌方探机调兵、追击或改变安全汇合点"
        order_events = [e for e in events if e.get("event") == "battle_order"]
        choices=[e for e in events if e.get("event")=="target_selection"]
        locked_raid=state.get("raid_policy")=="locked_depot_no_chase_v1" or any(e.get("raid_policy")=="locked_depot_no_chase_v1" for e in order_events)
        if locked_raid:
            effect+=(("；建筑任务完成，部队和新援到共同安全点待命" if completed or state.get("auto_withdrawal") else
                      "；只进攻锁定建筑；射程内还击攻击者，敌人跑出射程就不追，继续原建筑任务") if v11 else
                     ("；破矿后进攻已结束，部队和新援到共同安全点待命" if completed or state.get("auto_withdrawal") else
                      "；锁定目标分矿；射程内还击攻击者，移动敌人跑出射程就不追，继续原分矿任务"))
        if state.get("raid_policy")=="combat_first_objective_v2" or any(e.get("raid_policy")=="combat_first_objective_v2" for e in order_events):
            effect+="；沿途优先接战真实敌军和炮台，限距追击后恢复原建筑任务；援军先汇合主力"
        if state.get("raid_policy")=="main_area_outer_first_v1" or any(e.get("raid_policy")=="main_area_outer_first_v1" for e in order_events):
            effect+="；只清固定主基地区域：敌军炮台优先，外围水晶及建筑逐步推进，主建筑最后；确认区域清完后到共同安全点待命"
            if state:effect+=f"；已知待清建筑 {state.get('main_known_buildings',0)}，待扫查位置 {state.get('main_unchecked',0)}"
        if state.get("clear_policy")=="persistent_guard_v3" or command.get("clear_policy")=="persistent_guard_v3":
            effect+="；持续警戒，不因暂时无目标而结束；敌军再现自动接战，丢失视野只作短暂局部搜索"
        if state.get("clear_policy")=="local_guard_v4" or command.get("clear_policy")=="local_guard_v4":
            effect+="；各部就近警戒，贴身敌军可还击，不受旧区域限制；再次按7更新警戒位置，保留集火；不追探机"
        if any(e.get("event")=="detection_skirmish" for e in events):effect+="；等待反隐期间仍在安全交战，原任务保留"
        if choices:
            if not locked_raid:effect+="；自动选敌：威胁优先，限制追击"
            latest=next((e for e in reversed(choices) if e.get("selected",-1)>=0),None)
            if latest:
                candidate=next((c for c in latest.get("candidates",[]) if c.get("id")==latest["selected"]),{})
                label={162:"炮台",66:"龙骑士",64:"探机"}.get(candidate.get("type"),
                    "关键建筑" if candidate.get("important") else "敌方建筑" if candidate.get("building") else "敌方单位")
                effect+=f"；最近选敌记录：{label}"
                if candidate.get("active"):effect+="（正在攻击我方）"
            released=sum(e.get("event")=="target_release" for e in events)
            if released:effect+=f"；已结束越界追击 {released} 次"
        perimeter=state.get("defence_policy")=="perimeter_bounded_v1" or any(e.get("defence_policy")=="perimeter_bounded_v1" for e in order_events)
        rendezvous=state.get("rendezvous_policy")=="shared_safe_point_v1" or any(e.get("rendezvous_policy")=="shared_safe_point_v1" for e in order_events)
        if rendezvous:
            effect+="；前线先脱战，后方及新造援兵到同一安全点，汇合后等待人工指令"
            if state:
                effect+=f"；已到位 {state.get('rally_waiting',0)}、赶来 {state.get('rally_joining',0)}、避险 {state.get('rally_evading',0)}、路线等待 {state.get('rally_blocked',0)}"
                if state.get('rally_new_units'):effect+=f"；本任务新加入 {state['rally_new_units']} 名新兵"
            if any(e.get('event')=='rendezvous_point' and e.get('reason')=='known_threat' for e in events):effect+="；已因可见威胁调整汇合点"
        if command.get("assembly_policy")=="fixed_front_wait_v1" or state.get("assembly_policy")=="fixed_front_wait_v1":
            effect+="；固定前线集结区，前线守位、后方和新兵赶来；忽略探机，只在射程内还击；到齐后等下一条指令"
        if command.get("assembly_policy")=="shared_local_refuge_v2" or state.get("assembly_policy")=="shared_local_refuge_v2":
            effect+="；原地等援，受压时使用原集结区附近同一避险点，避免分散；安全后返回原岗位"
        if perimeter:
            effect+="；返程撤离优先；到基地外围保护建筑和工人，限距拦截，敌人离开后收回防线"
        if state.get("multibase_policy")=="proportional_reinforce_v1":
            distribution=state.get("base_distribution",[])
            parts=[]
            for base in distribution:
                label="主矿" if not base.get("number") else f"分矿{base['number']}"
                if not base.get("completed",True):label+="（建设中）"
                parts.append(f"{label}{base.get('assigned',0)}名")
            if parts:effect+="；多基地分守："+"、".join(parts)
            effect+=f"；受袭按敌情增援，再按{'0' if command.get('keymap')=='toprow_commands_v17' else '9' if v11 else '8'}恢复常规比例"
            if state.get("defence_reinforced"):effect+="；当前保留增援部署"
        if not perimeter and not rendezvous and (state.get("flight_policy")=="run_first" or any(e.get("flight_policy")=="run_first" for e in order_events)):
            effect+="；撤离优先，贴近时短暂原地还击，不追敌"
            shots=sum(e.get("event")=="flight_burst_end" and e.get("fired") is True for e in events)
            if shots:effect+=f"；已记录还击 {shots} 次"
        conflicts = sum(e.get("conflict") is True for e in order_events)
        if conflicts:
            effect += f"；已拦截冲突指令 {conflicts} 次"
        if any(e.get("accepted") is False or e.get("verified") is False for e in order_events):
            effect += "；存在下令失败，需检查回放"
        if replaced:
            effect += "；已被新的部队动作替换"
            if any(c.get("action")=="restore_auto" and c["frame"]==end+1 for c in self.commands[index+1:limit]):effect+=f"（{reset_key(command)}全部重置，交回Bot）"
        return {"frame": start, "title": title, "effect": effect}

    @staticmethod
    def valid_point(value):
        return isinstance(value, (list, tuple)) and len(value) == 2 and all(
            isinstance(v, (int, float)) and math.isfinite(v) for v in value)

    def snapshot(self, frame, command_index=None):
        count = bisect_right(self.command_frames, frame)
        if command_index is not None:
            count = min(count, command_index+1)
        recent = [self.describe_command(i, frame, count) for i in range(max(0, count-3), count)]
        state = self.states.latest(frame) or {}
        economy = self.economy.latest(frame) or {}
        status = []
        mode_line = "旧版按键记录"
        if self.mode_controls:
            last_command = next((c for c in reversed(self.commands[:count]) if c.get("schema", 0) >= 3), {})
            mode = last_command.get("mode", state.get("mode", "all"))
            mode_line = "当前模式：" + MODE_NAMES.get(mode, "未知")
            # Commands contain the exact assignment at the key frame. Use it
            # between periodic samples, including when navigating one of several
            # distinct commands recorded in the same game frame.
            if last_command.get("frame", -1) >= state.get("frame", -1):
                state = dict(state)
                state.setdefault("frame", last_command.get("frame", frame))
                state.setdefault("schema", last_command.get("schema", 3))
                for field in ("main_task", "squad_task", "squad_ids", "evade", "probe_id", "mining_lock", "keymap"):
                    if field in last_command:
                        state[field] = last_command[field]
                if "army_ids" in last_command:
                    state["army"] = len(last_command["army_ids"])
                    state["main_army"] = state["army"]-len(state.get("squad_ids", []))
            if self.dynamic_squad:
                update = next((e for e in reversed(self.events.rows[:bisect_right(self.events.frames,frame)])
                               if e.get("event") == "squad_rebalance"), None)
                if update and update["frame"] > max(state.get("frame",-1),last_command.get("frame",-1)):
                    state = dict(state, frame=update["frame"], squad_ids=update["squad_ids"],
                                 army=update["army"], main_army=update["army"]-len(update["squad_ids"]))
        if state:
            status.append(f"部队采样@{stamp(state['frame'])}" if self.mode_controls else
                          f"状态@{stamp(state['frame'])}  {STANCES.get(state.get('stance'), '未知')}")
            status.append(f"主力 {state.get('main_army', '—')} / 部队 {state.get('army', '—')}")
            if state.get("schema", 0) < 3:
                status.append("侦察 " + TASKS.get(state.get("scout"), "未知"))
            if state.get("evade") not in (None, "idle"):
                status.append("探机避险 " + TASKS.get(state["evade"], "未知"))
            if state.get("schema", 0) >= 3:
                # A mode key is effective immediately, even between state samples.
                status.insert(0, mode_line)
                if "mining_lock" in state:status.append("强制采矿："+("开" if state["mining_lock"] else "关"))
                status.append(f"动态小队 {len(state.get('squad_ids', []))} 名（自动补兵，至少留6名健康兵回防）" if self.dynamic_squad else
                              f"固定小队 {len(state.get('squad_ids', []))} 名")
                for field, label in (("main_task", "主力任务"), ("squad_task", "小队任务")):
                    task = state.get(field)
                    latest = next((e for e in reversed(self.events.rows[:bisect_right(self.events.frames, frame)])
                                   if e.get("event") == "battle_state" and e.get("task_id") == task), None)
                    if latest:
                        action_label=STANCES.get(latest.get("action"), "未知")
                        if tenkeys(state):
                            action_label=({"near_enemy":"攻近敌建筑","expansion":"攻分矿建筑","main":"攻主建筑"}.get(latest.get("objective_role"),action_label)
                                          if not latest.get("auto_withdrawal") else "安全集结")
                            if latest.get("clear_task"):action_label="区域警戒" if latest.get("clear_policy") in ("persistent_guard_v3","local_guard_v4") else "就近待命" if latest.get("clear_complete") else "清兵和炮台"
                            if latest.get("assembly_policy") in ("fixed_front_wait_v1","shared_local_refuge_v2"):action_label="原地集结"
                        status.append(label + "：" + action_label + " · "
                                      + BATTLE_PHASES.get(latest.get("phase"), "未知"))
                        if latest.get("auto_withdrawal"):
                            status[-1] += "（建筑任务完成）" if tenkeys(state) else "（分矿任务完成）"
                    elif task:
                        assignment=next((c for c in reversed(self.commands[:count]) if c.get("task_id")==task or c.get("reserve_task")==task), {})
                        action="defend" if assignment.get("reserve_task")==task else assignment.get("action")
                        if action=="split_squad":
                            inherited=next((c for c in self.commands[:count] if c.get("task_id")==assignment.get("inherited_task")), {})
                            action=inherited.get("action", "baseline")
                        status.append(label+"："+STANCES.get(action,"自动战斗")+" · 等待状态采样")
                    elif field=="main_task" and (state.get("schema",0)>=5 or last_command.get("schema",0)>=5):
                        status.append("主力任务：Bot自动控制")
        if economy:
            status.append(f"经济@{stamp(economy['frame'])}  矿 {economy.get('minerals', '—')} 气 {economy.get('gas', '—')} 工人 {economy.get('workers', '—')}")
        base_notice=next((e for e in reversed(self.events.rows[:bisect_right(self.events.frames,frame)]) if e.get('event')=='base_notice'),None)
        if base_notice and frame-base_notice['frame']<=240:
            status.append("基地提示："+str(base_notice.get('message','')))
        return {"recent": recent, "count": count, "total": len(self.commands),
                "mode": mode_line,
                "status": "  |  ".join(status) or "当前时刻暂无状态记录",
                "notice": self.notice}

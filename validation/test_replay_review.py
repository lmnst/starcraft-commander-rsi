import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import nullcontext

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from replay_review import ReplayReview


class ReviewTests(unittest.TestCase):
    def test_v17_physical_keys_preserve_v16_history(self):
        base={"event":"command","schema":6,"keymap":"toprow_commands_v17","mode":"all","status":"applied","modifier":"none"}
        model=self.model([
            dict(base,frame=0,key="9",action="retreat",task_id=1),
            dict(base,frame=10,key="0",action="defend",task_id=2),
            dict(base,frame=20,key="-",action="restore_auto",status="auto_restored"),
            dict(base,frame=30,key="8",modifier="alt",keymap="tenkeys_main_area_v16",action="retreat",task_id=3),
            dict(base,frame=40,key="9",keymap="tenkeys_main_area_v16",action="defend",task_id=4),
            dict(base,frame=50,key="0",keymap="tenkeys_main_area_v16",action="restore_auto",status="auto_restored")])
        for index,key,label in ((0,"9","安全撤离集结"),(1,"0","全军回防"),(2,"-","全局重置"),(3,"Alt+8","安全撤离集结"),(4,"9","全军回防"),(5,"0","全局重置")):
            title=model.describe_command(index,60)['title']
            self.assertIn(key+" ·",title)
            self.assertIn(label,title)

    def test_v16_area_assault_completion_preserves_old6(self):
        common={"event":"command","schema":6,"mode":"all","status":"applied","key":"6","action":"attack"}
        model=self.model([
            dict(common,frame=0,keymap="tenkeys_main_area_v16",task_id=1),
            {"event":"battle_state","frame":5,"task_id":1,"phase":"main_outer_pylon","units":[1],"raid_policy":"main_area_outer_first_v1","main_known_buildings":3,"main_unchecked":7},
            {"event":"building_complete","frame":15,"task_id":1,"reason":"main_area_cleared"},
            {"event":"battle_state","frame":16,"task_id":1,"phase":"rally_waiting","units":[1],"raid_policy":"main_area_outer_first_v1","auto_withdrawal":True,"main_known_buildings":0,"main_unchecked":0},
            dict(common,frame=30,keymap="tenkeys_local_combat_v15",task_id=2)])
        self.assertIn("主基地区域清场",model.describe_command(0,6)['title'])
        self.assertIn("外围水晶",model.describe_command(0,6)['effect'])
        self.assertIn("主基地区域已确认清完",model.describe_command(0,20)['effect'])
        self.assertIn("进攻主基地",model.describe_command(1,35)['title'])

    def test_v15_reanchored_guard_shared_refuge_and_alt8(self):
        base={"event":"command","schema":6,"keymap":"tenkeys_local_combat_v15","mode":"all","status":"applied"}
        model=self.model([
            dict(base,frame=0,key="7",action="clear_enemies",task_id=1,clear_policy="local_guard_v4"),
            dict(base,frame=10,key="7",action="clear_enemies",task_id=1,clear_policy="local_guard_v4",status="patrol_reanchored"),
            {"event":"battle_state","frame":12,"task_id":1,"phase":"detection_skirmishing","units":[1]},
            {"event":"detection_skirmish","frame":13,"task_id":1,"unit_id":1},
            dict(base,frame=20,key="8",action="rally",task_id=2,assembly_policy="shared_local_refuge_v2"),
            {"event":"battle_state","frame":22,"task_id":2,"phase":"assembly_shared_refuge","units":[1],"assembly_policy":"shared_local_refuge_v2"},
            dict(base,frame=30,key="8",modifier="alt",action="retreat",task_id=3)])
        guard=model.describe_command(1,18)
        self.assertIn("警戒位置已更新",guard['title'])
        self.assertIn("不受旧区域限制",guard['effect'])
        self.assertIn("安全交战",guard['effect'])
        self.assertIn("同一避险点",model.describe_command(2,25)['effect'])
        self.assertIn("安全撤离集结",model.describe_command(3,35)['title'])

    def test_v14_stationary_and_alt8_withdrawal_keep_old8_history(self):
        base={"event":"command","schema":6,"keymap":"tenkeys_assembly_v14","mode":"all","status":"applied","key":"8"}
        model=self.model([
            dict(base,frame=0,action="rally",task_id=1,modifier="none",assembly_policy="fixed_front_wait_v1"),
            {"event":"battle_state","frame":10,"task_id":1,"action":"rally","phase":"assembly_waiting","units":[1,2],"assembly_policy":"fixed_front_wait_v1"},
            dict(base,frame=20,action="retreat",task_id=2,modifier="alt",rendezvous_policy="shared_safe_point_v1"),
            {"event":"battle_state","frame":30,"task_id":2,"action":"retreat","phase":"rally_waiting","units":[1,2],"rendezvous_policy":"shared_safe_point_v1"},
            dict(base,frame=40,keymap="tenkeys_detection_v13",action="retreat",task_id=3),
        ])
        stationary=model.describe_command(0,15)
        self.assertIn("8 · 全队 · 原地集结",stationary["title"])
        self.assertIn("前线守位",stationary["effect"])
        self.assertNotIn("前线先脱战",stationary["effect"])
        retreat=model.describe_command(1,35)
        self.assertIn("Alt+8",retreat["title"])
        self.assertIn("安全撤离集结",retreat["title"])
        self.assertIn("前线先脱战",retreat["effect"])
        self.assertIn("安全集结",model.describe_command(2,45)["title"])

    def test_v13_detection_pause_resumes_original_task_and_preserves_v12(self):
        common={"event":"command","schema":6,"keymap":"tenkeys_detection_v13","mode":"all","status":"applied"}
        model=self.model([
            dict(common,frame=0,key="6",action="attack",task_id=1),
            {"event":"battle_state","frame":10,"task_id":1,"action":"attack","phase":"evading_cloak","units":[1,2]},
            {"event":"battle_state","frame":20,"task_id":1,"action":"attack","phase":"waiting_detection","units":[1,2]},
            {"event":"battle_state","frame":30,"task_id":1,"action":"attack","phase":"engaging","units":[1,2]},
            dict(common,frame=40,key="7",action="clear_enemies",task_id=2,clear_policy="persistent_guard_v3"),
            {"event":"battle_state","frame":50,"task_id":2,"action":"clear_enemies","phase":"waiting_detection","units":[1,2]}])
        self.assertIn("进攻主基地",model.describe_command(0,10)["title"])
        self.assertIn("局部避让",model.describe_command(0,10)["effect"])
        self.assertIn("等待反隐",model.describe_command(0,20)["effect"])
        self.assertIn("正在交战",model.describe_command(0,30)["effect"])
        self.assertIn("持续区域警戒",model.describe_command(1,50)["title"])
        self.assertIn("等待反隐",model.describe_command(1,50)["effect"])

    def test_v12_continuous_combat_and_confirmed_task_keep_history(self):
        common={"event":"command","schema":6,"keymap":"tenkeys_combat_v12","mode":"all","status":"applied"}
        model=self.model([
            dict(common,frame=10,key="6",action="attack",task_id=1),
            dict(common,frame=20,key="6",action="attack",task_id=1,status="task_confirmed"),
            {"event":"battle_state","frame":30,"task_id":1,"action":"attack","phase":"engaging","units":[1,2],"raid_policy":"combat_first_objective_v2"},
            dict(common,frame=40,key="7",action="clear_enemies",task_id=2,clear_policy="persistent_guard_v3"),
            {"event":"battle_state","frame":41,"task_id":2,"action":"clear_enemies","phase":"patrol_waiting","units":[1,2],"clear_policy":"persistent_guard_v3"},
            {"event":"battle_state","frame":60,"task_id":2,"action":"clear_enemies","phase":"patrol_searching","units":[1,2],"clear_policy":"persistent_guard_v3"}])
        first=model.describe_command(0,35)
        self.assertIn("进攻主基地",first['title'])
        self.assertIn("沿途优先接战",first['effect'])
        self.assertIn("正在交战",first['effect'])
        self.assertNotIn("只进攻锁定建筑",first['effect'])
        self.assertIn("原任务继续",model.describe_command(1,35)['title'])
        guard=model.describe_command(2,65)
        self.assertIn("持续区域警戒",guard['title'])
        self.assertIn("短暂局部搜索",guard['effect'])
        self.assertIn("忽略全部敌方工人",guard['effect'])
        self.assertNotIn("清理结束",guard['effect'])

    def test_worker_bait_policy_and_empty_area_hold_are_versioned(self):
        common={"event":"command","schema":6,"keymap":"tenkeys_economy_v11","mode":"all","status":"applied"}
        model=self.model([
            dict(common,frame=10,key="7",action="clear_enemies",task_id=1,clear_policy="ignore_workers_v2"),
            {"event":"clear_complete","frame":10,"task_id":1,"standby_in_place":True},
            {"event":"battle_state","frame":11,"task_id":1,"action":"rally","phase":"standby_waiting","units":[1,2],
             "clear_complete":True,"clear_policy":"ignore_workers_v2","standby_in_place":True,"standby_policy":"clear_open_posts_v1"},
            dict(common,frame=30,key="9",action="defend",task_id=2,army_worker_policy="ignore_worker_bait_v1"),
            dict(common,frame=40,key="7",action="clear_enemies",task_id=3)])
        effect=model.describe_command(0,20)['effect']
        self.assertIn("保持各单位原位置",effect)
        self.assertIn("忽略全部敌方工人",effect)
        self.assertNotIn("安全空地分散",effect)
        self.assertIn("不因敌方探机调兵",model.describe_command(1,35)['effect'])
        self.assertNotIn("忽略全部敌方工人",model.describe_command(2,45)['effect'])

    def test_v11_mining_ends_probe_rescue_and_proxy_label_is_explicit(self):
        common={"event":"command","schema":6,"keymap":"tenkeys_economy_v11","mode":"all"}
        model=self.model([
            dict(common,frame=10,key="1",action="scout_evade",status="scout_evade_applied",probe_id=8,evade="evading"),
            {"event":"scout_evade_sample","frame":15,"probe_id":8,"phase":"evading"},
            dict(common,frame=20,key="2",action="mine_lock",status="mining_locked",mining_lock=True),
            {"event":"scout_evade_sample","frame":30,"probe_id":8,"phase":"returning"},
            dict(common,frame=40,key="5",action="expansion",status="applied",task_id=1,main_task=1),
            {"event":"battle_state","frame":41,"task_id":1,"action":"expansion","phase":"advancing","objective_role":"near_enemy"}])
        effect=model.describe_command(0,35)['effect']
        self.assertIn("切回强制采矿",effect)
        self.assertNotIn("返程",effect)
        self.assertIn("攻近敌建筑",model.snapshot(41)['status'])

    def test_v11_keymap_and_mining_history_are_versioned(self):
        common={"event":"command","schema":6,"keymap":"tenkeys_economy_v11","mode":"all","status":"applied"}
        rows=[dict(common,frame=0,key="2",action="mine_lock",status="mining_locked",mining_lock=True),
              {"event":"mining_blocked","frame":5},
              dict(common,frame=10,key="3",action="split_squad",status="squad_split",units=[1,2],reserve_units=list(range(3,9))),
              dict(common,frame=20,key="4",action="select_mode"),
              dict(common,frame=30,key="5",action="expansion",task_id=1),
              {"event":"building_complete","frame":40,"task_id":1,"reason":"depot_destroyed"},
              dict(common,frame=50,key="6",action="attack",task_id=2),
              dict(common,frame=60,key="7",action="clear_enemies",task_id=3),
              dict(common,frame=70,key="8",action="retreat",task_id=4),
              dict(common,frame=80,key="9",action="defend",task_id=5,global_defence=True),
              dict(common,frame=90,key="0",action="restore_auto",mining_lock=False)]
        model=self.model(rows)
        self.assertIn("强制采矿",model.describe_command(0,5)['title'])
        self.assertIn("1 次",model.describe_command(0,5)['effect'])
        self.assertIn("3 · 小队",model.describe_command(1,10)['title'])
        for i,label in enumerate(("全队模式","近敌／分矿建筑","主建筑","兵和炮台","安全集结","全军回防"),2):
            self.assertIn(label,model.describe_command(i,90)['title'])
        self.assertNotIn("已摧毁",model.describe_command(3,35)['effect'])
        self.assertIn("目标建筑已摧毁",model.describe_command(3,45)['effect'])
        self.assertIn("矿锁已解除",model.describe_command(8,90)['effect'])
        self.assertIn("强制采矿：开",model.snapshot(1)['status'])
        self.assertIn("强制采矿：关",model.snapshot(90)['status'])

    def test_global_defence_distribution_and_expansion_notices_are_time_scoped(self):
        model=self.model([
            {"event":"command","schema":5,"frame":10,"key":"8","action":"defend","mode":"all","status":"applied","task_id":1,"unit_count":20,"global_defence":True},
            {"event":"state","schema":5,"frame":10,"army":20,"main_army":20,"main_task":1,"squad_ids":[]},
            {"event":"base_notice","frame":20,"message":"分矿1开建：附近0兵；8全军回防。"},
            {"event":"battle_state","frame":30,"task_id":1,"action":"defend","phase":"returning","units":list(range(20)),"multibase_policy":"proportional_reinforce_v1","base_distribution":[{"id":1,"number":0,"completed":True,"assigned":14},{"id":2,"number":1,"completed":False,"assigned":6}]},
            {"event":"battle_state","frame":50,"task_id":1,"action":"defend","phase":"intercepting","units":list(range(20)),"multibase_policy":"proportional_reinforce_v1","defence_reinforced":True,"base_distribution":[{"id":1,"number":0,"completed":True,"assigned":10},{"id":2,"number":1,"completed":False,"assigned":10}]},
        ])
        self.assertIn("全军回防",model.describe_command(0,10)['title'])
        self.assertNotIn("分矿1开建",model.snapshot(15)['status'])
        self.assertIn("分矿1开建",model.snapshot(25)['status'])
        self.assertIn("主矿14名",model.describe_command(0,30)['effect'])
        self.assertIn("建设中",model.describe_command(0,30)['effect'])
        self.assertNotIn("保留增援部署",model.describe_command(0,30)['effect'])
        self.assertIn("保留增援部署",model.describe_command(0,50)['effect'])
        self.assertIn("再按8",model.describe_command(0,50)['effect'])

    def test_clear_standby_summary_tracks_area_and_does_not_leak_completion(self):
        model=self.model([
            {"event":"command","schema":5,"frame":10,"key":"9","action":"clear_enemies","mode":"all","status":"applied","task_id":1,"unit_count":20},
            {"event":"clear_complete","frame":20,"task_id":1},
            {"event":"battle_state","frame":20,"task_id":1,"action":"rally","phase":"standby_forming","units":list(range(20)),"clear_complete":True,"standby_policy":"clear_open_posts_v1","standby_near_base":True},
            {"event":"battle_state","frame":40,"task_id":1,"action":"rally","phase":"standby_waiting","units":list(range(20)),"clear_complete":True,"standby_policy":"clear_open_posts_v1","standby_near_base":True},
            {"event":"battle_state","frame":60,"task_id":1,"action":"rally","phase":"standby_returning","units":list(range(20)),"clear_complete":True,"standby_policy":"clear_open_posts_v1","standby_near_base":False,"standby_waiting_space":2},
        ])
        self.assertNotIn("清理结束",model.describe_command(0,15)['effect'])
        effect=model.describe_command(0,40)['effect']
        self.assertIn("基地外围空地",effect)
        self.assertIn("矿路和出兵口",effect)
        self.assertIn("空地待命",effect)
        later=model.describe_command(0,60)['effect']
        self.assertIn("交战区附近安全空地",later)
        self.assertIn("2 名等待安全空位",later)
        self.assertIn("返回各自站位",later)

    def test_shared_rally_tracks_reinforcements_waiting_and_manual_override(self):
        model=self.model([
            {"event":"command","schema":5,"frame":10,"key":"7","mode":"all","action":"retreat","status":"applied","task_id":1,"unit_count":12,"rendezvous_policy":"shared_safe_point_v1"},
            {"event":"battle_state","frame":20,"task_id":1,"action":"retreat","phase":"rally_withdrawing","units":list(range(12)),"rendezvous_policy":"shared_safe_point_v1","rally_evading":4,"rally_joining":8},
            {"event":"rendezvous_point","frame":40,"task_id":1,"reason":"known_threat"},
            {"event":"battle_state","frame":60,"task_id":1,"action":"retreat","phase":"rally_waiting","units":list(range(15)),"rendezvous_policy":"shared_safe_point_v1","rally_waiting":10,"rally_joining":5,"rally_new_units":3},
            {"event":"command","schema":5,"frame":100,"key":"5","mode":"all","action":"attack","status":"applied","task_id":2,"unit_count":15},
        ])
        early=model.snapshot(20)['recent'][0]
        self.assertIn("撤退等援兵",early['title'])
        self.assertIn("后方及新造援兵到同一安全点",early['effect'])
        self.assertNotIn("新加入",early['effect'])
        later=model.snapshot(60)['recent'][0]['effect']
        self.assertIn("已到位 10",later)
        self.assertIn("新加入 3 名新兵",later)
        self.assertIn("可见威胁调整汇合点",later)
        self.assertIn("已被新的部队动作替换",model.describe_command(0,100)['effect'])
        self.assertNotIn("等待人工指令",model.snapshot(100)['recent'][-1]['effect'])

    def test_perimeter_defense_reports_interception_and_recall(self):
        model=self.model([
            {"event":"command","schema":5,"frame":10,"key":"8","mode":"all","action":"defend","status":"applied","task_id":1,"unit_count":8},
            {"event":"battle_state","frame":20,"task_id":1,"action":"defend","phase":"intercepting","units":list(range(8)),"flight_policy":"return_then_defend","defence_policy":"perimeter_bounded_v1"},
            {"event":"battle_state","frame":100,"task_id":1,"action":"defend","phase":"reforming","units":list(range(8)),"defence_policy":"perimeter_bounded_v1"},
        ])
        self.assertNotIn("限距拦截",model.snapshot(19)['recent'][0]['effect'])
        at=model.snapshot(20)['recent'][0]['effect']
        self.assertIn("保护建筑和工人",at)
        self.assertIn("短距离拦截",at)
        self.assertIn("收回防线",model.snapshot(100)['recent'][0]['effect'])

    def test_global_reset_cuts_both_groups_and_probe_then_allows_reentry(self):
        model=self.model([
            {"event":"command","schema":5,"frame":10,"key":"1","action":"scout_evade","status":"scout_evade_applied","probe_id":77},
            {"event":"scout_evade_state","frame":15,"probe_id":77,"phase":"evading"},
            {"event":"command","schema":5,"frame":20,"key":"9","action":"clear_enemies","mode":"squad","status":"applied","task_id":4,"main_task":3,"squad_task":4,"squad_ids":[1,2],"units":[1,2],"unit_count":2},
            {"event":"battle_state","frame":25,"task_id":4,"phase":"engaging","action":"clear_enemies","units":[1,2]},
            {"event":"command","schema":5,"frame":50,"key":"0","action":"restore_auto","status":"auto_restored","mode":"all","previous_mode":"squad","reset_scope":"global","task_id":0,"main_task":0,"squad_task":0,"squad_ids":[],"released_probe":77,"released_units":[1,2,3],"released_tasks":[3,4],"evade":"idle","probe_id":-1},
            {"event":"auto_control","frame":60,"released_at":50,"unit_id":77,"baseline_untouched":True},
            {"event":"command","schema":5,"frame":80,"key":"9","action":"clear_enemies","status":"applied","mode":"all","task_id":5,"main_task":5,"squad_task":0,"squad_ids":[],"unit_count":4},
            {"event":"battle_state","frame":100,"task_id":5,"action":"rally","phase":"holding","units":[1,2,3,4],"clear_complete":True},
        ])
        early=model.snapshot(49)
        self.assertNotIn("已重置",str(early))
        reset=model.snapshot(50)
        self.assertIn("全部恢复自动",reset['recent'][-1]['title'])
        self.assertIn("主力任务：Bot自动控制",reset['status'])
        self.assertNotIn("探机避险",reset['status'])
        self.assertIn("0全部重置",model.describe_command(0,60)['effect'])
        self.assertIn("0全部重置",model.describe_command(1,60)['effect'])
        self.assertNotIn("观察到Bot",reset['recent'][-1]['effect'])
        self.assertIn("Bot重新下令 1 个单位",model.snapshot(60)['recent'][-1]['effect'])
        later=model.snapshot(100)
        self.assertIn("清理敌军",later['recent'][-1]['title'])
        self.assertIn("清理结束",later['recent'][-1]['effect'])
        self.assertNotIn("交回Bot",later['recent'][-1]['effect'])

    def test_reset_and_reacquire_same_frame_navigation(self):
        model=self.model([
            {"event":"command","schema":5,"frame":20,"key":"0","action":"restore_auto","status":"auto_restored","mode":"all","task_id":0,"main_task":0,"squad_task":0,"squad_ids":[],"evade":"idle"},
            {"event":"command","schema":5,"frame":20,"key":"9","action":"clear_enemies","status":"applied","mode":"all","task_id":7,"main_task":7,"squad_task":0,"squad_ids":[],"unit_count":4},
            {"event":"state","schema":5,"frame":20,"mode":"all","main_task":7,"squad_task":0,"army":4},
            {"event":"battle_state","frame":20,"task_id":7,"action":"clear_enemies","phase":"engaging","units":[1,2,3,4]},
        ])
        at_reset=model.snapshot(20,0)
        self.assertIn("Bot自动控制",at_reset['status'])
        self.assertNotIn("清理敌军",str(at_reset))
        at_clear=model.snapshot(20,1)
        self.assertIn("清理敌军",at_clear['status'])

    def test_expansion_completion_shows_automatic_retreat_at_correct_frame(self):
        model=self.model([
            {"event":"command","schema":4,"frame":10,"key":"4","mode":"all","action":"expansion","status":"applied","task_id":1,"unit_count":8},
            {"event":"battle_state","frame":20,"task_id":1,"action":"expansion","phase":"engaging","units":list(range(8))},
            {"event":"expansion_complete","frame":100,"task_id":1,"reason":"depot_destroyed"},
            {"event":"battle_state","frame":100,"task_id":1,"action":"retreat","phase":"retreating","auto_withdrawal":True,"flight_policy":"run_first","units":list(range(8))},
            {"event":"battle_state","frame":200,"task_id":1,"action":"retreat","phase":"safe","auto_withdrawal":True,"flight_policy":"run_first","units":list(range(8))},
            {"event":"command","schema":4,"frame":250,"key":"5","mode":"all","action":"attack","status":"applied","task_id":2,"unit_count":8},
        ])
        self.assertNotIn("自动安全撤离",model.snapshot(99)["recent"][0]["effect"])
        at=model.snapshot(100)["recent"][0]["effect"]
        self.assertIn("目标分矿基地已摧毁",at)
        self.assertIn("不继续进攻主基地",at)
        self.assertIn("撤离优先",at)
        self.assertIn("已撤到安全位置",model.snapshot(200)["recent"][0]["effect"])
        self.assertNotIn("自动安全撤离",model.snapshot(250)["recent"][-1]["effect"])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.replay = self.root / "maps/replays/match.rep"
        self.replay.parent.mkdir(parents=True)
        self.replay.write_bytes(b"test")
        (self.root / "match.json").write_text(json.dumps({"replay": "maps/replays/match.rep", "control": "human"}))
        self.logs = self.root / "bwapi-data/write"
        self.logs.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def model(self, rows, economy=()):
        for name, values in (("commander-layer", rows), ("astra-trace", economy)):
            (self.logs / (name + ".jsonl")).write_text(
                "\n".join(json.dumps(r) for r in values), encoding="utf-8")
        return ReplayReview(self.replay)

    def test_time_reversal_never_reveals_future_commands_or_effects(self):
        model = self.model([
            {"event": "command", "frame": 100, "key": "8", "status": "applied", "stance": "retreat"},
            {"event": "state", "frame": 120, "stance": "retreat"},
            {"event": "retreat_complete", "frame": 200},
            {"event": "command", "frame": 300, "key": "F5", "status": "applied"},
        ], [{"event": "state", "frame": 250, "minerals": 900}])
        self.assertIn("撤退完成", model.snapshot(220)["recent"][0]["effect"])
        early = model.snapshot(150)
        self.assertEqual(early["count"], 1)
        self.assertNotIn("撤退完成", early["recent"][0]["effect"])
        self.assertNotIn("900", early["status"])
        self.assertEqual(model.snapshot(99)["recent"], [])
        self.assertIn("F5", model.snapshot(300)["recent"][-1]["title"])

    def test_receipt_is_not_task_success_and_waiting_is_visible(self):
        model = self.model([
            {"event": "command", "frame": 10, "key": "1", "status": "applied"},
            {"event": "state", "frame": 20, "scout": "waiting_units"},
            {"event": "scout_start", "frame": 40, "reason": "42"},
            {"event": "state", "frame": 40, "scout": "active"},
        ])
        row = model.snapshot(20)["recent"][0]
        self.assertIn("已接收", row["title"])
        self.assertIn("等待可用单位", row["effect"])
        self.assertNotIn("已记录派出", row["effect"])
        self.assertIn("已记录派出", model.snapshot(40)["recent"][0]["effect"])

    def test_rejection_and_command_attribution(self):
        model = self.model([
            {"event": "command", "frame": 10, "key": "2", "status": "not_enough_army"},
            {"event": "command", "frame": 20, "key": "5", "status": "applied", "origin": [0, 0], "goal": [1000, 0]},
            {"event": "command", "frame": 30, "key": "6", "status": "applied"},
            {"event": "state", "frame": 40, "stance": "defend", "center": [900, 0]},
        ])
        recent = model.snapshot(40)["recent"]
        self.assertIn("未派出", recent[0]["title"])
        self.assertNotIn("缩短", recent[1]["effect"])
        self.assertIn("后续已下新指令", recent[1]["effect"])
        self.assertEqual(model.neighbor(20, -1), 10)
        self.assertEqual(model.neighbor(20, 1), 30)
        self.assertIsNone(model.neighbor(30, 1))

    def test_scout_evade_preserves_old_meaning_and_shows_actual_outcome(self):
        model = self.model([
            {"event": "command", "frame": 10, "key": "1", "status": "applied"},
            {"event": "command", "frame": 30, "key": "1", "action": "scout_evade", "status": "no_scout_probe"},
            {"event": "command", "frame": 50, "key": "1", "action": "scout_evade", "status": "scout_evade_applied", "probe_id": 42, "evade": "evading"},
            {"event": "scout_evade_order", "frame": 60, "probe_id": 42, "phase": "evading", "overrides": 1, "accepted": True},
            {"event": "scout_evade_state", "frame": 90, "probe_id": 42, "phase": "home_safe", "overrides": 3, "accepted": True},
        ])
        self.assertNotIn("侦察避险", model.describe_command(0, 100)["title"])
        self.assertIn("没有可接管", model.describe_command(1, 100)["title"])
        self.assertIn("未派出新单位", model.describe_command(1, 100)["effect"])
        early = model.describe_command(2, 60)
        self.assertIn("侦察避险", early["title"])
        self.assertIn("#42", early["effect"])
        self.assertIn("脱离危险", early["effect"])
        self.assertNotIn("已回家", early["effect"])
        self.assertIn("已回家采矿", model.describe_command(2, 100)["effect"])
        self.assertIn("3 次", model.describe_command(2, 100)["effect"])

    def test_missing_mismatched_and_damaged_logs(self):
        self.assertIn("没有本局", ReplayReview(self.replay).notice)
        model = self.model([{ "event": "command", "frame": 10, "key": "7", "status": "applied"}])
        with (self.logs / "commander-layer.jsonl").open("a") as out:
            out.write("\n{bad json")
        self.assertIn("损坏", ReplayReview(self.replay).notice)
        other = self.replay.with_name("other.rep")
        other.write_bytes(b"test")
        self.assertIn("不匹配", ReplayReview(other).notice)

    def test_mode_switch_does_not_cut_off_running_squad_task(self):
        model = self.model([
            {"event":"command", "schema":3, "frame":10, "key":"5", "mode":"squad", "action":"attack", "status":"applied", "task_id":1, "units":[1,2], "unit_count":2},
            {"event":"state", "schema":3, "frame":15, "mode":"squad", "squad_ids":[1,2], "squad_task":1, "main_task":0},
            {"event":"command", "schema":3, "frame":20, "key":"3", "mode":"all", "action":"select_mode", "status":"mode_selected", "task_id":0},
            {"event":"battle_state", "schema":3, "frame":30, "task_id":1, "units":[1,2], "action":"attack", "phase":"engaging"},
            {"event":"command", "schema":3, "frame":40, "key":"8", "mode":"all", "action":"defend", "status":"applied", "task_id":2, "units":[1,2,3], "unit_count":3},
            {"event":"battle_state", "schema":3, "frame":50, "task_id":2, "units":[1,2,3], "action":"defend", "phase":"guarding"},
        ])
        self.assertIn("小队 · 进攻主基地", model.describe_command(0,30)["title"])
        self.assertIn("正在交战", model.describe_command(0,30)["effect"])
        self.assertNotIn("替换", model.describe_command(0,30)["effect"])
        self.assertIn("本身不派兵", model.describe_command(1,30)["effect"])
        self.assertIn("当前模式：全队", model.snapshot(25)["status"])
        self.assertIn("已被新的部队动作替换", model.describe_command(0,50)["effect"])
        self.assertIn("已到基地防守", model.describe_command(2,50)["effect"])

    def test_squad_action_keeps_main_task_and_scopes_conflict_evidence(self):
        model = self.model([
            {"event":"command", "schema":3, "frame":10, "key":"7", "mode":"all", "action":"retreat", "status":"applied", "task_id":1, "unit_count":20},
            {"event":"command", "schema":3, "frame":20, "key":"5", "mode":"squad", "action":"attack", "status":"applied", "task_id":2, "unit_count":5},
            {"event":"battle_order", "schema":3, "frame":30, "task_id":1, "conflict":True, "accepted":True, "verified":True},
            {"event":"battle_order", "schema":3, "frame":31, "task_id":2, "conflict":True, "accepted":False, "verified":False},
            {"event":"battle_state", "schema":3, "frame":40, "task_id":1, "units":list(range(15)), "phase":"safe"},
        ])
        main = model.describe_command(0,50)
        self.assertIn("安全撤退", main["title"])
        self.assertIn("已撤到安全位置", main["effect"])
        self.assertNotIn("替换", main["effect"])
        self.assertNotIn("下令失败", main["effect"])
        self.assertIn("1 次", main["effect"])
        self.assertIn("下令失败", model.describe_command(1,50)["effect"])
        self.assertNotIn("已撤到", model.describe_command(0,35)["effect"])

    def test_mode_rejection_and_lost_squad_are_not_reported_as_success(self):
        model = self.model([
            {"event":"command", "schema":3, "frame":10, "key":"4", "mode":"all", "action":"expansion", "status":"expansion_unknown", "task_id":0},
            {"event":"command", "schema":3, "frame":20, "key":"5", "mode":"squad", "action":"attack", "status":"squad_lost", "task_id":0},
        ])
        self.assertIn("尚未发现", model.describe_command(0,30)["title"])
        self.assertIn("原任务保持", model.describe_command(0,30)["effect"])
        self.assertIn("原小队已损失", model.describe_command(1,30)["title"])

    def test_enter_squad_recalls_reserves_and_repeated_key_keeps_members(self):
        model=self.model([
            {"event":"state", "schema":4, "frame":0, "mode":"all", "army":20, "squad_ids":[]},
            {"event":"command", "schema":4, "frame":10, "key":"2", "mode":"squad", "action":"split_squad", "status":"squad_split", "units":[1,2,3,4,5], "unit_count":5, "army_ids":list(range(1,21)), "squad_ids":[1,2,3,4,5], "task_id":2, "reserve_task":3, "reserve_units":list(range(6,21)), "main_task":3,"squad_task":2},
            {"event":"battle_state", "schema":4, "frame":20, "task_id":3, "action":"defend", "phase":"returning", "units":list(range(6,21))},
            {"event":"command", "schema":4, "frame":30, "key":"2", "mode":"squad", "action":"split_squad", "status":"mode_unchanged", "task_id":2, "main_task":3,"squad_task":2,"squad_ids":[1,2,3,4,5]},
            {"event":"battle_state", "schema":4, "frame":40, "task_id":3, "action":"defend", "phase":"guarding", "units":list(range(6,21))},
        ])
        early=model.snapshot(10)
        self.assertIn("当前模式：小队",early['mode'])
        self.assertIn("固定小队 5 名",early['status'])
        self.assertIn("主力任务：回防",early['status'])
        self.assertIn("其余 15 名回基地",model.describe_command(0,20)['effect'])
        self.assertIn("正在回防",model.describe_command(0,20)['effect'])
        self.assertIn("已到基地防守",model.describe_command(0,40)['effect'])
        self.assertIn("没有再次抽兵",model.describe_command(1,40)['effect'])

    def test_dynamic_squad_updates_between_samples_and_stops_at_global_reset(self):
        model=self.model([
            {"event":"start","frame":0,"squad_policy":"dynamic_quarter_v1"},
            {"event":"command","schema":5,"frame":10,"key":"2","mode":"squad","action":"split_squad","status":"squad_split",
             "units":[1,2],"squad_ids":[1,2],"reserve_units":list(range(3,9)),"army_ids":list(range(1,9)),"main_task":1,"squad_task":2},
            {"event":"squad_rebalance","frame":20,"army":12,"squad_ids":[1,2,9],"added":[9],"returned":[]},
            {"event":"command","schema":5,"frame":30,"key":"0","mode":"all","action":"restore_auto","status":"auto_restored",
             "army_ids":list(range(1,13)),"squad_ids":[],"main_task":0,"squad_task":0},
        ])
        self.assertIn("动态小队 2 名",model.snapshot(15)['status'])
        self.assertIn("动态小队 3 名",model.snapshot(25)['status'])
        self.assertIn("主力 9 / 部队 12",model.snapshot(25)['status'])
        self.assertIn("动态小队 0 名",model.snapshot(35)['status'])
        self.assertIn("自动增援及补损",model.describe_command(0,25)['effect'])

    def test_locked_raid_explains_no_chase_and_safe_wait_without_implying_main_attack(self):
        model=self.model([
            {"event":"command","schema":5,"frame":10,"key":"4","mode":"all","action":"expansion","status":"applied","task_id":1,"units":[1,2]},
            {"event":"battle_state","frame":20,"task_id":1,"action":"expansion","phase":"advancing","units":[1,2],"raid_policy":"locked_depot_no_chase_v1"},
            {"event":"expansion_complete","frame":30,"task_id":1,"reason":"depot_destroyed"},
            {"event":"battle_state","frame":40,"task_id":1,"action":"retreat","phase":"rally_waiting","units":[1,2],"raid_policy":"locked_depot_no_chase_v1","rendezvous_policy":"shared_safe_point_v1","auto_withdrawal":True},
        ])
        early=model.describe_command(0,25)['effect']
        self.assertIn("跑出射程就不追",early)
        self.assertNotIn("目标分矿基地已摧毁",early)
        late=model.describe_command(0,45)['effect']
        self.assertIn("目标分矿基地已摧毁",late)
        self.assertIn("安全点待命",late)
        self.assertIn("不继续进攻主基地",late)
        self.assertNotIn("继续原分矿任务",late)

    def test_dynamic_policy_is_identified_even_without_reinforcement_events(self):
        model=self.model([
            {"event":"start","frame":0,"squad_policy":"dynamic_quarter_v1"},
            {"event":"state","schema":5,"frame":120,"mode":"squad","army":8,"main_army":6,"squad_ids":[1,2]},
        ])
        self.assertIn("动态小队 2 名",model.snapshot(120)['status'])

    def test_same_frame_mode_navigation_does_not_show_unselected_future_action(self):
        model=self.model([
            {"event":"state", "schema":4,"frame":0,"mode":"all","army":20,"squad_ids":[]},
            {"event":"command","schema":4,"frame":10,"key":"3","mode":"all","action":"select_mode","status":"mode_selected","task_id":0,"main_task":0,"squad_task":0,"squad_ids":[]},
            {"event":"command","schema":4,"frame":10,"key":"2","mode":"squad","action":"split_squad","status":"squad_split","task_id":0,"main_task":1,"squad_task":0,"reserve_task":1,"squad_ids":[1,2],"units":[1,2],"reserve_units":[3,4]},
            {"event":"state","schema":4,"frame":10,"mode":"squad","army":4,"main_task":1,"squad_task":0,"squad_ids":[1,2]},
        ])
        first=model.snapshot(10,0)
        self.assertEqual(first['mode'],"当前模式：全队")
        self.assertIn("固定小队 0 名",first['status'])
        self.assertNotIn("主力任务",first['status'])
        self.assertIn("固定小队 2 名",model.snapshot(10,1)['status'])

    def test_run_first_flight_effect_and_shots_stay_at_their_game_frames(self):
        model=self.model([
            {"event":"command","schema":4,"frame":10,"key":"8","action":"defend","mode":"all","status":"applied","task_id":1,"unit_count":20},
            {"event":"battle_order","frame":10,"task_id":1,"flight_policy":"run_first","accepted":True,"verified":True},
            {"event":"battle_state","frame":20,"task_id":1,"action":"defend","phase":"returning","units":list(range(20)),"flight_policy":"run_first"},
            {"event":"flight_burst_end","frame":30,"task_id":1,"duration":5,"fired":True},
            {"event":"battle_state","frame":40,"task_id":1,"action":"defend","phase":"guarding","units":list(range(20)),"flight_policy":"run_first"},
        ])
        early=model.describe_command(0,20)
        self.assertIn("撤离优先",early['effect'])
        self.assertIn("不追敌",early['effect'])
        self.assertIn("正在回防",early['effect'])
        self.assertNotIn("已记录还击",early['effect'])
        self.assertIn("已记录还击 1 次",model.describe_command(0,35)['effect'])
        self.assertIn("已到基地防守",model.describe_command(0,45)['effect'])

    def test_threat_first_target_summary_does_not_reveal_future_choices(self):
        model=self.model([
            {"event":"command","schema":4,"frame":10,"key":"5","action":"attack","mode":"all","status":"applied","task_id":1,"unit_count":20},
            {"event":"target_selection","frame":20,"task_id":1,"selected":2,"candidates":[{"id":2,"type":162,"building":True,"active":True}]},
            {"event":"target_release","frame":30,"task_id":1},
            {"event":"target_selection","frame":40,"task_id":1,"selected":3,"candidates":[{"id":3,"type":64,"worker":True,"active":False}]},
        ])
        early=model.describe_command(0,25)['effect']
        self.assertIn("威胁优先",early)
        self.assertIn("炮台",early)
        self.assertNotIn("探机",early)
        self.assertNotIn("越界追击",early)
        late=model.describe_command(0,45)['effect']
        self.assertIn("探机",late)
        self.assertIn("越界追击 1 次",late)

    def test_panel_sync_collapse_seek_and_close(self):
        import tkinter as tk
        from native_replay import ReplayPanel
        self.model([
            {"event": "command", "frame": 100, "key": "5", "status": "applied"},
            {"event": "command", "frame": 200, "key": "7", "status": "applied"},
        ])
        root = tk.Tk()
        root.withdraw()
        try:
            with patch("native_replay.threading.Thread.start"):
                panel = ReplayPanel(root, self.replay, False, lambda _: None)
            panel.window.withdraw()
            panel.events.put(dict(phase="ready", frame=150, total=500, ended=False, seeking=False, paused=True))
            panel.poll()
            self.assertIn("5 · 攻主基地", panel.review_title.cget("text"))
            panel.window.update_idletasks()
            sidebar_size = (panel.window.winfo_width(), panel.window.winfo_height())
            panel.toggle_review()
            self.assertFalse(panel.review_expanded)
            panel.toggle_review()
            self.assertTrue(panel.review_expanded)
            self.assertEqual((panel.window.winfo_width(), panel.window.winfo_height()), sidebar_size)
            panel.toggle_controls_placement()
            self.assertTrue(panel.controls_on_right)
            self.assertTrue(panel.control_slot.winfo_manager())
            self.assertEqual(panel.control_row.master, panel.control_slot)
            self.assertEqual(panel.progress.get(), 150)
            panel.toggle_controls_placement()
            self.assertFalse(panel.controls_on_right)
            self.assertFalse(panel.control_slot.winfo_manager())
            self.assertEqual(panel.control_row.master, panel.controls_window)
            self.assertEqual(panel.progress.get(), 150)
            self.assertFalse(panel.session.fullscreen)
            panel.paused = True
            panel.jump_command(1)
            self.assertEqual(panel.session.commands.get_nowait(), ("seek", 200, True, 42))
            panel.events.put(dict(phase="ready", frame=202, total=500, ended=False, seeking=False, paused=True))
            panel.poll()
            self.assertFalse(panel.review_ready)
            panel.events.put(dict(phase="ready", frame=200, total=500, ended=False, seeking=False, paused=True))
            panel.poll()
            self.assertTrue(panel.review_ready)
            panel.jump_command(-1)
            self.assertEqual(panel.session.commands.get_nowait(), ("seek", 100, True, 42))
            panel.events.put(dict(phase="ready", frame=100, total=500, ended=False, seeking=False, paused=True))
            panel.poll()
            panel.progress.set(50)
            panel.seek()
            self.assertEqual(panel.session.commands.get_nowait(), ("seek", 50, True, 42))
            panel.events.put(dict(phase="ready", frame=50, total=500, ended=False, seeking=False, paused=True))
            panel.poll()
            self.assertIn("尚无", panel.review_title.cget("text"))
            panel.close()
            self.assertTrue(panel.session.stopping.is_set())
            self.assertEqual(panel.previous_command.cget("state"), "disabled")
            print("Panel requested size:", panel.panel_width, panel.window.winfo_reqheight())
            panel.controls_window.destroy()
            panel.window.destroy()
        finally:
            root.destroy()

    def test_sidebar_geometry_preserves_game_and_avoids_overlap(self):
        from replay_layout import sidebar_geometry
        position, sidebar = sidebar_geometry((0, 0, 1280, 760), (960, 720))
        self.assertEqual(position, (0, 20))
        self.assertEqual(sidebar, (968, 0, 304, 760))
        position, sidebar = sidebar_geometry((-1920, 0, 0, 1040), (960, 720))
        self.assertEqual(position[0], -1920)
        self.assertGreaterEqual(sidebar[0], position[0]+960)
        self.assertLessEqual(sidebar[0]+sidebar[2], 0)
        with self.assertRaises(RuntimeError):
            sidebar_geometry((0, 0, 1024, 768), (960, 720))

    def test_original_fullscreen_viewport_uses_physical_pixels(self):
        from replay_layout import original_replay_size
        # The current machine uses 200% DPI. The old large image must not be
        # replaced with the fixed 960x720 window (or mistaken logical pixels).
        size = original_replay_size(True)
        self.assertEqual(size[0]*3, size[1]*4)
        self.assertEqual(size[0] % 640, 0)
        self.assertEqual(original_replay_size(False), (960, 720))

    def test_navigation_keeps_distinct_actions_in_the_same_frame(self):
        model = self.model([
            {"event": "command", "frame": 10, "key": "1", "status": "applied"},
            {"event": "command", "frame": 10, "key": "5", "status": "applied"},
            {"event": "command", "frame": 11, "key": "7", "status": "applied"},
        ])
        self.assertEqual(model.neighbor_index(0, 1), 0)
        self.assertEqual(model.neighbor_index(10, 1, 0), 1)
        self.assertEqual(model.neighbor_index(10, -1, 1), 0)
        self.assertEqual(model.snapshot(10, 0)["count"], 1)
        self.assertIn("1 · 侦察", model.snapshot(10, 0)["recent"][-1]["title"])

    def test_pointer_guard_excludes_sidebar_overlays_and_other_apps(self):
        import replay_layout

        class Windows:
            x, y, foreground_pid, under = 400, 200, 42, 10

            def GetForegroundWindow(self):
                return 10

            def GetWindowThreadProcessId(self, hwnd, pid):
                pid._obj.value = self.foreground_pid

            def GetCursorPos(self, point):
                point._obj.x, point._obj.y = self.x, self.y
                return True

            def GetWindowRect(self, hwnd, rect):
                rect._obj.left, rect._obj.top, rect._obj.right, rect._obj.bottom = 0, 40, 960, 760
                return True

            def WindowFromPoint(self, point):
                return self.under

            def IsChild(self, parent, child):
                return False

        api = Windows()
        with patch.object(replay_layout.ctypes.windll, "user32", api):
            self.assertTrue(replay_layout.pointer_inside_game(10, 42))
            api.x = 1000
            self.assertFalse(replay_layout.pointer_inside_game(10, 42))
            api.x, api.under = 400, 20
            self.assertFalse(replay_layout.pointer_inside_game(10, 42))
            api.under, api.foreground_pid = 10, 99
            self.assertFalse(replay_layout.pointer_inside_game(10, 42))

    def test_abnormal_replay_exit_is_reported_and_saved(self):
        from native_replay import NativeReplay
        from unittest.mock import MagicMock
        session = NativeReplay(self.replay, False, lambda _: None)
        session.directory = self.root / "playback"
        state = {"frame": 120, "total": 1000, "ended": False}
        process = MagicMock(pid=123, returncode=13)
        process.poll.return_value = 13
        processes = MagicMock()
        processes.launch.return_value = process

        def prepare(slot, *args, **kwargs):
            (slot / "bwapi-data/read").mkdir(parents=True)
            return {}

        with patch("native_replay.game_lock", return_value=nullcontext()), \
             patch("native_replay.GameProcesses", return_value=processes), \
             patch("native_replay.prepare", side_effect=prepare), \
             patch("native_replay.read_json", side_effect=lambda path: state if path.name == "replay-status.json" else None):
            with self.assertRaisesRegex(RuntimeError, "异常退出"):
                session.run()
        records = [json.loads(line) for line in (session.directory / "session-events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertIn("process_exit", [r["event"] for r in records])
        self.assertEqual(records[-1]["event"], "error")
        self.assertEqual(records[-1]["exit_code"], 13)
        self.assertEqual(records[-1]["state"]["frame"], 120)
        processes.close.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)

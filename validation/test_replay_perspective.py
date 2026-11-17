import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from replay_perspective import read_camera, load_camera, camera_path
from native_replay import NativeReplay


class PerspectiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.replay = self.root / "match.rep"
        self.replay.write_bytes(b"test")

    def tearDown(self):
        self.temp.cleanup()

    def write_track(self, rows):
        path = camera_path(self.replay)
        path.write_text("\n".join(json.dumps(r) for r in rows)+"\n", encoding="utf-8")
        return path

    def track_rows(self):
        return [{"event": "camera_start", "schema": 1, "player_id": 1},
                {"event": "camera", "frame": 0, "x": 0, "y": 1024},
                {"event": "camera", "frame": 100, "x": 640, "y": 800},
                {"event": "camera_end", "frame": 200}]

    def test_old_replay_cannot_be_presented_as_original_view(self):
        self.assertIsNone(load_camera(self.replay))
        with patch("native_replay.load_labels", return_value={"self_player_id": 1}):
            with self.assertRaisesRegex(ValueError, "未记录"):
                NativeReplay(self.replay, False, lambda _: None, view_mode="self")
            self.assertEqual(NativeReplay(self.replay, False, lambda _: None).view_mode, "all")

    def test_incomplete_or_out_of_order_camera_is_rejected(self):
        rows = self.track_rows()
        self.assertIsNone(read_camera(self.write_track(rows[:-1])))
        rows[2]["frame"] = -1
        self.assertIsNone(read_camera(self.write_track(rows)))
        rows = self.track_rows()
        rows[1]["frame"] = 10
        self.assertIsNone(read_camera(self.write_track(rows)))

    def test_player_identity_must_match(self):
        self.write_track(self.track_rows())
        with patch("native_replay.load_labels", return_value={"self_player_id": 0}):
            with self.assertRaisesRegex(ValueError, "不一致"):
                NativeReplay(self.replay, False, lambda _: None, view_mode="self")
        with patch("native_replay.load_labels", return_value={"self_player_id": 1}):
            session = NativeReplay(self.replay, False, lambda _: None, view_mode="self")
            self.assertEqual(session.camera_track.player_id, 1)
            self.assertEqual(session.camera_track.samples[-1], [100, 640, 800])

    def test_match_save_preserves_portable_camera_file(self):
        from replay_info import save_match_labels
        source = self.root / "bwapi-data/write/camera-track.jsonl"
        source.parent.mkdir(parents=True)
        source.write_text("\n".join(json.dumps(r) for r in self.track_rows()), encoding="utf-8")
        identity = {"self_player_id": 1, "players": [{"id": 1, "name": "StarterBot"}]}
        (source.parent / "match-identity.json").write_text(json.dumps(identity), encoding="utf-8")
        status = {}
        save_match_labels(self.root, status, self.replay)
        self.assertTrue(status["camera_recorded"])
        self.assertEqual(load_camera(self.replay).samples[0], [0, 0, 1024])
        labels = json.loads(self.replay.with_suffix(".rep.json").read_text())
        self.assertTrue(labels["camera_recorded"])

    def test_new_free_camera_uses_identity_without_requiring_a_track(self):
        labels={"self_player_id":1,"self_replay_camera":"free"}
        with patch("native_replay.load_labels",return_value=labels):
            session=NativeReplay(self.replay,False,lambda _:None,view_mode="self")
            self.assertEqual(session.camera_mode,"free")
            self.assertIsNone(session.camera_track)
        with patch("native_replay.load_labels",return_value={"self_replay_camera":"free"}):
            with self.assertRaisesRegex(ValueError,"身份"):
                NativeReplay(self.replay,False,lambda _:None,view_mode="self")

    def test_new_match_flag_survives_portable_labels_old_match_stays_recorded(self):
        from replay_info import save_match_labels,load_labels
        identity={"self_player_id":1,"players":[{"id":1,"name":"StarterBot"}]}
        target=self.root/'bwapi-data/write/match-identity.json';target.parent.mkdir(parents=True)
        target.write_text(json.dumps(identity),encoding='utf-8')
        status={"self_replay_camera":"free","player_color_policy":"self_blue_enemy_red_v1"}
        save_match_labels(self.root,status,self.replay)
        self.assertEqual(load_labels(self.replay)['self_replay_camera'],'free')
        self.assertEqual(load_labels(self.replay)['player_color_policy'],'self_blue_enemy_red_v1')
        self.replay.with_suffix('.rep.json').unlink()
        self.write_track(self.track_rows())
        with patch("native_replay.load_labels",return_value={"self_player_id":1}):
            old=NativeReplay(self.replay,False,lambda _:None,view_mode="self")
            self.assertEqual(old.camera_mode,'recorded')
            self.assertIsNotNone(old.camera_track)


if __name__ == "__main__":
    unittest.main(verbosity=2)

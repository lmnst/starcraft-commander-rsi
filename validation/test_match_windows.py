import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app"))
from match_windows import viewport_rect

class GeometryTests(unittest.TestCase):
    def test_expanded_fullscreen_uses_physical_pixels_and_aspect_ratio(self):
        self.assertEqual(viewport_rect([0,0,2560,1600],(800,600)),[213,0,2346,1600])
        self.assertEqual(viewport_rect([-2560,0,0,1600],(800,600)),[-2347,0,-214,1600])
    def test_standard_integer_scaling_retains_original_behavior(self):
        self.assertEqual(viewport_rect([0,0,2560,1600],(640,480),True),[320,80,2240,1520])
    def test_window_origin_is_included_in_cursor_bounds(self):
        self.assertEqual(viewport_rect([100,200,1060,920],(800,600)),[100,200,1060,920])

if __name__=="__main__":unittest.main()

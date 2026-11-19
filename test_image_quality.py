import unittest
from unittest.mock import patch

import cv2
import numpy as np

from image_quality import final_decision, inspect_candidate, nearly_identical, refine_translation
from outline_matching import footprint_agreement
from shapely.geometry import box


class ImageQualityTests(unittest.TestCase):
    def setUp(self):
        self.coverage = np.full((256, 256), 255, np.uint8)
        self.polygon = [[200, 200], [500, 200], [500, 500], [200, 500]]

    def test_small_georeference_offset_is_corrected(self):
        rng = np.random.default_rng(7)
        current = rng.integers(0, 255, (256, 256, 3), dtype=np.uint8)
        old = cv2.warpAffine(current, np.float32([[1, 0, 5], [0, 1, -4]]), (256, 256))
        aligned, _, (dx, dy) = refine_translation(old, current, self.coverage)
        self.assertAlmostEqual(dx, -5, delta=1)
        self.assertAlmostEqual(dy, 4, delta=1)
        self.assertLess(np.mean(cv2.absdiff(aligned, current)), np.mean(cv2.absdiff(old, current)))

    def test_identical_imagery_is_not_a_change(self):
        image = np.full((256, 256, 3), 120, np.uint8)
        self.assertTrue(nearly_identical(image, image.copy(), self.coverage))

    def test_candidate_without_old_coverage_is_rejected(self):
        image = np.zeros((256, 256, 3), np.uint8)
        coverage = np.zeros((256, 256), np.uint8)
        self.assertEqual(inspect_candidate(self.polygon, image, image, coverage)[1], "reject")

    def test_unchanged_roof_is_marked_for_review(self):
        rng = np.random.default_rng(12)
        image = rng.integers(0, 255, (256, 256, 3), dtype=np.uint8)
        self.assertEqual(inspect_candidate(self.polygon, image, image, self.coverage)[1], "review")

    def test_new_structure_can_advance_to_second_review(self):
        old = np.full((256, 256, 3), 100, np.uint8)
        current = old.copy()
        cv2.rectangle(current, (52, 52), (127, 127), (40, 50, 80), -1)
        self.assertEqual(inspect_candidate(self.polygon, old, current, self.coverage)[1], "accept")

    def test_crossed_outline_is_rejected(self):
        image = np.zeros((256, 256, 3), np.uint8)
        crossed = [[200, 200], [500, 500], [500, 200], [200, 500]]
        self.assertEqual(inspect_candidate(crossed, image, image, self.coverage)[1], "reject")

    def test_second_pass_must_confirm_before_red_result(self):
        self.assertEqual(final_decision("accept", "", None)[0], "review")
        self.assertEqual(final_decision("review", "屋根が似ています", {"decision":"new", "change_kind":"rebuild"})[0], "review")
        self.assertEqual(final_decision("accept", "", {"decision":"new", "change_kind":"new_build"})[0], "new")
        self.assertEqual(final_decision("accept", "", {"decision":"new", "change_kind":"rebuild"})[0], "new")
        self.assertEqual(final_decision("accept", "", {"decision":"new", "change_kind":"demolition"})[0], "reject")
        self.assertEqual(final_decision("accept", "", {"decision":"new", "change_kind":"non_building"})[0], "reject")
        self.assertEqual(final_decision("accept", "", {"decision":"new"})[0], "review")
        self.assertEqual(final_decision("accept", "", {"decision":"reject", "reason":"既存の建物"})[0], "reject")

    def test_footprint_requires_substantial_overlap_on_both_sides(self):
        roof = box(0, 0, 10, 10)
        self.assertIsNotNone(footprint_agreement(roof, box(1, 1, 10, 10)))
        self.assertIsNone(footprint_agreement(roof, box(7, 0, 17, 10)))
        self.assertIsNone(footprint_agreement(roof, box(-10, -10, 20, 20)))

    def test_cut_off_building_is_rejected(self):
        image = np.zeros((256, 256, 3), np.uint8)
        self.assertEqual(inspect_candidate([[0, 200], [300, 200], [300, 500], [0, 500]], image, image, self.coverage)[1], "reject")

    def test_cells_overlap_without_increasing_image_extent(self):
        import app
        polygon = [[35.5, 137.8], [35.5, 137.804], [35.503, 137.804], [35.503, 137.8]]
        with app.app.test_client() as client, patch.object(app, 'exe') as executable, \
                patch.object(app.detection_store, 'start_run', return_value=1), \
                patch.object(app.threading, 'Thread') as thread:
            executable.is_file.return_value = True
            with client.session_transaction() as session:
                session['user_id'] = 1
                session['csrf'] = 'test'
            with patch.dict(app.state, {'status': 'idle', 'points': []}):
                response = client.post('/compare', json={'polygon': polygon, 'years': 15},
                                       headers={'X-CSRF-Token': 'test'})
                self.assertEqual(response.status_code, 200)
                cells = thread.call_args.kwargs['args'][1]
                height = 160 / 111320
                self.assertAlmostEqual(cells[0][2] - cells[0][0], height)
                self.assertTrue(any(a[1] < b[1] < a[3] for a in cells for b in cells if a[0] == b[0]))


if __name__ == "__main__":
    unittest.main()

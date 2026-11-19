import unittest

import cv2
import numpy as np

from image_quality import final_decision, inspect_candidate, nearly_identical, refine_translation


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
        self.assertEqual(final_decision("review", "屋根が似ています", {"decision":"new"})[0], "review")
        self.assertEqual(final_decision("accept", "", {"decision":"new", "reason":"両時期を比較"})[0], "new")
        self.assertEqual(final_decision("accept", "", {"decision":"reject", "reason":"既存の建物"})[0], "reject")


if __name__ == "__main__":
    unittest.main()

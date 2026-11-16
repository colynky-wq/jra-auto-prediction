import unittest

import add_oos_frame_numbers as frames


class JRAFrameAllocationTests(unittest.TestCase):
    def test_standard_field_sizes(self):
        self.assertEqual(frames.frame_sizes(5), [1, 1, 1, 1, 1])
        self.assertEqual(frames.frame_sizes(8), [1] * 8)
        self.assertEqual(frames.frame_sizes(9), [1] * 7 + [2])
        self.assertEqual(frames.frame_sizes(13), [1, 1, 1, 2, 2, 2, 2, 2])
        self.assertEqual(frames.frame_sizes(16), [2] * 8)
        self.assertEqual(frames.frame_sizes(17), [2] * 7 + [3])
        self.assertEqual(frames.frame_sizes(18), [2] * 6 + [3, 3])

    def test_known_13_horse_layout(self):
        expected = {
            1: 1, 2: 2, 3: 3,
            4: 4, 5: 4,
            6: 5, 7: 5,
            8: 6, 9: 6,
            10: 7, 11: 7,
            12: 8, 13: 8,
        }
        self.assertEqual(
            {h: frames.frame_for_horse(h, 13) for h in range(1, 14)},
            expected,
        )

    def test_18_horse_outer_frames_have_three(self):
        self.assertEqual(frames.frame_for_horse(12, 18), 6)
        self.assertEqual(frames.frame_for_horse(13, 18), 7)
        self.assertEqual(frames.frame_for_horse(15, 18), 7)
        self.assertEqual(frames.frame_for_horse(16, 18), 8)
        self.assertEqual(frames.frame_for_horse(18, 18), 8)

    def test_metadata_requires_complete_original_card(self):
        rows = [{'馬番': n} for n in range(1, 14)]
        self.assertEqual(frames.metadata_field_size(rows), 13)
        with self.assertRaises(ValueError):
            frames.metadata_field_size([{'馬番': 1}, {'馬番': 3}])

    def test_scratched_entry_can_still_define_original_card(self):
        rows = [
            {'馬番': 1, '発売対象': True},
            {'馬番': 2, '発売対象': False},
            {'馬番': 3, '発売対象': True},
            {'馬番': 4, '発売対象': True},
            {'馬番': 5, '発売対象': True},
            {'馬番': 6, '発売対象': True},
            {'馬番': 7, '発売対象': True},
            {'馬番': 8, '発売対象': True},
            {'馬番': 9, '発売対象': True},
        ]
        self.assertEqual(frames.metadata_field_size(rows), 9)
        self.assertEqual(frames.frame_for_horse(8, 9), 8)
        self.assertEqual(frames.frame_for_horse(9, 9), 8)


if __name__ == '__main__':
    unittest.main()

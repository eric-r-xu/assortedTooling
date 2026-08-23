import unittest
from unittest.mock import call, patch

import numpy as np
from PIL import Image

import oneWordSearch_autoplay as autoplay


class TestWhiteLetterIsolation(unittest.TestCase):
    def test_white_letters_are_dark_and_tile_colors_are_uniform(self):
        pixels = np.full((60, 60, 3), (25, 120, 205), dtype=np.uint8)
        pixels[12:48, 26:34] = (255, 255, 255)
        pixels[12:20, 17:43] = (255, 255, 255)

        cleaned = autoplay.clean_cell_for_ocr(Image.fromarray(pixels))
        values = np.array(cleaned)

        self.assertLess(values[120, 120], 20)
        self.assertGreater(values[20, 20], 235)

    def test_antialiased_white_edges_are_kept_on_colored_tile(self):
        pixels = np.full((50, 50, 3), (30, 105, 190), dtype=np.uint8)
        pixels[15:35, 23:27] = (255, 255, 255)
        pixels[15:35, 21:23] = (145, 180, 220)

        isolated = autoplay.isolate_white_letters(Image.fromarray(pixels))

        self.assertIsNotNone(isolated)
        isolated_values = np.array(isolated)
        self.assertEqual(isolated_values[25, 22], 0)
        self.assertEqual(isolated_values[5, 5], 255)

    def test_dark_letters_still_use_existing_cleanup(self):
        pixels = np.full((50, 50, 3), 245, dtype=np.uint8)
        pixels[10:40, 22:28] = 15
        image = Image.fromarray(pixels)

        self.assertIsNone(autoplay.isolate_white_letters(image))
        cleaned = np.array(autoplay.clean_cell_for_ocr(image, threshold=210))
        self.assertTrue(np.any(cleaned < 20))


class TestDarkGridSplitting(unittest.TestCase):
    def test_white_glyph_bands_define_row_major_dark_grid(self):
        pixels = np.full((520, 540, 3), (8, 35, 40), dtype=np.uint8)
        expected_x = [55, 160, 265, 370, 475]
        expected_y = [50, 150, 250, 350, 450]

        # These stand in for white glyphs. Their deliberately varied sizes
        # ensure detection uses five projection bands rather than fixed cells.
        for row, center_y in enumerate(expected_y):
            for col, center_x in enumerate(expected_x):
                half_w = 9 + (row + col) % 4
                half_h = 15 + (row * 2 + col) % 5
                pixels[
                    center_y - half_h:center_y + half_h + 1,
                    center_x - half_w:center_x + half_w + 1,
                ] = 255

        image = Image.fromarray(pixels)
        detected = autoplay.detect_dark_grid_centers(image)

        self.assertIsNotNone(detected)
        x_centers, y_centers = detected
        np.testing.assert_allclose(x_centers, expected_x, atol=1)
        np.testing.assert_allclose(y_centers, expected_y, atol=1)

        cells, screen_centers = autoplay.split_grid_into_cells(
            image,
            capture_region={"left": 100, "top": 200},
        )
        self.assertEqual([(row, col) for row, col, _ in cells], [
            (row, col)
            for row in range(autoplay.GRID_SIZE)
            for col in range(autoplay.GRID_SIZE)
        ])
        self.assertEqual(screen_centers[(0, 0)], (155, 250))
        self.assertEqual(screen_centers[(4, 4)], (575, 650))


class TestTORecognition(unittest.TestCase):
    def test_closed_ring_is_recognized_as_o_not_t(self):
        y, x = np.ogrid[:64, :64]
        radius_squared = (x - 31.5) ** 2 + (y - 31.5) ** 2
        ring = (radius_squared <= 31 ** 2) & (radius_squared >= 19 ** 2)

        with patch.object(
            autoplay,
            "get_normalized_letter_mask",
            return_value=(ring, 1.0),
        ):
            self.assertTrue(autoplay.looks_like_capital_o_misread_as_t(None))
            self.assertEqual(
                autoplay.correct_t_o_confusion(None, "T", 89),
                ("O", 96),
            )

    def test_real_t_is_not_changed(self):
        capital_t = np.zeros((64, 64), dtype=bool)
        capital_t[:12, :] = True
        capital_t[:, 27:37] = True

        with patch.object(
            autoplay,
            "get_normalized_letter_mask",
            return_value=(capital_t, 0.9),
        ):
            self.assertFalse(autoplay.looks_like_capital_o_misread_as_t(None))
            self.assertEqual(
                autoplay.correct_t_o_confusion(None, "T", 92),
                ("T", 92),
            )

    def test_template_first_t_prediction_is_shape_checked(self):
        with (
            patch.object(
                autoplay,
                "maybe_use_template_image_match_first",
                return_value=("T", 93),
            ),
            patch.object(
                autoplay,
                "looks_like_capital_o_misread_as_t",
                return_value=True,
            ),
        ):
            self.assertEqual(autoplay.ocr_single_letter(None), ("O", 96))

    def test_tesseract_t_prediction_is_shape_checked(self):
        with (
            patch.object(
                autoplay,
                "maybe_use_template_image_match_first",
                return_value=(None, None),
            ),
            patch.object(autoplay, "looks_like_capital_i", return_value=False),
            patch.object(autoplay, "ocr_attempt", return_value=("T", 91)),
            patch.object(
                autoplay,
                "looks_like_capital_o_misread_as_t",
                return_value=True,
            ),
        ):
            self.assertEqual(autoplay.ocr_single_letter(None), ("O", 96))


class TestPFRecognition(unittest.TestCase):
    def test_f_bars_are_not_mistaken_for_a_p_bowl(self):
        capital_f = np.zeros((64, 64), dtype=bool)
        capital_f[:, 0:14] = True
        capital_f[0:11, 0:60] = True
        capital_f[27:38, 0:52] = True

        with patch.object(
            autoplay,
            "get_normalized_letter_mask",
            return_value=(capital_f, 0.68),
        ):
            self.assertFalse(autoplay.looks_like_capital_p(None))
            self.assertTrue(autoplay.looks_like_capital_f(None))

    def test_p_has_a_right_side_bridge_between_its_bars(self):
        capital_p = np.zeros((64, 64), dtype=bool)
        capital_p[:, 0:14] = True
        capital_p[0:11, 0:57] = True
        capital_p[28:39, 0:57] = True
        capital_p[8:32, 50:64] = True

        with patch.object(
            autoplay,
            "get_normalized_letter_mask",
            return_value=(capital_p, 0.75),
        ):
            self.assertTrue(autoplay.looks_like_capital_p(None))
            self.assertFalse(autoplay.looks_like_capital_f(None))

class TestWordFindingAndTracing(unittest.TestCase):
    def test_finds_diagonal_laine_and_vertical_nurse(self):
        grid = [
            ["L", "?", "?", "?", "N"],
            ["?", "A", "?", "?", "U"],
            ["?", "?", "I", "?", "R"],
            ["?", "?", "?", "N", "S"],
            ["?", "?", "?", "?", "E"],
        ]

        found = {
            item["word"]: item["positions"]
            for item in autoplay.find_5_letter_words(grid)
        }

        self.assertEqual(
            found["LAINE"],
            [(0, 0), (1, 1), (2, 2), (3, 3), (4, 4)],
        )
        self.assertEqual(
            found["NURSE"],
            [(0, 4), (1, 4), (2, 4), (3, 4), (4, 4)],
        )

    def test_vertical_trace_is_one_continuous_mouse_gesture(self):
        positions = [(row, 4) for row in range(autoplay.GRID_SIZE)]
        centers = {(row, 4): (500, 100 + row * 80) for row in range(5)}
        word = {"word": "NURSE", "positions": positions}

        with (
            patch.object(autoplay.pyautogui, "moveTo") as move_to,
            patch.object(autoplay.pyautogui, "mouseDown") as mouse_down,
            patch.object(autoplay.pyautogui, "mouseUp") as mouse_up,
            patch.object(autoplay.pyautogui, "dragTo") as drag_to,
            patch.object(autoplay.time, "sleep"),
        ):
            autoplay.trace_word_on_screen(word, centers)

        self.assertEqual(mouse_down.call_args_list, [call(button="left")])
        self.assertEqual(mouse_up.call_args_list, [call(button="left")])
        drag_to.assert_not_called()
        self.assertEqual(move_to.call_args_list, [
            call(500, 100, duration=autoplay.TRACE_MOVE_DURATION),
            call(500, 180, duration=autoplay.TRACE_DRAG_DURATION),
            call(500, 260, duration=autoplay.TRACE_DRAG_DURATION),
            call(500, 340, duration=autoplay.TRACE_DRAG_DURATION),
            call(500, 420, duration=autoplay.TRACE_DRAG_DURATION),
        ])

if __name__ == "__main__":
    unittest.main()

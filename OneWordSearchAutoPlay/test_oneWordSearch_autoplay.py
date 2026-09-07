import os
import tempfile
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

    def test_scans_all_twelve_lines_in_both_directions_once(self):
        grid = [
            [f"{row}{col}" for col in range(autoplay.GRID_SIZE)]
            for row in range(autoplay.GRID_SIZE)
        ]

        with patch.object(autoplay, "is_english_word", return_value=True) as check:
            found = autoplay.find_5_letter_words(grid)

        self.assertEqual(len(autoplay.WORD_LINES), 12)
        self.assertEqual(len(found), 24)
        self.assertEqual(check.call_count, 24)
        self.assertEqual(
            [tuple(item["positions"]) for item in found],
            [
                positions
                for line in autoplay.WORD_LINES
                for positions in (line, tuple(reversed(line)))
            ],
        )

    def test_unknown_cells_skip_both_word_lookups_for_their_lines(self):
        grid = [["?"] * autoplay.GRID_SIZE for _ in range(autoplay.GRID_SIZE)]

        with patch.object(autoplay, "is_english_word") as check:
            self.assertEqual(autoplay.find_5_letter_words(grid), [])

        check.assert_not_called()

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


class TestFastTemplateMatching(unittest.TestCase):
    def test_margin_compares_different_letters_not_duplicate_templates(self):
        templates = [
            ("A", np.array([[1.0, 0.0]], dtype=np.float32), "a-best.png"),
            ("A", np.array([[0.999, 0.001]], dtype=np.float32), "a-near.png"),
            ("B", np.array([[0.90, 0.10]], dtype=np.float32), "b.png"),
        ]
        index = autoplay._build_backup_template_index(templates)
        query = np.array([[1.0, 0.0]], dtype=np.float32)

        with (
            patch.object(autoplay, "load_backup_template_index", return_value=index),
            patch.object(autoplay, "clean_cell_for_ocr", return_value=None),
            patch.object(
                autoplay,
                "normalize_image_for_backup_matching",
                return_value=query,
            ),
        ):
            letter, confidence, score, path = autoplay.match_cell_with_backup_images(
                None
            )

        self.assertEqual(letter, "A")
        self.assertEqual(confidence, 98)
        self.assertAlmostEqual(score, 1.0)
        self.assertEqual(path, "a-best.png")

    def test_inconclusive_template_first_result_is_reused_after_ocr(self):
        templates = [
            (
                "A",
                np.array([[0.75, np.sqrt(1.0 - 0.75 ** 2)]], dtype=np.float32),
                "a.png",
            ),
            (
                "B",
                np.array([[0.50, np.sqrt(1.0 - 0.50 ** 2)]], dtype=np.float32),
                "b.png",
            ),
        ]
        index = autoplay._build_backup_template_index(templates)
        query = np.array([[1.0, 0.0]], dtype=np.float32)
        processing_cache = {}

        with (
            patch.object(autoplay, "load_backup_template_index", return_value=index),
            patch.object(autoplay, "_get_cleaned_cell", return_value=None),
            patch.object(
                autoplay,
                "normalize_image_for_backup_matching",
                return_value=query,
            ) as normalize,
            patch("builtins.print"),
        ):
            self.assertEqual(
                autoplay.maybe_use_template_image_match_first(
                    None,
                    processing_cache=processing_cache,
                ),
                (None, None),
            )
            self.assertEqual(
                autoplay.maybe_use_backup_image_match(
                    None,
                    "?",
                    -1,
                    processing_cache=processing_cache,
                ),
                ("A", 75),
            )

        normalize.assert_called_once_with(None)


class TestSharedCellProcessing(unittest.TestCase):
    def test_normalized_mask_is_built_once_per_configuration(self):
        cleaned = Image.fromarray(np.full((240, 240), 255, dtype=np.uint8))
        processing_cache = {}

        with patch.object(
            autoplay,
            "clean_cell_for_ocr",
            return_value=cleaned,
        ) as clean:
            first = autoplay.get_normalized_letter_mask(
                None,
                processing_cache=processing_cache,
            )
            second = autoplay.get_normalized_letter_mask(
                None,
                processing_cache=processing_cache,
            )

        self.assertIs(first, second)
        clean.assert_called_once_with(None, threshold="otsu")


class TestRecognitionCache(unittest.TestCase):
    def tearDown(self):
        autoplay.clear_ocr_result_cache()

    def test_identical_pixels_reuse_the_recognition_result(self):
        first = Image.fromarray(np.full((20, 20, 3), 50, dtype=np.uint8))
        identical_copy = first.copy()

        with patch.object(
            autoplay,
            "_recognize_single_letter",
            return_value=("A", 95),
        ) as recognize:
            self.assertEqual(autoplay.ocr_single_letter(first), ("A", 95))
            self.assertEqual(
                autoplay.ocr_single_letter(identical_copy),
                ("A", 95),
            )

        recognize.assert_called_once()
        self.assertIs(recognize.call_args.args[0], first)

    def test_changed_glyph_is_recognized_again(self):
        first_pixels = np.full((40, 40, 3), (30, 105, 190), dtype=np.uint8)
        first_pixels[8:32, 18:22] = 255
        changed_pixels = np.full(
            (40, 40, 3),
            (30, 105, 190),
            dtype=np.uint8,
        )
        changed_pixels[18:22, 8:32] = 255
        first = Image.fromarray(first_pixels)
        changed = Image.fromarray(changed_pixels)

        with patch.object(
            autoplay,
            "_recognize_single_letter",
            side_effect=[("A", 95), ("B", 94)],
        ) as recognize:
            self.assertEqual(autoplay.ocr_single_letter(first), ("A", 95))
            self.assertEqual(autoplay.ocr_single_letter(changed), ("B", 94))

        self.assertEqual(recognize.call_count, 2)

    def test_same_glyph_on_different_tiles_reuses_normalized_result(self):
        first_pixels = np.full((50, 50, 3), (30, 105, 190), dtype=np.uint8)
        changed_tile_pixels = np.full(
            (50, 50, 3),
            (130, 60, 180),
            dtype=np.uint8,
        )
        first_pixels[12:38, 22:28] = 255
        changed_tile_pixels[12:38, 22:28] = 255

        with patch.object(
            autoplay,
            "_recognize_single_letter",
            return_value=("I", 99),
        ) as recognize:
            self.assertEqual(
                autoplay.ocr_single_letter(Image.fromarray(first_pixels)),
                ("I", 99),
            )
            self.assertEqual(
                autoplay.ocr_single_letter(Image.fromarray(changed_tile_pixels)),
                ("I", 99),
            )

        recognize.assert_called_once()


class TestOCRExecutor(unittest.TestCase):
    def tearDown(self):
        autoplay.close_ocr_executor()

    def test_executor_is_reused(self):
        first = autoplay.get_ocr_executor()
        second = autoplay.get_ocr_executor()

        self.assertIs(first, second)


class TestFailsafeCornerStop(unittest.TestCase):
    def test_corner_pointer_is_reported_regardless_of_traced_word(self):
        corner = autoplay.pyautogui.FAILSAFE_POINTS[0]

        with patch.object(autoplay.pyautogui, "position", return_value=corner):
            self.assertTrue(autoplay.mouse_in_failsafe_corner())

    def test_pointer_away_from_every_corner_does_not_stop(self):
        with patch.object(autoplay.pyautogui, "position", return_value=(400, 400)):
            self.assertFalse(autoplay.mouse_in_failsafe_corner())


class TestPersistentGlyphCache(unittest.TestCase):
    def setUp(self):
        autoplay.clear_ocr_result_cache()
        self._cache_file = tempfile.NamedTemporaryFile(
            suffix=".json",
            delete=False,
        )
        self._cache_file.close()
        self._patcher = patch.object(
            autoplay,
            "GLYPH_CACHE_PATH",
            self._cache_file.name,
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        os.unlink(self._cache_file.name)
        autoplay.clear_ocr_result_cache()

    def test_confident_results_survive_a_restart_and_weak_ones_do_not(self):
        strong_key = ((240, 240), b"\x01" * 16)
        weak_key = ((240, 240), b"\x02" * 16)

        autoplay._store_lru_result(
            autoplay._NORMALIZED_GLYPH_RESULT_CACHE,
            autoplay._NORMALIZED_GLYPH_RESULT_CACHE_LOCK,
            strong_key,
            ("A", 96),
            autoplay.NORMALIZED_GLYPH_CACHE_SIZE,
        )
        autoplay._store_lru_result(
            autoplay._NORMALIZED_GLYPH_RESULT_CACHE,
            autoplay._NORMALIZED_GLYPH_RESULT_CACHE_LOCK,
            weak_key,
            ("B", 40),
            autoplay.NORMALIZED_GLYPH_CACHE_SIZE,
        )

        autoplay.save_persistent_glyph_cache()
        autoplay.clear_ocr_result_cache()
        restored = autoplay.load_persistent_glyph_cache()

        self.assertEqual(restored, 1)
        self.assertEqual(
            autoplay._get_lru_result(
                autoplay._NORMALIZED_GLYPH_RESULT_CACHE,
                autoplay._NORMALIZED_GLYPH_RESULT_CACHE_LOCK,
                strong_key,
            ),
            ("A", 96),
        )
        self.assertIsNone(
            autoplay._get_lru_result(
                autoplay._NORMALIZED_GLYPH_RESULT_CACHE,
                autoplay._NORMALIZED_GLYPH_RESULT_CACHE_LOCK,
                weak_key,
            )
        )

    def test_a_version_bump_discards_the_old_file(self):
        key = ((240, 240), b"\x03" * 16)
        autoplay._store_lru_result(
            autoplay._NORMALIZED_GLYPH_RESULT_CACHE,
            autoplay._NORMALIZED_GLYPH_RESULT_CACHE_LOCK,
            key,
            ("C", 99),
            autoplay.NORMALIZED_GLYPH_CACHE_SIZE,
        )
        autoplay.save_persistent_glyph_cache()
        autoplay.clear_ocr_result_cache()

        with patch.object(
            autoplay,
            "GLYPH_CACHE_VERSION",
            autoplay.GLYPH_CACHE_VERSION + 1,
        ):
            self.assertEqual(autoplay.load_persistent_glyph_cache(), 0)

    def test_a_corrupt_file_is_ignored(self):
        with open(self._cache_file.name, "w", encoding="utf-8") as handle:
            handle.write("{not json")

        self.assertEqual(autoplay.load_persistent_glyph_cache(), 0)


class TestMouseMovement(unittest.TestCase):
    def setUp(self):
        self.region = {"left": 200, "top": 150, "width": 500, "height": 500}

    def test_already_safe_mouse_skips_move_and_settling_delay(self):
        with (
            patch.object(autoplay.pyautogui, "position", return_value=(900, 700)),
            patch.object(autoplay.pyautogui, "moveTo") as move_to,
            patch.object(autoplay.time, "sleep") as sleep,
        ):
            moved = autoplay.move_mouse_away_from_capture(self.region)

        self.assertFalse(moved)
        move_to.assert_not_called()
        sleep.assert_not_called()

    def test_mouse_inside_board_is_moved_and_given_time_to_clear(self):
        with (
            patch.object(autoplay.pyautogui, "position", return_value=(300, 300)),
            patch.object(autoplay.pyautogui, "size", return_value=(1440, 900)),
            patch.object(autoplay.pyautogui, "moveTo") as move_to,
            patch.object(autoplay.time, "sleep") as sleep,
        ):
            moved = autoplay.move_mouse_away_from_capture(self.region)

        self.assertTrue(moved)
        move_to.assert_called_once()
        sleep.assert_called_once_with(0.15)

if __name__ == "__main__":
    unittest.main()

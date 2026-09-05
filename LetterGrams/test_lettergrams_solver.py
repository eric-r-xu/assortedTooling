import io
import unittest
from unittest.mock import patch

from lettergrams_solver import DEFAULT_DICTIONARY_PATH, find_dictionary, score_board, solve


class ScoreBoardTests(unittest.TestCase):
    def test_crossing_bonus_counts_for_both_words(self):
        # CAT across and CAR down cross at C on a double-letter square.
        board = ("C", "A", "T", "A", "", "", "R", "", "")
        result = score_board(board, 3, {(0, 0): "DL"}, {"CAT", "CAR"})
        self.assertIsNotNone(result)
        self.assertEqual(result.score, 16)  # (C*2+A+T) + (C*2+A+R)

    def test_rejects_invalid_cross_word(self):
        board = ("C", "A", "T", "A", "", "", "R", "", "")
        self.assertIsNone(score_board(board, 3, {}, {"CAT"}))

    def test_rejects_isolated_tile(self):
        board = ("C", "A", "T", "", "", "", "", "", "Q")
        self.assertIsNone(score_board(board, 3, {}, {"CAT"}))

    def test_solver_uses_every_tile(self):
        results, _expanded, _seen = solve(
            "CAT", 3, {(0, 0): "DW"}, {"CAT"}, ["CAT"],
            beam_width=100, time_limit=1, result_count=1, exhaustive=False,
        )
        self.assertEqual(results[0].score, 10)
        self.assertEqual(sum(bool(cell) for cell in results[0].board), 3)

    def test_solver_periodic_thinking_logs(self):
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            solve(
                "CAT", 3, {(0, 0): "DW"}, {"CAT"}, ["CAT"],
                beam_width=100, time_limit=1, result_count=1, exhaustive=False,
                log_interval=0.000001,
            )
            self.assertIn("Thinking...", mock_stdout.getvalue())

    def test_find_dictionary_default(self):
        path = find_dictionary(None)
        self.assertEqual(path, DEFAULT_DICTIONARY_PATH)
        self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()


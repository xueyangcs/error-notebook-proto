"""Unit tests for grading JSON parse + light validation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from schema import ValidationError, parse_model_json, validate

VALID = {
    "problem_text": "求 2x+1=7",
    "student_solution": "解: x=3",
    "correctness": "correct",
    "score_hint": 100,
    "error_spans": [],
    "step_critique": [{"step": 1, "comment": "移项正确", "ok": True}],
    "correct_solution_sketch": "2x+1=7 → 2x=6 → x=3",
    "confidence": 0.95,
    "notes": "印刷题干、仿手写作答",
}


class TestValidate(unittest.TestCase):
    def test_accepts_valid_sample(self) -> None:
        result = validate(dict(VALID))
        self.assertEqual(result["correctness"], "correct")
        self.assertEqual(result["score_hint"], 100)
        self.assertEqual(len(result["step_critique"]), 1)
        self.assertTrue(result["step_critique"][0]["ok"])

    def test_rejects_missing_correctness(self) -> None:
        data = dict(VALID)
        del data["correctness"]
        with self.assertRaises(ValidationError) as ctx:
            validate(data)
        self.assertIn("correctness", str(ctx.exception))

    def test_rejects_invalid_correctness(self) -> None:
        data = dict(VALID)
        data["correctness"] = "maybe"
        with self.assertRaises(ValidationError):
            validate(data)

    def test_normalizes_correctness_case(self) -> None:
        data = dict(VALID)
        data["correctness"] = "PARTIAL"
        self.assertEqual(validate(data)["correctness"], "partial")


class TestParseModelJson(unittest.TestCase):
    def test_strips_markdown_fences(self) -> None:
        raw = "```json\n" + __import__("json").dumps(VALID, ensure_ascii=False) + "\n```"
        parsed = parse_model_json(raw)
        self.assertEqual(parsed["correctness"], "correct")

    def test_rejects_non_json(self) -> None:
        with self.assertRaises(ValidationError):
            parse_model_json("not json at all")


if __name__ == "__main__":
    unittest.main()

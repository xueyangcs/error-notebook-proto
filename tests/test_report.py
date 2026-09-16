"""Unit tests for Markdown report rendering."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from report import render_markdown

SAMPLE = {
    "problem_text": "求 2x+1=7",
    "student_solution": "解: x=3",
    "correctness": "correct",
    "score_hint": 100,
    "error_spans": [],
    "step_critique": [{"step": 1, "comment": "移项正确", "ok": True}],
    "correct_solution_sketch": "2x+1=7 → 2x=6 → x=3",
    "confidence": 0.95,
    "notes": "印刷题干",
}


class TestRenderMarkdown(unittest.TestCase):
    def test_contains_sections(self) -> None:
        md = render_markdown(SAMPLE)
        for heading in ("题干", "学生作答", "判定", "分步点评", "参考解法", "置信度"):
            self.assertIn(heading, md)
        self.assertIn("求 2x+1=7", md)
        self.assertIn("正确", md)
        self.assertIn("95%", md)


if __name__ == "__main__":
    unittest.main()

"""Render validated grading results as human-readable Markdown."""

from __future__ import annotations

from typing import Any

CORRECTNESS_LABELS = {
    "correct": "正确",
    "partial": "部分正确",
    "incorrect": "错误",
    "unknown": "无法判断",
}


def _escape_md_block(text: str) -> str:
    """Keep body text readable; preserve newlines as Markdown paragraphs."""
    return (text or "").strip() or "（无）"


def render_markdown(result: dict[str, Any]) -> str:
    """Convert a validated grading dict into a Chinese Markdown report."""
    correctness = str(result.get("correctness", "unknown"))
    label = CORRECTNESS_LABELS.get(correctness, correctness)
    score = result.get("score_hint")
    score_text = "—" if score is None else f"{score}"
    confidence = result.get("confidence", 0.0)
    try:
        conf_pct = f"{float(confidence) * 100:.0f}%"
    except (TypeError, ValueError):
        conf_pct = str(confidence)

    lines: list[str] = []
    lines.append("# 作业批改报告")
    lines.append("")
    lines.append("## 题干")
    lines.append("")
    lines.append(_escape_md_block(str(result.get("problem_text", ""))))
    lines.append("")
    lines.append("## 学生作答")
    lines.append("")
    lines.append(_escape_md_block(str(result.get("student_solution", ""))))
    lines.append("")
    lines.append("## 判定")
    lines.append("")
    lines.append(f"- **对错**：{label}（`{correctness}`）")
    lines.append(f"- **分数提示**：{score_text}")
    lines.append("")

    error_spans = result.get("error_spans") or []
    if error_spans:
        lines.append("### 错误点")
        lines.append("")
        for span in error_spans:
            lines.append(f"- {span}")
        lines.append("")

    lines.append("## 分步点评")
    lines.append("")
    steps = result.get("step_critique") or []
    if not steps:
        lines.append("（无分步点评）")
        lines.append("")
    else:
        for item in steps:
            step_no = item.get("step", "?")
            ok = item.get("ok", False)
            mark = "✓" if ok else "✗"
            comment = str(item.get("comment", "")).strip() or "（无点评）"
            lines.append(f"{step_no}. [{mark}] {comment}")
        lines.append("")

    lines.append("## 参考解法")
    lines.append("")
    sketch = _escape_md_block(str(result.get("correct_solution_sketch", "")))
    lines.append(sketch)
    lines.append("")
    lines.append("## 置信度")
    lines.append("")
    lines.append(f"{conf_pct}（{confidence}）")
    lines.append("")

    notes = str(result.get("notes", "")).strip()
    if notes:
        lines.append("## 备注")
        lines.append("")
        lines.append(notes)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"

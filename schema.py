"""Lightweight grading JSON schema: parse, coerce, and validate."""

from __future__ import annotations

import json
from typing import Any

CORRECTNESS_VALUES = frozenset({"correct", "partial", "incorrect", "unknown"})

REQUIRED_FIELDS = (
    "problem_text",
    "student_solution",
    "correctness",
    "score_hint",
    "error_spans",
    "step_critique",
    "correct_solution_sketch",
    "confidence",
    "notes",
)


class ValidationError(ValueError):
    """Raised when a grading payload is missing fields or has invalid types."""


def extract_json_text(raw: str) -> str:
    """Strip markdown fences / leading prose and return the JSON object text."""
    text = (raw or "").strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        text = text[first_nl + 1 :] if first_nl != -1 else text.lstrip("`")
        stripped = text.rstrip()
        if stripped.endswith("```"):
            text = stripped[: stripped.rfind("```")]
    text = text.strip()
    if text and not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
    return text


def parse_model_json(raw: str) -> dict[str, Any]:
    """Parse model output as JSON, retrying once after fence stripping."""
    if raw is None:
        raise ValidationError("empty model response")
    candidates = [raw.strip()]
    stripped = extract_json_text(raw)
    if stripped and stripped not in candidates:
        candidates.append(stripped)

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if not isinstance(data, dict):
            last_error = ValidationError("grading result must be a JSON object")
            continue
        return data

    detail = f": {last_error}" if last_error else ""
    raise ValidationError(f"model output is not valid JSON{detail}")


def _as_str(value: Any, field: str) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        raise ValidationError(f"{field} must be a string")
    return str(value)


def _as_score_hint(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValidationError("score_hint must be 0-100 or null")
    if isinstance(value, (int, float)):
        score = int(round(float(value)))
        if score < 0 or score > 100:
            raise ValidationError("score_hint must be 0-100 or null")
        return score
    raise ValidationError("score_hint must be 0-100 or null")


def _as_confidence(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, bool):
        raise ValidationError("confidence must be a number between 0 and 1")
    try:
        conf = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("confidence must be a number between 0 and 1") from exc
    if conf < 0:
        return 0.0
    if conf > 1:
        return 1.0
    return conf


def _as_error_spans(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if not isinstance(value, list):
        raise ValidationError("error_spans must be a list of strings")
    return [str(item) for item in value]


def _as_step_critique(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError("step_critique must be a list")
    steps: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValidationError("each step_critique item must be an object")
        step_no = item.get("step", index)
        try:
            step_no = int(step_no)
        except (TypeError, ValueError) as exc:
            raise ValidationError("step_critique.step must be an integer") from exc
        ok = item.get("ok", False)
        if not isinstance(ok, bool):
            ok = str(ok).strip().lower() in {"true", "1", "yes", "ok"}
        steps.append(
            {
                "step": step_no,
                "comment": _as_str(item.get("comment", ""), "step_critique.comment"),
                "ok": ok,
            }
        )
    return steps


def validate(data: dict[str, Any]) -> dict[str, Any]:
    """Light-validate and normalize a grading payload. Raises ValidationError."""
    if not isinstance(data, dict):
        raise ValidationError("grading result must be a JSON object")

    missing = [field for field in REQUIRED_FIELDS if field not in data]
    if missing:
        raise ValidationError("missing fields: " + ", ".join(missing))

    correctness = str(data["correctness"]).strip().lower()
    if correctness not in CORRECTNESS_VALUES:
        allowed = "|".join(sorted(CORRECTNESS_VALUES))
        raise ValidationError(f"correctness must be one of {allowed}")

    return {
        "problem_text": _as_str(data["problem_text"], "problem_text"),
        "student_solution": _as_str(data["student_solution"], "student_solution"),
        "correctness": correctness,
        "score_hint": _as_score_hint(data["score_hint"]),
        "error_spans": _as_error_spans(data["error_spans"]),
        "step_critique": _as_step_critique(data["step_critique"]),
        "correct_solution_sketch": _as_str(
            data["correct_solution_sketch"], "correct_solution_sketch"
        ),
        "confidence": _as_confidence(data["confidence"]),
        "notes": _as_str(data["notes"], "notes"),
    }

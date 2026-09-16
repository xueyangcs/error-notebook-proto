#!/usr/bin/env python3
"""Grade a K-12 homework photo with Gemini and write structured JSON / Markdown."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from prompts import SYSTEM_PROMPT, build_user_prompt
from report import render_markdown
from schema import ValidationError, parse_model_json, validate

DEFAULT_MODEL = "gemini-3.5-flash-lite"
FALLBACK_MODELS = (
    "gemini-2.5-flash",
    "gemini-flash-latest",
    "gemini-2.0-flash",
)
LEVELS = ("小学", "初中", "高中")
MIME_BY_SUFFIX = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从作业照片生成结构化批改报告（Gemini 多模态）"
    )
    parser.add_argument("image", type=Path, help="作业图片路径（jpg/png/webp 等）")
    parser.add_argument(
        "--level",
        choices=LEVELS,
        default="初中",
        help="学段（默认：初中）",
    )
    parser.add_argument("--subject", default="数学", help="学科（默认：数学）")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Gemini 模型名（默认：{DEFAULT_MODEL}）",
    )
    parser.add_argument(
        "--format",
        choices=("md", "json"),
        default="md",
        dest="fmt",
        help="输出格式（默认：md；始终写入 out/<stem>.md，json 时也写入 .json）",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("out"),
        help="结果输出目录（默认：out/）",
    )
    return parser.parse_args(argv)


def mime_type_for(path: Path) -> str:
    return MIME_BY_SUFFIX.get(path.suffix.lower(), "image/jpeg")


def require_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        print(
            "缺少 GEMINI_API_KEY。请到 Google AI Studio 免费申请：\n"
            "  https://aistudio.google.com/apikey\n"
            "然后执行：\n"
            "  export GEMINI_API_KEY='你的密钥'\n"
            "密钥只从环境变量读取，不要写入仓库。",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return key


def _model_chain(primary: str) -> list[str]:
    chain = [primary]
    for name in FALLBACK_MODELS:
        if name not in chain:
            chain.append(name)
    return chain


def call_gemini(
    *,
    api_key: str,
    model: str,
    image_bytes: bytes,
    mime_type: str,
    user_prompt: str,
) -> str:
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        print(
            "未安装 google-genai。请先运行：pip install -r requirements.txt",
            file=sys.stderr,
        )
        raise SystemExit(1)

    client = genai.Client(api_key=api_key)
    last_error: Exception | None = None
    for model_id in _model_chain(model):
        try:
            response = client.models.generate_content(
                model=model_id,
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                    user_prompt,
                ],
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    temperature=0.2,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(
                        disable=True
                    ),
                ),
            )
        except Exception as exc:  # noqa: BLE001 — try next model
            last_error = exc
            print(f"模型 {model_id} 调用失败，尝试备选…", file=sys.stderr)
            continue

        text = getattr(response, "text", None)
        if not text:
            last_error = RuntimeError("empty response")
            print(f"模型 {model_id} 返回空内容，尝试备选…", file=sys.stderr)
            continue
        if model_id != model:
            print(f"已使用备选模型：{model_id}", file=sys.stderr)
        return text

    print(f"Gemini API 调用失败：{last_error}", file=sys.stderr)
    raise SystemExit(1)


def grade_bytes(
    *,
    image_bytes: bytes,
    mime_type: str,
    level: str,
    subject: str,
    model: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Call Gemini (JSON), validate, return normalized grading dict."""
    key = api_key if api_key is not None else require_api_key()
    if not image_bytes:
        raise ValueError("图片为空")
    raw = call_gemini(
        api_key=key,
        model=model or DEFAULT_MODEL,
        image_bytes=image_bytes,
        mime_type=mime_type,
        user_prompt=build_user_prompt(level, subject),
    )
    payload = parse_model_json(raw)
    return validate(payload)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    image_path: Path = args.image
    if not image_path.is_file():
        print(f"找不到图片：{image_path}", file=sys.stderr)
        return 1

    api_key = require_api_key()
    image_bytes = image_path.read_bytes()
    if not image_bytes:
        print(f"图片为空：{image_path}", file=sys.stderr)
        return 1

    out_dir: Path = args.out_dir
    stem = image_path.stem
    raw_path = out_dir / f"{stem}.raw.txt"
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"

    raw = call_gemini(
        api_key=api_key,
        model=args.model,
        image_bytes=image_bytes,
        mime_type=mime_type_for(image_path),
        user_prompt=build_user_prompt(args.level, args.subject),
    )

    try:
        payload = parse_model_json(raw)
        result = validate(payload)
    except (ValidationError, json.JSONDecodeError) as exc:
        write_text(raw_path, raw)
        print(
            f"无法解析或校验模型输出：{exc}\n原始文本已写入 {raw_path}",
            file=sys.stderr,
        )
        return 1

    md_text = render_markdown(result)
    write_text(md_path, md_text)

    # Always write .md; also write .json when --format json (and keep .json for md too
    # as optional convenience — goal says "and optionally .json").
    if args.fmt == "json":
        write_text(json_path, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(f"已写入 {md_path}")
        print(f"已写入 {json_path}")
    else:
        print(f"已写入 {md_path}")

    print(
        f"correctness={result['correctness']}  "
        f"confidence={result['confidence']}  "
        f"score_hint={result['score_hint']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

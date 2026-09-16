#!/usr/bin/env python3
"""Minimal FastAPI UI for homework photo grading."""

from __future__ import annotations

import html
import json
import os
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, Response

from grade_image import DEFAULT_MODEL, LEVELS, grade_bytes, mime_type_for
from report import render_markdown
from schema import ValidationError

try:
    import markdown as md_lib
except ImportError:  # pragma: no cover
    md_lib = None

app = FastAPI(title="作业批改原型", description="上传作业照片，生成批改报告")

INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>作业批改</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 40rem; margin: 2rem auto; padding: 0 1rem; }
    label { display: block; margin-top: 1rem; font-weight: 600; }
    input, select, button { margin-top: 0.35rem; font-size: 1rem; }
    button { margin-top: 1.5rem; padding: 0.5rem 1.2rem; cursor: pointer; }
    .hint { color: #555; font-size: 0.9rem; }
  </style>
</head>
<body>
  <h1>作业照片批改</h1>
  <p class="hint">上传含题干与学生作答的图片，系统将调用 Gemini 生成批改报告。</p>
  <form action="/grade" method="post" enctype="multipart/form-data">
    <label>作业图片
      <input type="file" name="image" accept="image/*" required />
    </label>
    <label>学段
      <select name="level">
        <option value="小学">小学</option>
        <option value="初中" selected>初中</option>
        <option value="高中">高中</option>
      </select>
    </label>
    <label>学科
      <input type="text" name="subject" value="数学" />
    </label>
    <button type="submit">开始批改</button>
  </form>
</body>
</html>
"""


def _md_to_html(md_text: str) -> str:
    if md_lib is not None:
        body = md_lib.markdown(md_text, extensions=["fenced_code", "tables", "nl2br"])
    else:
        body = "<pre>" + html.escape(md_text) + "</pre>"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>批改结果</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 48rem; margin: 2rem auto; padding: 0 1rem; line-height: 1.55; }}
    pre, code {{ background: #f4f4f4; padding: 0.2em 0.4em; border-radius: 4px; }}
    pre {{ padding: 0.75rem; overflow-x: auto; }}
    a {{ color: #06c; }}
  </style>
</head>
<body>
  <p><a href="/">← 返回上传</a></p>
  {body}
</body>
</html>
"""


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX_HTML


@app.post("/grade")
async def grade(
    image: Annotated[UploadFile, File()],
    level: Annotated[str, Form()] = "初中",
    subject: Annotated[str, Form()] = "数学",
    format: Annotated[str, Form()] = "html",
) -> Response:
    if level not in LEVELS:
        level = "初中"
    data = await image.read()
    if not data:
        return HTMLResponse("<p>图片为空</p><p><a href='/'>返回</a></p>", status_code=400)

    filename = image.filename or "upload.jpg"
    mime = image.content_type or mime_type_for(Path(filename))
    try:
        result = grade_bytes(
            image_bytes=data,
            mime_type=mime,
            level=level,
            subject=subject or "数学",
            model=os.environ.get("GEMINI_MODEL", DEFAULT_MODEL),
        )
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 500
        msg = "缺少 GEMINI_API_KEY" if code == 2 else "批改服务调用失败"
        return HTMLResponse(
            f"<p>{html.escape(msg)}</p><p><a href='/'>返回</a></p>", status_code=502
        )
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        return HTMLResponse(
            f"<p>结果校验失败：{html.escape(str(exc))}</p><p><a href='/'>返回</a></p>",
            status_code=502,
        )
    except Exception as exc:  # noqa: BLE001
        return HTMLResponse(
            f"<p>批改失败：{html.escape(str(exc))}</p><p><a href='/'>返回</a></p>",
            status_code=502,
        )

    md_text = render_markdown(result)
    if format in ("md", "markdown", "text/markdown"):
        return Response(content=md_text, media_type="text/markdown; charset=utf-8")
    return HTMLResponse(_md_to_html(md_text))


def main() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run("web_app:app", host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    main()

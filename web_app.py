#!/usr/bin/env python3
"""Minimal FastAPI UI for homework photo grading."""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import threading
import time
from pathlib import Path
from typing import Annotated, Callable

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response

from grade_image import DEFAULT_MODEL, LEVELS, grade_bytes, mime_type_for
from report import render_markdown
from schema import ValidationError

try:
    import markdown as md_lib
except ImportError:  # pragma: no cover
    md_lib = None

MINUTE_SECONDS = 60
HOUR_SECONDS = 3600
DAY_SECONDS = 86400
MINUTE_LIMIT = 3
HOUR_LIMIT = 10
DAY_LIMIT = 20
COOKIE_NAME = "homework_access"

app = FastAPI(title="作业批改原型", description="上传作业照片，生成批改报告")

PAGE_CSS = """
    body { font-family: system-ui, sans-serif; max-width: 40rem; margin: 2rem auto; padding: 0 1rem; }
    label { display: block; margin-top: 1rem; font-weight: 600; }
    input, select, button { margin-top: 0.35rem; font-size: 1rem; }
    button { margin-top: 1.5rem; padding: 0.5rem 1.2rem; cursor: pointer; }
    .hint { color: #555; font-size: 0.9rem; }
    .err { color: #b00020; }
    .quota { margin: 1rem 0 1.25rem; padding: 0.75rem 1rem; background: #f6f7f9; border-radius: 8px; }
    .qtext { margin: 0 0 0.6rem; font-size: 0.9rem; color: #333; }
    .qbar { display: flex; align-items: center; gap: 0.5rem; margin-top: 0.35rem; font-size: 0.8rem; color: #555; }
    .qlab { width: 3.2rem; flex-shrink: 0; }
    .qnum { width: 3.2rem; text-align: right; font-variant-numeric: tabular-nums; }
    .qtrack { flex: 1; height: 6px; background: #e0e3e8; border-radius: 99px; overflow: hidden; }
    .qtrack i { display: block; height: 100%; background: #3b82f6; border-radius: 99px; }
    a { color: #06c; }
"""

RESULT_CSS = """
    body { font-family: system-ui, sans-serif; max-width: 48rem; margin: 2rem auto; padding: 0 1rem; line-height: 1.55; }
    pre, code { background: #f4f4f4; padding: 0.2em 0.4em; border-radius: 4px; }
    pre { padding: 0.75rem; overflow-x: auto; }
    a { color: #06c; }
    .quota { margin: 1rem 0 1.25rem; padding: 0.75rem 1rem; background: #f6f7f9; border-radius: 8px; }
    .qtext { margin: 0 0 0.6rem; font-size: 0.9rem; color: #333; }
    .qbar { display: flex; align-items: center; gap: 0.5rem; margin-top: 0.35rem; font-size: 0.8rem; color: #555; }
    .qlab { width: 3.2rem; flex-shrink: 0; }
    .qnum { width: 3.2rem; text-align: right; font-variant-numeric: tabular-nums; }
    .qtrack { flex: 1; height: 6px; background: #e0e3e8; border-radius: 99px; overflow: hidden; }
    .qtrack i { display: block; height: 100%; background: #3b82f6; border-radius: 99px; }
    .err { color: #b00020; }
"""


class GlobalRateLimiter:
    """Process-wide rolling windows shared by every client."""

    def __init__(
        self,
        clock: Callable[[], float] | None = None,
        *,
        minute_limit: int = MINUTE_LIMIT,
        hour_limit: int = HOUR_LIMIT,
        day_limit: int = DAY_LIMIT,
    ) -> None:
        self._clock = clock or time.time
        self._minute_limit = minute_limit
        self._hour_limit = hour_limit
        self._day_limit = day_limit
        self._lock = threading.Lock()
        self._hits: list[float] = []

    def snapshot(self) -> dict[str, dict[str, int]]:
        with self._lock:
            return self._snapshot_unlocked(self._clock())

    def try_acquire(self) -> tuple[bool, dict[str, dict[str, int]], str | None]:
        with self._lock:
            now = self._clock()
            snap = self._snapshot_unlocked(now)
            if snap["minute"]["remaining"] <= 0:
                return False, snap, f"已达到每分钟批改上限（{self._minute_limit} 次/分），请稍后再试。"
            if snap["hour"]["remaining"] <= 0:
                return False, snap, f"已达到每小时批改上限（{self._hour_limit} 次/时），请稍后再试。"
            if snap["day"]["remaining"] <= 0:
                return False, snap, f"已达到每天批改上限（{self._day_limit} 次/天），请稍后再试。"
            self._hits.append(now)
            return True, self._snapshot_unlocked(now), None

    def _snapshot_unlocked(self, now: float) -> dict[str, dict[str, int]]:
        self._hits = [t for t in self._hits if now - t < DAY_SECONDS]

        def bucket(limit: int, window: int) -> dict[str, int]:
            used = sum(1 for t in self._hits if now - t < window)
            remaining = max(0, limit - used)
            return {"used": used, "limit": limit, "remaining": remaining}

        return {
            "minute": bucket(self._minute_limit, MINUTE_SECONDS),
            "hour": bucket(self._hour_limit, HOUR_SECONDS),
            "day": bucket(self._day_limit, DAY_SECONDS),
        }


_limiter = GlobalRateLimiter()


def _access_password() -> str:
    return os.environ.get("ACCESS_PASSWORD") or ""


def auth_cookie_value(password: str | None = None) -> str:
    secret = password if password is not None else _access_password()
    return hashlib.sha256(f"homework-grade:{secret}".encode("utf-8")).hexdigest()


def _passwords_equal(given: str, expected: str) -> bool:
    given_b = given.encode("utf-8")
    expected_b = expected.encode("utf-8")
    if len(given_b) != len(expected_b):
        return False
    return hmac.compare_digest(given_b, expected_b)


def check_access(password: str | None, cookie: str | None) -> bool:
    expected = _access_password()
    if not expected:
        return False
    if cookie and _passwords_equal(cookie, auth_cookie_value(expected)):
        return True
    if password and _passwords_equal(password, expected):
        return True
    return False


def gate_grading_request(
    password: str | None,
    cookie: str | None,
    limiter: GlobalRateLimiter | None = None,
) -> tuple[int, str | None, dict[str, dict[str, int]]]:
    """Password first, then global quota. Failed password does not consume quota."""
    limiter = limiter or _limiter
    snap = limiter.snapshot()
    if not _access_password():
        return 503, "服务未配置访问密码，请联系管理员。", snap
    if not check_access(password, cookie):
        return 401, "密码错误，无法批改。", snap
    ok, snap, msg = limiter.try_acquire()
    if not ok:
        return 429, msg, snap
    return 200, None, snap


def quota_text(snap: dict[str, dict[str, int]]) -> str:
    return (
        f"剩余 {snap['minute']['remaining']}/{snap['minute']['limit']} 次/分"
        f" · {snap['hour']['remaining']}/{snap['hour']['limit']} 次/时"
        f" · {snap['day']['remaining']}/{snap['day']['limit']} 次/天"
    )


def _quota_html(snap: dict[str, dict[str, int]]) -> str:
    bars = []
    for key, label in (("minute", "每分钟"), ("hour", "每小时"), ("day", "每天")):
        item = snap[key]
        pct = 0.0 if item["limit"] <= 0 else 100.0 * item["remaining"] / item["limit"]
        bars.append(
            f'<div class="qbar" data-key="{key}">'
            f'<span class="qlab">{html.escape(label)}</span>'
            f'<div class="qtrack"><i style="width:{pct:.1f}%"></i></div>'
            f'<span class="qnum">{item["remaining"]}/{item["limit"]}</span>'
            f"</div>"
        )
    return (
        f'<div class="quota" id="quota">'
        f'<p class="qtext">{html.escape(quota_text(snap))}</p>'
        f"{''.join(bars)}"
        f"</div>"
    )


def _maybe_set_cookie(response: Response, password: str) -> None:
    expected = _access_password()
    if expected and password and _passwords_equal(password, expected):
        response.set_cookie(
            COOKIE_NAME,
            auth_cookie_value(expected),
            httponly=True,
            samesite="lax",
            max_age=7 * 24 * 3600,
        )


def _shell_page(title: str, body: str, *, css: str = PAGE_CSS) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(title)}</title>
  <style>{css}</style>
</head>
<body>
{body}
</body>
</html>
"""


def _index_html(snap: dict[str, dict[str, int]], need_password: bool) -> str:
    password_field = ""
    if need_password:
        password_field = """
    <label>访问密码
      <input type="password" name="password" autocomplete="current-password" required />
    </label>
    <p class="hint">请向管理员获取访问密码后再批改。</p>
"""
    else:
        password_field = '<p class="hint">访问已验证。本页配额为全站共享（非按 IP）。</p>'
    body = f"""
  <h1>作业照片批改</h1>
  <p class="hint">上传含题干与学生作答的图片，系统将调用 Gemini 生成批改报告。</p>
  {_quota_html(snap)}
  <form action="/grade" method="post" enctype="multipart/form-data">
    {password_field}
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
  <script>
    function renderQuota(q) {{
      const el = document.getElementById("quota");
      if (!el) return;
      el.querySelector(".qtext").textContent =
        "剩余 " + q.minute.remaining + "/" + q.minute.limit + " 次/分 · "
        + q.hour.remaining + "/" + q.hour.limit + " 次/时 · "
        + q.day.remaining + "/" + q.day.limit + " 次/天";
      const keys = ["minute", "hour", "day"];
      el.querySelectorAll(".qbar").forEach((bar, i) => {{
        const item = q[keys[i]];
        bar.querySelector(".qnum").textContent = item.remaining + "/" + item.limit;
        bar.querySelector("i").style.width =
          (item.limit ? (100 * item.remaining / item.limit) : 0) + "%";
      }});
    }}
    function refreshQuota() {{
      fetch("/api/quota").then(r => r.json()).then(renderQuota).catch(() => {{}});
    }}
    refreshQuota();
  </script>
"""
    return _shell_page("作业批改", body)


def _error_page(message: str, snap: dict[str, dict[str, int]]) -> str:
    body = f"""
  <p class="err">{html.escape(message)}</p>
  {_quota_html(snap)}
  <p><a href="/">← 返回上传</a></p>
"""
    return _shell_page("无法批改", body)


def _md_to_html(md_text: str, snap: dict[str, dict[str, int]]) -> str:
    if md_lib is not None:
        body_md = md_lib.markdown(md_text, extensions=["fenced_code", "tables", "nl2br"])
    else:
        body_md = "<pre>" + html.escape(md_text) + "</pre>"
    body = f"""
  <p><a href="/">← 返回上传</a></p>
  {_quota_html(snap)}
  {body_md}
"""
    return _shell_page("批改结果", body, css=RESULT_CSS)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/quota")
def api_quota():
    return JSONResponse(_limiter.snapshot())


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> str:
    authed = check_access(None, request.cookies.get(COOKIE_NAME))
    return _index_html(_limiter.snapshot(), need_password=not authed)


@app.post("/grade")
async def grade(
    request: Request,
    image: Annotated[UploadFile, File()],
    level: Annotated[str, Form()] = "初中",
    subject: Annotated[str, Form()] = "数学",
    format: Annotated[str, Form()] = "html",
    password: Annotated[str, Form()] = "",
) -> Response:
    cookie = request.cookies.get(COOKIE_NAME)
    code, msg, snap = gate_grading_request(password, cookie)
    if code != 200:
        response = HTMLResponse(_error_page(msg or "无法批改", snap), status_code=code)
        if code != 401:
            _maybe_set_cookie(response, password)
        return response

    if level not in LEVELS:
        level = "初中"
    data = await image.read()
    if not data:
        response = HTMLResponse(
            _error_page("图片为空", snap),
            status_code=400,
        )
        _maybe_set_cookie(response, password)
        return response

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
        err_code = exc.code if isinstance(exc.code, int) else 500
        fail_msg = "缺少 GEMINI_API_KEY" if err_code == 2 else "批改服务调用失败"
        response = HTMLResponse(_error_page(fail_msg, snap), status_code=502)
        _maybe_set_cookie(response, password)
        return response
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        response = HTMLResponse(
            _error_page(f"结果校验失败：{exc}", snap),
            status_code=502,
        )
        _maybe_set_cookie(response, password)
        return response
    except Exception as exc:  # noqa: BLE001
        response = HTMLResponse(
            _error_page(f"批改失败：{exc}", snap),
            status_code=502,
        )
        _maybe_set_cookie(response, password)
        return response

    md_text = render_markdown(result)
    if format in ("md", "markdown", "text/markdown"):
        response = Response(content=md_text, media_type="text/markdown; charset=utf-8")
        _maybe_set_cookie(response, password)
        return response
    response = HTMLResponse(_md_to_html(md_text, snap))
    _maybe_set_cookie(response, password)
    return response


def main() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run("web_app:app", host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    main()

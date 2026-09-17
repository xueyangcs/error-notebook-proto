"""Unit tests for the global grading rate limiter and password gate."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

import web_app
from web_app import GlobalRateLimiter, gate_grading_request


class FakeClock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class TestGlobalRateLimiter(unittest.TestCase):
    def test_fourth_call_in_a_minute_rejected(self) -> None:
        clock = FakeClock()
        limiter = GlobalRateLimiter(clock=clock)
        for _ in range(3):
            ok, snap, msg = limiter.try_acquire()
            self.assertTrue(ok, msg)
            self.assertIsNone(msg)
        ok, snap, msg = limiter.try_acquire()
        self.assertFalse(ok)
        self.assertIsNotNone(msg)
        self.assertIn("分钟", msg)
        self.assertEqual(snap["minute"]["used"], 3)
        self.assertEqual(snap["minute"]["remaining"], 0)
        self.assertEqual(snap["minute"]["limit"], 3)
        # Rejected call must not consume a slot.
        self.assertEqual(limiter.snapshot()["minute"]["used"], 3)

    def test_slot_frees_after_rolling_minute(self) -> None:
        clock = FakeClock()
        limiter = GlobalRateLimiter(clock=clock)
        for _ in range(3):
            self.assertTrue(limiter.try_acquire()[0])
        clock.now += 60
        ok, snap, msg = limiter.try_acquire()
        self.assertTrue(ok, msg)
        self.assertEqual(snap["minute"]["used"], 1)
        self.assertEqual(snap["minute"]["remaining"], 2)

    def test_eleventh_call_in_an_hour_rejected(self) -> None:
        clock = FakeClock()
        limiter = GlobalRateLimiter(clock=clock)
        for i in range(10):
            clock.now = 1_000_000.0 + i * 61
            ok, _, msg = limiter.try_acquire()
            self.assertTrue(ok, msg)
        clock.now = 1_000_000.0 + 10 * 61
        ok, snap, msg = limiter.try_acquire()
        self.assertFalse(ok)
        self.assertIn("小时", msg)
        self.assertEqual(snap["hour"]["used"], 10)
        self.assertEqual(snap["hour"]["remaining"], 0)

    def test_twenty_first_call_in_a_day_rejected(self) -> None:
        clock = FakeClock()
        limiter = GlobalRateLimiter(clock=clock)
        start = clock.now
        for i in range(20):
            clock.now = start + i * 4000  # > 1 hour apart so only the day window binds
            ok, _, msg = limiter.try_acquire()
            self.assertTrue(ok, msg)
        clock.now = start + 20 * 4000
        ok, snap, msg = limiter.try_acquire()
        self.assertFalse(ok)
        self.assertIn("天", msg)
        self.assertEqual(snap["day"]["used"], 20)
        self.assertEqual(snap["day"]["remaining"], 0)
        self.assertEqual(snap["day"]["limit"], 20)

    def test_snapshot_does_not_record_a_hit(self) -> None:
        limiter = GlobalRateLimiter(clock=FakeClock())
        before = limiter.snapshot()
        self.assertEqual(before["day"]["used"], 0)
        self.assertEqual(before["minute"]["remaining"], 3)
        self.assertEqual(before["hour"]["remaining"], 10)
        self.assertEqual(before["day"]["remaining"], 20)
        limiter.snapshot()
        self.assertEqual(limiter.snapshot()["day"]["used"], 0)

    def test_quota_text_format(self) -> None:
        limiter = GlobalRateLimiter(clock=FakeClock())
        limiter.try_acquire()
        snap = limiter.snapshot()
        text = (
            f"剩余 {snap['minute']['remaining']}/{snap['minute']['limit']} 次/分"
            f" · {snap['hour']['remaining']}/{snap['hour']['limit']} 次/时"
            f" · {snap['day']['remaining']}/{snap['day']['limit']} 次/天"
        )
        self.assertEqual(text, "剩余 2/3 次/分 · 9/10 次/时 · 19/20 次/天")


class TestGateGradingRequest(unittest.TestCase):
    def setUp(self) -> None:
        self.limiter = GlobalRateLimiter(clock=FakeClock())

    @patch.dict(os.environ, {"ACCESS_PASSWORD": "s3cret"}, clear=False)
    def test_wrong_password_does_not_consume_quota(self) -> None:
        code, msg, snap = gate_grading_request("wrong", None, self.limiter)
        self.assertEqual(code, 401)
        self.assertIn("密码", msg)
        self.assertEqual(snap["day"]["used"], 0)
        self.assertEqual(self.limiter.snapshot()["minute"]["used"], 0)

    @patch.dict(os.environ, {"ACCESS_PASSWORD": "s3cret"}, clear=False)
    def test_correct_password_consumes_quota(self) -> None:
        code, msg, snap = gate_grading_request("s3cret", None, self.limiter)
        self.assertEqual(code, 200)
        self.assertIsNone(msg)
        self.assertEqual(snap["minute"]["used"], 1)

    @patch.dict(os.environ, {"ACCESS_PASSWORD": "s3cret"}, clear=False)
    def test_valid_cookie_allows_without_password_field(self) -> None:
        from web_app import auth_cookie_value

        cookie = auth_cookie_value()
        code, msg, snap = gate_grading_request("", cookie, self.limiter)
        self.assertEqual(code, 200)
        self.assertIsNone(msg)
        self.assertEqual(snap["minute"]["used"], 1)

    @patch.dict(os.environ, {"ACCESS_PASSWORD": ""}, clear=False)
    def test_missing_password_config_does_not_call_limiter(self) -> None:
        os.environ.pop("ACCESS_PASSWORD", None)
        code, msg, snap = gate_grading_request("anything", None, self.limiter)
        self.assertEqual(code, 503)
        self.assertIn("访问密码", msg)
        self.assertEqual(snap["day"]["used"], 0)

    @patch.dict(os.environ, {"ACCESS_PASSWORD": "s3cret"}, clear=False)
    def test_rate_limit_after_password_ok(self) -> None:
        for _ in range(3):
            self.assertEqual(gate_grading_request("s3cret", None, self.limiter)[0], 200)
        code, msg, snap = gate_grading_request("s3cret", None, self.limiter)
        self.assertEqual(code, 429)
        self.assertIn("分钟", msg)
        self.assertEqual(snap["minute"]["remaining"], 0)


class TestWebAppHttp(unittest.TestCase):
    def setUp(self) -> None:
        self.limiter = GlobalRateLimiter(clock=FakeClock())
        patcher = patch.object(web_app, "_limiter", self.limiter)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(web_app.app)

    def test_healthz_is_public(self) -> None:
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})

    def test_api_quota_json(self) -> None:
        response = self.client.get("/api/quota")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["minute"]["limit"], 3)
        self.assertEqual(body["hour"]["limit"], 10)
        self.assertEqual(body["day"]["limit"], 20)
        self.assertEqual(body["minute"]["remaining"], 3)

    @patch.dict(os.environ, {"ACCESS_PASSWORD": "s3cret"}, clear=False)
    def test_index_shows_quota_and_password_field(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("剩余 3/3 次/分", response.text)
        self.assertIn('name="password"', response.text)

    @patch.dict(os.environ, {"ACCESS_PASSWORD": "s3cret"}, clear=False)
    def test_index_shows_in_progress_ui_on_grade_submit(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn('id="grade-form"', html)
        self.assertIn('id="grade-submit"', html)
        self.assertIn('id="grade-status"', html)
        self.assertIn("正在批改，请稍候", html)
        self.assertIn('addEventListener("submit"', html)
        self.assertIn("aria-busy", html)
        self.assertIn(".disabled = true", html)
        # Password, quota bars, and rate-limit copy must stay unchanged.
        self.assertIn('name="password"', html)
        self.assertIn('id="quota"', html)
        self.assertIn("剩余 3/3 次/分", html)
        self.assertIn('fetch("/api/quota")', html)

    @patch.dict(os.environ, {"ACCESS_PASSWORD": "s3cret"}, clear=False)
    def test_wrong_password_is_401_and_skips_gemini(self) -> None:
        with patch.object(web_app, "grade_bytes") as mock_grade:
            response = self.client.post(
                "/grade",
                data={"level": "初中", "subject": "数学", "password": "nope"},
                files={"image": ("a.jpg", b"abc", "image/jpeg")},
            )
        self.assertEqual(response.status_code, 401)
        self.assertIn("密码错误", response.text)
        self.assertIn("剩余 3/3 次/分", response.text)
        mock_grade.assert_not_called()
        self.assertEqual(self.limiter.snapshot()["minute"]["used"], 0)

    @patch.dict(os.environ, {"ACCESS_PASSWORD": "s3cret"}, clear=False)
    def test_fourth_http_grade_in_a_minute_is_429(self) -> None:
        for _ in range(3):
            self.assertTrue(self.limiter.try_acquire()[0])
        with patch.object(web_app, "grade_bytes") as mock_grade:
            response = self.client.post(
                "/grade",
                data={"level": "初中", "subject": "数学", "password": "s3cret"},
                files={"image": ("a.jpg", b"abc", "image/jpeg")},
            )
        self.assertEqual(response.status_code, 429)
        self.assertIn("每分钟", response.text)
        self.assertIn("剩余 0/3 次/分", response.text)
        mock_grade.assert_not_called()


if __name__ == "__main__":
    unittest.main()

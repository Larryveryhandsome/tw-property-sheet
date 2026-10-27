"""決策 #8：預設只聽本機；開放區網必須設密碼才會啟動；設了密碼後所有頁面與 API 都要驗證。"""
import base64
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def basic(user, pw):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


class BindDefaultTest(unittest.TestCase):
    def test_default_is_localhost(self):
        self.assertEqual(main.bind_config({}), ("127.0.0.1", 8000))

    def test_blank_host_falls_back_to_localhost(self):
        self.assertEqual(main.bind_config({"PSHEET_HOST": "  ", "PSHEET_PORT": ""}), ("127.0.0.1", 8000))

    def test_env_example_defaults_to_localhost(self):
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("PSHEET_HOST=127.0.0.1", text)
        self.assertIn("PSHEET_PASSWORD=", text)


class StartupCheckTest(unittest.TestCase):
    def test_local_hosts_need_no_password(self):
        for h in ("127.0.0.1", "localhost", "::1"):
            ok, _ = main.startup_check(h, "")
            self.assertTrue(ok, h)

    def test_lan_without_password_refused(self):
        for h in ("0.0.0.0", "192.0.2.10", "::"):
            ok, msg = main.startup_check(h, "")
            self.assertFalse(ok, h)
            self.assertIn("PSHEET_PASSWORD", msg)

    def test_lan_short_password_refused(self):
        ok, msg = main.startup_check("0.0.0.0", "1234567")
        self.assertFalse(ok)
        self.assertIn("太短", msg)

    def test_lan_with_password_allowed(self):
        ok, _ = main.startup_check("0.0.0.0", "12345678")
        self.assertTrue(ok)

    def test_process_refuses_to_start(self):
        """真的執行 main.py：PSHEET_HOST=0.0.0.0 又沒密碼 → 程序馬上結束（代碼 2），不會開始監聽。"""
        env = {**os.environ, "PSHEET_HOST": "0.0.0.0", "PSHEET_PASSWORD": "", "PSHEET_PORT": "18765",
               "PYTHONIOENCODING": "utf-8"}
        r = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "main.py")], cwd=str(ROOT), env=env,
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("拒絕啟動", r.stdout + r.stderr)


    def test_check_mode_for_vbs(self):
        """背景啟動.vbs 先跑 main.py --check：設定不行回 2（跳視窗），可以回 0（再真正啟動）。"""
        base = {**os.environ, "PSHEET_PORT": "18766", "PYTHONIOENCODING": "utf-8"}
        bad = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "main.py"), "--check"], cwd=str(ROOT),
                             env={**base, "PSHEET_HOST": "0.0.0.0", "PSHEET_PASSWORD": ""},
                             capture_output=True, timeout=60)
        good = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "main.py"), "--check"], cwd=str(ROOT),
                              env={**base, "PSHEET_HOST": "0.0.0.0", "PSHEET_PASSWORD": "12345678"},
                              capture_output=True, timeout=60)
        self.assertEqual(bad.returncode, 2)
        self.assertEqual(good.returncode, 0)


class PasswordMiddlewareTest(unittest.TestCase):
    def setUp(self):
        self._old = main.PASSWORD
        main.PASSWORD = "correct-horse"
        self.c = TestClient(main.app)

    def tearDown(self):
        main.PASSWORD = self._old

    def test_no_credentials_401_everywhere(self):
        for method, path in [("get", "/"), ("get", "/health"), ("get", "/api/settings"),
                             ("get", "/api/dropdown-options"), ("get", "/static/app.js"),
                             ("post", "/api/risk"), ("post", "/api/generate"), ("post", "/api/official-land")]:
            r = getattr(self.c, method)(path)
            self.assertEqual(r.status_code, 401, path)
            self.assertIn("Basic", r.headers.get("www-authenticate", ""), path)

    def test_wrong_password_401(self):
        self.assertEqual(self.c.get("/api/settings", headers=basic("x", "wrong-pass")).status_code, 401)

    def test_malformed_header_401(self):
        for h in ("Basic !!!notbase64", "Bearer correct-horse", "Basic " + base64.b64encode(b"nocolon").decode()):
            self.assertEqual(self.c.get("/health", headers={"Authorization": h}).status_code, 401, h)

    def test_correct_password_200(self):
        for path in ("/", "/health", "/api/settings", "/api/dropdown-options", "/static/app.js"):
            r = self.c.get(path, headers=basic("任何人", "correct-horse"))
            self.assertEqual(r.status_code, 200, path)

    def test_correct_password_post_api(self):
        r = self.c.post("/api/risk", data={"deed_json": "{}", "form_json": "{}"}, headers=basic("a", "correct-horse"))
        self.assertEqual(r.status_code, 200)


class NoPasswordLocalTest(unittest.TestCase):
    def test_local_without_password_open(self):
        old, main.PASSWORD = main.PASSWORD, ""
        try:
            self.assertEqual(TestClient(main.app).get("/health").status_code, 200)
        finally:
            main.PASSWORD = old


if __name__ == "__main__":
    unittest.main()

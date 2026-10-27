"""地政平台官方核對測試：用假的平台回應（httpx.MockTransport），不會真的連線或扣錢。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from services import moi  # noqa: E402


class FakePlatform:
    def __init__(self, token_ok=True, status=1, area="330.5"):
        self.calls = []
        self.token_ok, self.status, self.area = token_ok, status, area

    def handler(self, req: httpx.Request):
        self.calls.append(req.url.path)
        if req.url.path.endswith("/cp/getToken"):
            assert req.headers["Authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": "tok"} if self.token_ok else {})
        assert req.headers["Authorization"] == "Bearer tok"
        body = json.loads(req.content)
        assert body == [{"UNIT": "XA", "SEC": "0101", "NO": "01230000"}]
        return httpx.Response(200, json={"STATUS": self.status, "CODE": "A0702" if self.status != 1 else "",
                                         "RESPONSE": [{"LANDREG": {"LANDAREA": self.area, "ZONE": "住宅區"}}]})

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handler))


class MoiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.saved = (moi.DATA, moi.CACHE, moi.USAGE, dict(os.environ))
        moi.DATA, moi.CACHE, moi.USAGE = d, d / "c.json", d / "u.json"
        moi._token.update(value=None, at=0)
        os.environ.update(MOI_COP_CLIENT_ID="demo-id", MOI_COP_SECRET="demo-secret", MOI_DAILY_LIMIT="2")

    def tearDown(self):
        moi.DATA, moi.CACHE, moi.USAGE, env = self.saved
        os.environ.clear()
        os.environ.update(env)
        moi._token.update(value=None, at=0)
        self.tmp.cleanup()

    def test_disabled_without_keys(self):
        os.environ.pop("MOI_COP_SECRET")
        self.assertFalse(moi.enabled())
        with self.assertRaises(moi.MoiError):
            moi.get_token(client=FakePlatform().client(), sleep=lambda s: None)

    def test_query_caches_and_does_not_charge_twice(self):
        fake = FakePlatform()
        r = moi.query_land("XA", "0101", "01230000", client=fake.client(), today="2026-10-20")
        self.assertEqual((r["area_sqm"], r["area_ping"], r["cached"]), (330.5, 99.98, False))
        self.assertEqual(r["raw"]["ZONE"], "住宅區")
        r2 = moi.query_land("XA", "0101", "01230000", client=fake.client(), today="2026-10-20")
        self.assertTrue(r2["cached"])
        self.assertEqual(sum(p.endswith("QueryByLandNo") for p in fake.calls), 1)
        self.assertEqual(moi.used_today("2026-10-20"), 1)

    def test_daily_limit(self):
        fake = FakePlatform()
        moi.query_land("XA", "0101", "01230000", client=fake.client(), today="2026-10-20")
        moi._count("2026-10-20")
        with self.assertRaises(moi.MoiError) as e:
            moi.query_land("XB", "0101", "01230000", client=fake.client(), today="2026-10-20")
        self.assertIn("每日上限", str(e.exception))

    def test_platform_error_and_bad_token(self):
        with self.assertRaises(moi.MoiError) as e:
            moi.query_land("XA", "0101", "01230000", client=FakePlatform(status=0).client(), today="2026-10-21")
        self.assertIn("A0702", str(e.exception))
        moi._token.update(value=None, at=0)
        with self.assertRaises(moi.MoiError):
            moi.get_token(client=FakePlatform(token_ok=False).client(), sleep=lambda s: None)

    def test_helpers(self):
        self.assertEqual(moi.find_area({"X": "a", "LANDAREA": "12.5"}), 12.5)
        self.assertIsNone(moi.find_area(None))
        self.assertEqual(moi.compare(100, 104), {"other_ping": 104, "diff_pct": 4.0, "ok": False})
        self.assertEqual(moi.compare(100, None), {})


class MoiApiTest(unittest.TestCase):
    def test_endpoint_requires_keys_and_confirm(self):
        from fastapi.testclient import TestClient
        import main
        from services import parcel
        c = TestClient(main.app)
        saved_env = dict(os.environ)
        os.environ.pop("MOI_COP_CLIENT_ID", None)
        r = c.post("/api/official-land", data={"county": "示範市", "section": "示範段", "number": "123"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("金鑰申請", r.json()["detail"])

        async def fake_resolve(county, section, town=""):
            return "XA", "0101", "西區", "示範段"
        saved = parcel.resolve_section, moi.cached
        parcel.resolve_section = fake_resolve
        moi.cached = lambda *a: None
        os.environ.update(MOI_COP_CLIENT_ID="demo-id", MOI_COP_SECRET="demo-secret")
        try:
            r = c.post("/api/official-land", data={"county": "示範市", "section": "示範段", "number": "123"})
            self.assertEqual(r.status_code, 409)               # 沒確認就不查、不收費
            self.assertTrue(c.get("/api/settings").json()["moi"])
        finally:
            parcel.resolve_section, moi.cached = saved
            os.environ.clear()
            os.environ.update(saved_env)


if __name__ == "__main__":
    unittest.main()

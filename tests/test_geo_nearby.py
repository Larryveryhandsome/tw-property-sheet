"""周邊設施查詢：公開站台失敗或太慢時要在總時限內留空，不能讓使用者一直等。不會真的連線。"""
import asyncio
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from services import geo  # noqa: E402



class NearbyTest(unittest.TestCase):
    def tearDown(self):
        geo._client = self._orig
        geo.NEARBY_BUDGET = self._budget

    def setUp(self):
        self._orig, self._budget = geo._client, geo.NEARBY_BUDGET

    def test_all_mirrors_fail_returns_empty(self):
        geo._client = lambda timeout=15.0: httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(406)))
        self.assertEqual(asyncio.run(geo.nearby(24.75, 121.75)), {})

    def test_second_mirror_used_when_first_fails(self):
        calls = []

        def handler(req):
            calls.append(str(req.url))
            if len(calls) == 1:
                return httpx.Response(504)
            return httpx.Response(200, json={"elements": [
                {"lat": 24.7505, "lon": 121.75, "tags": {"amenity": "school", "name": "示範國小"}}]})
        geo._client = lambda timeout=15.0: httpx.AsyncClient(transport=httpx.MockTransport(handler))
        out = asyncio.run(geo.nearby(24.75, 121.75))
        self.assertIn("示範國小", out["school"])
        self.assertEqual(len(calls), 2)

    def test_slow_mirrors_cut_off_by_budget(self):
        async def slow(req):
            await asyncio.sleep(5)
            return httpx.Response(200, json={"elements": []})
        geo._client = lambda timeout=15.0: httpx.AsyncClient(transport=httpx.MockTransport(slow))
        geo.NEARBY_BUDGET = 0.3
        t = time.monotonic()
        self.assertEqual(asyncio.run(geo.nearby(24.75, 121.75)), {})
        self.assertLess(time.monotonic() - t, 2)


if __name__ == "__main__":
    unittest.main()

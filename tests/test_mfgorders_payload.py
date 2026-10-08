import json
import unittest
from unittest.mock import AsyncMock, patch

from tools import mfgorders


class MfgOrderPayloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_includes_product_revision(self):
        request_mock = AsyncMock(return_value="ok")

        with patch.object(mfgorders, "request", request_mock):
            result = await mfgorders.create_mfgorder(
                name="MO-001",
                product_name="WM-900A",
                product_revision="A",
                qty=80,
            )

        self.assertEqual(result, "ok")
        self.assertEqual(
            request_mock.await_args.kwargs["body"],
            {
                "name": "MO-001",
                "product": {"name": "WM-900A", "revision": "A"},
                "qty": 80,
            },
        )

    async def test_body_json_merges_case_insensitively(self):
        request_mock = AsyncMock(return_value="ok")
        body_json = json.dumps(
            {
                "Product": {
                    "Name": "WM-900A",
                    "Revision": "A",
                    "UseRor": False,
                },
                "Qty": 90,
            }
        )

        with patch.object(mfgorders, "request", request_mock):
            await mfgorders.create_mfgorder(
                name="MO-002",
                product_name="WM-900A",
                product_revision="A",
                qty=80,
                body_json=body_json,
            )

        payload = request_mock.await_args.kwargs["body"]
        self.assertNotIn("Product", payload)
        self.assertNotIn("Qty", payload)
        self.assertEqual(payload["qty"], 90)
        self.assertEqual(
            payload["product"],
            {
                "name": "WM-900A",
                "revision": "A",
                "useROR": False,
            },
        )

    async def test_body_json_must_be_an_object(self):
        request_mock = AsyncMock(return_value="ok")

        with patch.object(mfgorders, "request", request_mock):
            result = await mfgorders.create_mfgorder(
                name="MO-003",
                product_name="WM-900A",
                product_revision="A",
                qty=100,
                body_json="[]",
            )

        self.assertEqual(result, "❌ Invalid body_json: expected a JSON object.")
        request_mock.assert_not_awaited()

    async def test_update_endpoints_include_product_revision(self):
        cases = (
            (mfgorders.update_mfgorder, "/api/MfgOrders/MO-004"),
            (mfgorders.update_mfgorder_by_odata_key, "/api/MfgOrders(MO-004)"),
        )

        for update_func, expected_path in cases:
            with self.subTest(update_func=update_func.__name__):
                request_mock = AsyncMock(return_value="ok")
                with patch.object(mfgorders, "request", request_mock):
                    await update_func(
                        key="MO-004",
                        name="MO-004",
                        product_name="WM-900A",
                        product_revision="A",
                        qty=100,
                    )

                self.assertEqual(request_mock.await_args.args[:2], ("PUT", expected_path))
                self.assertEqual(
                    request_mock.await_args.kwargs["body"]["product"],
                    {"name": "WM-900A", "revision": "A"},
                )


if __name__ == "__main__":
    unittest.main()

import json
import unittest
from unittest.mock import AsyncMock, patch

from tools import container_start as container_start_module


class ContainerStartTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_serial_number_payload(self):
        request_mock = AsyncMock(return_value="ok")

        with patch.object(
            container_start_module, "request_shopfloor", request_mock
        ):
            result = await container_start_module.container_start(
                mfg_order_name="MO-100076435",
                product_name="WM-900A",
                product_revision="A",
                qty=1,
                level_name="Unit",
                container_name="SN-0001",
                uom_name="EA",
                owner_name="PRODUCTION",
                start_reason_name="NORMAL",
            )

        self.assertEqual(result, "ok")
        self.assertEqual(request_mock.await_args.args, ("POST", "/api/Start"))
        self.assertEqual(
            request_mock.await_args.kwargs["body"],
            {
                "details": {
                    "mfgOrder": {"name": "MO-100076435"},
                    "product": {"name": "WM-900A", "revision": "A"},
                    "qty": 1,
                    "level": {"name": "Unit"},
                    "containerName": "SN-0001",
                    "autoNumber": False,
                    "uom": {"name": "EA"},
                    "owner": {"name": "PRODUCTION"},
                    "startReason": {"name": "NORMAL"},
                }
            },
        )

    async def test_auto_number_payload(self):
        request_mock = AsyncMock(return_value="ok")

        with patch.object(
            container_start_module, "request_shopfloor", request_mock
        ):
            await container_start_module.container_start(
                mfg_order_name="MO-100076435",
                product_name="WM-900A",
                product_revision="A",
                qty=1,
                level_name="Unit",
                auto_number_rule_name="UNIT-SN",
                owner_name="PRODUCTION",
                start_reason_name="NORMAL",
            )

        details = request_mock.await_args.kwargs["body"]["details"]
        self.assertTrue(details["autoNumber"])
        self.assertNotIn("autoNumberRule", details)
        self.assertNotIn("containerName", details)

    async def test_workflow_uses_current_status_details(self):
        request_mock = AsyncMock(return_value="ok")

        with patch.object(
            container_start_module, "request_shopfloor", request_mock
        ):
            await container_start_module.container_start(
                mfg_order_name="MO-100076435",
                product_name="WM-900A",
                product_revision="A",
                qty=1,
                level_name="Unit",
                container_name="SN-0003",
                workflow_name="WF-WM-900A",
                workflow_revision="A",
                owner_name="PRODUCTION",
                start_reason_name="NORMAL",
            )

        payload = request_mock.await_args.kwargs["body"]
        self.assertNotIn("workflow", payload["details"])
        self.assertEqual(
            payload["currentStatusDetails"]["workflow"],
            {"name": "WF-WM-900A", "revision": "A"},
        )

    async def test_numbering_mode_is_exclusive(self):
        request_mock = AsyncMock(return_value="ok")

        with patch.object(
            container_start_module, "request_shopfloor", request_mock
        ):
            missing = await container_start_module.container_start(
                "MO-1", "P-1", "A", 1, "Unit", "PRODUCTION", "NORMAL"
            )
            duplicate = await container_start_module.container_start(
                "MO-1",
                "P-1",
                "A",
                1,
                "Unit",
                "PRODUCTION",
                "NORMAL",
                container_name="SN-1",
                auto_number_rule_name="RULE-1",
            )

        self.assertIn("exactly one", missing)
        self.assertIn("exactly one", duplicate)
        request_mock.assert_not_awaited()

    async def test_body_json_deep_merges_case_insensitively(self):
        request_mock = AsyncMock(return_value="ok")
        body_json = json.dumps(
            {
                "Details": {
                    "Qty": 2,
                    "Product": {
                        "Name": "WM-900A",
                        "Revision": "B",
                    },
                },
                "Comments": "test",
            }
        )

        with patch.object(
            container_start_module, "request_shopfloor", request_mock
        ):
            await container_start_module.container_start(
                mfg_order_name="MO-100076435",
                product_name="WM-900A",
                product_revision="A",
                qty=1,
                level_name="Unit",
                container_name="SN-0002",
                owner_name="PRODUCTION",
                start_reason_name="NORMAL",
                body_json=body_json,
            )

        payload = request_mock.await_args.kwargs["body"]
        self.assertNotIn("Details", payload)
        self.assertNotIn("Qty", payload["details"])
        self.assertNotIn("Product", payload["details"])
        self.assertEqual(payload["details"]["qty"], 2)
        self.assertEqual(payload["details"]["product"]["revision"], "B")
        self.assertEqual(payload["comments"], "test")

    async def test_blank_owner_is_rejected_before_write(self):
        request_mock = AsyncMock(return_value="ok")

        with patch.object(
            container_start_module, "request_shopfloor", request_mock
        ):
            result = await container_start_module.container_start(
                mfg_order_name="MO-1",
                product_name="P-1",
                product_revision="A",
                qty=1,
                level_name="Unit",
                owner_name="",
                start_reason_name="NORMAL",
                container_name="SN-1",
            )

        self.assertIn("Owner is required", result)
        request_mock.assert_not_awaited()

    async def test_body_json_cannot_force_auto_number_rule_write(self):
        request_mock = AsyncMock(return_value="ok")

        with patch.object(
            container_start_module, "request_shopfloor", request_mock
        ):
            result = await container_start_module.container_start(
                mfg_order_name="MO-1",
                product_name="P-1",
                product_revision="A",
                qty=1,
                level_name="Unit",
                auto_number_rule_name="RULE-1",
                owner_name="PRODUCTION",
                start_reason_name="NORMAL",
                body_json=json.dumps(
                    {"details": {"autoNumberRule": {"name": "RULE-1"}}}
                ),
            )

        self.assertIn("Do not write details.autoNumberRule", result)
        request_mock.assert_not_awaited()

    async def test_request_start_selection_values_uses_context(self):
        request_mock = AsyncMock(return_value="ok")

        with patch.object(
            container_start_module, "request_shopfloor", request_mock
        ):
            result = await (
                container_start_module.request_container_start_selection_values(
                    selection_values_expression="Details.StartReason",
                    mfg_order_name="MO-1",
                    product_name="P-1",
                    product_revision="A",
                    qty=1,
                    level_name="BOX",
                    owner_name="PRODUCTION",
                )
            )

        self.assertEqual(result, "ok")
        request_mock.assert_awaited_once_with(
            "POST",
            "/api/Start/RequestSelectionValues",
            body={
                "details": {
                    "mfgOrder": {"name": "MO-1"},
                    "product": {"name": "P-1", "revision": "A"},
                    "qty": 1,
                    "level": {"name": "BOX"},
                    "owner": {"name": "PRODUCTION"},
                }
            },
            params={"selectionValuesExpression": "Details.StartReason"},
        )


if __name__ == "__main__":
    unittest.main()

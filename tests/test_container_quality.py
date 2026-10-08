import json
import unittest
from unittest.mock import AsyncMock, patch

from tools import container_quality


class ContainerQualityTests(unittest.IsolatedAsyncioTestCase):
    async def test_container_defect_payload(self):
        request_mock = AsyncMock(return_value="ok")
        with patch.object(container_quality, "request_shopfloor", request_mock):
            result = await container_quality.container_defect(
                container_name="SN-0001",
                container_level_name="Unit",
                reason_code_name="Visual Defect",
                defect_count=2,
                detail_comment="scratch",
                resource_name="LINE-01",
                qty_inspected=10,
            )

        self.assertEqual(result, "ok")
        self.assertEqual(
            request_mock.await_args.args,
            ("POST", "/api/ContainerDefect"),
        )
        self.assertEqual(
            request_mock.await_args.kwargs["body"],
            {
                "container": {
                    "name": "SN-0001",
                    "level": {"name": "Unit"},
                },
                "serviceDetails": [
                    {
                        "reasonCode": {"name": "Visual Defect"},
                        "defectCount": 2,
                        "comment": "scratch",
                    }
                ],
                "resource": {"name": "LINE-01"},
                "qtyInspected": 10,
            },
        )

    async def test_container_defect_validation(self):
        request_mock = AsyncMock(return_value="ok")
        with patch.object(container_quality, "request_shopfloor", request_mock):
            result = await container_quality.container_defect(
                "SN-0001",
                "Visual Defect",
                0,
            )

        self.assertIn("greater than 0", result)
        request_mock.assert_not_awaited()

    async def test_rework_default_payload(self):
        request_mock = AsyncMock(return_value="ok")
        with patch.object(container_quality, "request_shopfloor", request_mock):
            result = await container_quality.rework(
                container_name="SN-0001",
                rework_reason_name="Repair Required",
                comments="send to rework",
            )

        self.assertEqual(result, "ok")
        self.assertEqual(
            request_mock.await_args.args,
            ("POST", "/api/Rework"),
        )
        self.assertEqual(
            request_mock.await_args.kwargs["body"],
            {
                "container": {"name": "SN-0001"},
                "reworkReason": {"name": "Repair Required"},
                "moveAllQty": True,
                "close": False,
                "comments": "send to rework",
            },
        )

    async def test_rework_partial_quantity_and_route_payload(self):
        request_mock = AsyncMock(return_value="ok")
        with patch.object(container_quality, "request_shopfloor", request_mock):
            await container_quality.rework(
                container_name="LOT-0001",
                rework_reason_name="Inspection Failure",
                move_all_qty=False,
                qty=5,
                to_workflow_name="REWORK-WF",
                to_workflow_revision="A",
                to_step_name="Repair",
                end_rework_workflow_name="MAIN-WF",
                end_rework_workflow_revision="B",
                end_rework_step_name="Inspect",
            )

        payload = request_mock.await_args.kwargs["body"]
        self.assertFalse(payload["moveAllQty"])
        self.assertEqual(payload["qty"], 5)
        self.assertEqual(
            payload["toWorkflow"],
            {"name": "REWORK-WF", "revision": "A"},
        )
        self.assertEqual(payload["toStep"], {"name": "Repair"})
        self.assertEqual(
            payload["endReworkWorkflow"],
            {"name": "MAIN-WF", "revision": "B"},
        )
        self.assertEqual(payload["endReworkStep"], {"name": "Inspect"})

    async def test_body_json_merges_with_canonical_field_names(self):
        request_mock = AsyncMock(return_value="ok")
        body_json = json.dumps(
            {
                "Container": {"Name": "SN-0002", "Level": {"Name": "Unit"}},
                "ServiceDetails": [
                    {
                        "ReasonCode": {"Name": "Electrical Defect"},
                        "DefectCount": 3,
                    }
                ],
            }
        )
        with patch.object(container_quality, "request_shopfloor", request_mock):
            await container_quality.container_defect(
                "SN-0001",
                "Visual Defect",
                1,
                body_json=body_json,
            )

        payload = request_mock.await_args.kwargs["body"]
        self.assertNotIn("Container", payload)
        self.assertNotIn("ServiceDetails", payload)
        self.assertEqual(
            payload["container"],
            {"name": "SN-0002", "level": {"name": "Unit"}},
        )
        self.assertEqual(
            payload["serviceDetails"],
            [
                {
                    "reasonCode": {"name": "Electrical Defect"},
                    "defectCount": 3,
                }
            ],
        )

    async def test_rework_workflow_requires_revision(self):
        request_mock = AsyncMock(return_value="ok")
        with patch.object(container_quality, "request_shopfloor", request_mock):
            result = await container_quality.rework(
                "SN-0001",
                "Repair Required",
                to_workflow_name="REWORK-WF",
            )

        self.assertIn("must be provided together", result)
        request_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

import json
import unittest
from unittest.mock import AsyncMock, patch

from tools import container_moves


class ContainerMoveTests(unittest.IsolatedAsyncioTestCase):
    async def test_move_std_default_payload(self):
        request_mock = AsyncMock(return_value="ok")
        with patch.object(container_moves, "request_shopfloor", request_mock):
            result = await container_moves.container_move(
                container_name="SN-0001",
                container_level_name="PCB",
                path_name="Default Path",
                resource_name="LINE-01",
            )

        self.assertEqual(result, "ok")
        self.assertEqual(
            request_mock.await_args.args,
            ("POST", "/api/MoveStd"),
        )
        self.assertEqual(
            request_mock.await_args.kwargs["body"],
            {
                "container": {
                    "name": "SN-0001",
                    "level": {"name": "PCB"},
                },
                "close": False,
                "moveAllQty": True,
                "path": {"name": "Default Path"},
                "resource": {"name": "LINE-01"},
            },
        )

    async def test_move_in_payload(self):
        request_mock = AsyncMock(return_value="ok")
        with patch.object(container_moves, "request_shopfloor", request_mock):
            await container_moves.container_move_in(
                container_name="SN-0001",
                resource_name="LINE-01",
                comments="begin processing",
            )

        self.assertEqual(
            request_mock.await_args.args,
            ("POST", "/api/MoveIn"),
        )
        self.assertEqual(
            request_mock.await_args.kwargs["body"],
            {
                "container": {"name": "SN-0001"},
                "resource": {"name": "LINE-01"},
                "comments": "begin processing",
            },
        )

    async def test_move_out_partial_quantity_payload(self):
        request_mock = AsyncMock(return_value="ok")
        with patch.object(container_moves, "request_shopfloor", request_mock):
            await container_moves.container_move_out(
                container_name="LOT-0001",
                move_all_qty=False,
                qty=5,
                thruput_all_qty=False,
                to_resource_name="LINE-02",
            )

        self.assertEqual(
            request_mock.await_args.args,
            ("POST", "/api/MoveOut"),
        )
        self.assertEqual(
            request_mock.await_args.kwargs["body"],
            {
                "container": {"name": "LOT-0001"},
                "thruputAllQty": False,
                "close": False,
                "moveAllQty": False,
                "qty": 5,
                "toResource": {"name": "LINE-02"},
            },
        )

    async def test_partial_quantity_validation(self):
        request_mock = AsyncMock(return_value="ok")
        with patch.object(container_moves, "request_shopfloor", request_mock):
            missing = await container_moves.container_move(
                "LOT-1", move_all_qty=False
            )
            conflicting = await container_moves.container_move_out(
                "LOT-1", move_all_qty=True, qty=1
            )

        self.assertIn("greater than 0", missing)
        self.assertIn("must be omitted", conflicting)
        request_mock.assert_not_awaited()

    async def test_body_json_merges_without_case_duplicates(self):
        request_mock = AsyncMock(return_value="ok")
        with patch.object(container_moves, "request_shopfloor", request_mock):
            await container_moves.container_move_in(
                container_name="SN-0001",
                body_json=json.dumps(
                    {
                        "Container": {
                            "Name": "SN-0002",
                            "Level": {"Name": "PCB"},
                        },
                        "Comments": "override",
                    }
                ),
            )

        payload = request_mock.await_args.kwargs["body"]
        self.assertNotIn("Container", payload)
        self.assertNotIn("Comments", payload)
        self.assertEqual(payload["container"]["name"], "SN-0002")
        self.assertEqual(payload["container"]["level"], {"name": "PCB"})
        self.assertEqual(payload["comments"], "override")


if __name__ == "__main__":
    unittest.main()

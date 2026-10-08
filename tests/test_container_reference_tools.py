import unittest
from unittest.mock import AsyncMock, patch

from tools import container_levels, numbering_rules


class ContainerReferenceToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_numbering_rules_builds_odata_query(self):
        request_mock = AsyncMock(return_value="ok")

        with patch.object(numbering_rules, "request", request_mock):
            result = await numbering_rules.list_numbering_rules(
                filter_expr="Name eq 'UNIT-SN'",
                top=10,
                select="Name,Prefix,SequenceLength",
                orderby="Name asc",
            )

        self.assertEqual(result, "ok")
        self.assertEqual(
            request_mock.await_args.args,
            ("GET", "/api/NumberingRules"),
        )
        self.assertEqual(
            request_mock.await_args.kwargs["params"],
            {
                "$filter": "Name eq 'UNIT-SN'",
                "$top": 10,
                "$select": "Name,Prefix,SequenceLength",
                "$orderby": "Name asc",
            },
        )

    async def test_get_numbering_rule(self):
        request_mock = AsyncMock(return_value="ok")

        with patch.object(numbering_rules, "request", request_mock):
            await numbering_rules.get_numbering_rule("UNIT-SN")

        request_mock.assert_awaited_once_with(
            "GET", "/api/NumberingRules/UNIT-SN"
        )

    async def test_list_container_levels_builds_odata_query(self):
        request_mock = AsyncMock(return_value="ok")

        with patch.object(container_levels, "request", request_mock):
            result = await container_levels.list_container_levels(
                filter_expr="Name eq 'Unit'",
                top=10,
                select="Name,ContainerNumberingRule",
                expand="ContainerNumberingRule",
            )

        self.assertEqual(result, "ok")
        self.assertEqual(
            request_mock.await_args.args,
            ("GET", "/api/ContainerLevels"),
        )
        self.assertEqual(
            request_mock.await_args.kwargs["params"],
            {
                "$filter": "Name eq 'Unit'",
                "$top": 10,
                "$select": "Name,ContainerNumberingRule",
                "$expand": "ContainerNumberingRule",
            },
        )

    async def test_get_container_level(self):
        request_mock = AsyncMock(return_value="ok")

        with patch.object(container_levels, "request", request_mock):
            await container_levels.get_container_level("Unit")

        request_mock.assert_awaited_once_with(
            "GET", "/api/ContainerLevels/Unit"
        )


if __name__ == "__main__":
    unittest.main()

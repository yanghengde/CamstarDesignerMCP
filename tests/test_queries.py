import json
import unittest
from unittest.mock import AsyncMock, patch

from core import response
from tools import queries


class QueryToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_query_services_parses_swagger_index(self):
        config = {
            "urls": [
                {"url": "../swagger/AdHoc/swagger.json", "name": "AdHoc"},
                {
                    "url": "../swagger/ContainerInquiry/swagger.json",
                    "name": "ContainerInquiry",
                },
                {
                    "url": "../swagger/ContainerInfoInquiry/swagger.json",
                    "name": "ContainerInfoInquiry",
                },
            ]
        }
        html = (
            "<script>var configObject = JSON.parse('"
            + json.dumps(config)
            + "');</script>"
        )
        fetch_mock = AsyncMock(return_value=html)

        with patch.object(queries, "_fetch_query_text", fetch_mock):
            result = await queries.list_query_services("container")

        data = json.loads(result)
        self.assertEqual(data["count"], 2)
        self.assertEqual(
            data["services"],
            ["ContainerInfoInquiry", "ContainerInquiry"],
        )
        fetch_mock.assert_awaited_once_with("/swagger/index.html")

    async def test_get_query_service_schema_summarizes_request(self):
        swagger = {
            "paths": {
                "/api/TestInquiry": {
                    "post": {
                        "operationId": "POST:api/TestInquiry",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": (
                                            "#/components/schemas/"
                                            "TestInquiry"
                                        )
                                    }
                                }
                            }
                        },
                        "parameters": [],
                    }
                }
            },
            "components": {
                "schemas": {
                    "TestInquiry": {
                        "required": ["container"],
                        "properties": {
                            "container": {},
                            "product": {},
                        },
                    }
                }
            },
        }
        fetch_mock = AsyncMock(return_value=swagger)

        with patch.object(queries, "_fetch_query_json", fetch_mock):
            result = await queries.get_query_service_schema(
                "TestInquiry",
                field_filter="container",
            )

        data = json.loads(result)
        self.assertEqual(data["service"], "TestInquiry")
        self.assertEqual(data["paths"][0]["method"], "POST")
        self.assertEqual(
            data["requestSchemas"]["TestInquiry"],
            {
                "required": ["container"],
                "fieldCount": 2,
                "fields": ["container"],
            },
        )

    async def test_execute_inquiry_builds_query_parameters(self):
        request_mock = AsyncMock(return_value="ok")
        with patch.object(queries, "request_query", request_mock):
            result = await queries.execute_query_inquiry(
                cdo_name="ComponentIssueInquiry",
                body_json=json.dumps({"ObjectName": "Container1"}),
                select="Container,Product,Qty",
                expand="IssueDetails",
                include_execute_node=True,
            )

        self.assertEqual(result, "ok")
        request_mock.assert_awaited_once_with(
            "POST",
            "/api/ComponentIssueInquiry",
            body={"ObjectName": "Container1"},
            params={
                "$select": "Container,Product,Qty",
                "$expand": "IssueDetails",
                "execute": "true",
            },
        )

    async def test_request_selection_values_uses_current_server_route(self):
        request_mock = AsyncMock(return_value="ok")
        with patch.object(queries, "request_query", request_mock):
            await queries.request_query_selection_values(
                "ContainerInfoInquiry",
                "Container",
                body_json=json.dumps({"Factory": {"Name": "Factory1"}}),
            )

        request_mock.assert_awaited_once_with(
            "POST",
            "/api/ContainerInfoInquiry/RequestSelectionValues",
            body={"Factory": {"Name": "Factory1"}},
            params={"selectionValuesExpression": "Container"},
        )

    async def test_execute_inquiry_event_is_explicit(self):
        request_mock = AsyncMock(return_value="ok")
        with patch.object(queries, "request_query", request_mock):
            await queries.execute_query_inquiry_event(
                "SomeInquiry",
                "RefreshResults",
                body_json=json.dumps({"ObjectName": "Container1"}),
                select="CompletionMsg",
            )

        request_mock.assert_awaited_once_with(
            "POST",
            "/api/SomeInquiry",
            body={"ObjectName": "Container1"},
            params={
                "eventname": "RefreshResults",
                "$select": "CompletionMsg",
            },
        )

    async def test_advanced_query_accepts_parameter_mapping(self):
        request_mock = AsyncMock(return_value="ok")
        with patch.object(queries, "request_query", request_mock):
            await queries.execute_advanced_query(
                "GetEmployeesByRole",
                parameters_json=json.dumps({"Role": "role-instance-id"}),
            )

        request_mock.assert_awaited_once_with(
            "POST",
            "/api/AdvancedQuery/GetEmployeesByRole",
            body={
                "queryType": "system",
                "parameters": [
                    {"name": "Role", "value": "role-instance-id"}
                ],
            },
        )

    async def test_user_query_uses_lowercase_user_type(self):
        request_mock = AsyncMock(return_value="ok")
        with patch.object(queries, "request_query", request_mock):
            await queries.execute_user_query("My Query")

        request_mock.assert_awaited_once_with(
            "POST",
            "/api/AdvancedQuery/My%20Query",
            body={"queryType": "user"},
        )

    async def test_adhoc_query_allows_one_select(self):
        request_mock = AsyncMock(return_value="ok")
        with patch.object(queries, "request_query", request_mock):
            result = await queries.execute_adhoc_query(
                "SELECT ContainerId, ContainerName FROM Container",
                parameters_json=json.dumps(
                    [{"name": "Factory", "value": "Factory1"}]
                ),
            )

        self.assertEqual(result, "ok")
        request_mock.assert_awaited_once_with(
            "POST",
            "/api/AdHoc",
            body={
                "queryText": (
                    "SELECT ContainerId, ContainerName FROM Container"
                ),
                "parameters": [
                    {"name": "Factory", "value": "Factory1"}
                ],
            },
        )

    async def test_adhoc_query_rejects_write_and_multiple_statements(self):
        request_mock = AsyncMock(return_value="ok")
        with patch.object(queries, "request_query", request_mock):
            update_result = await queries.execute_adhoc_query(
                "UPDATE Container SET ContainerName='X'"
            )
            multi_result = await queries.execute_adhoc_query(
                "SELECT ContainerName FROM Container; DELETE FROM Container"
            )
            select_into_result = await queries.execute_adhoc_query(
                "SELECT ContainerName INTO Backup FROM Container"
            )

        self.assertIn("restricted to a SELECT", update_result)
        self.assertIn("exactly one SELECT", multi_result)
        self.assertIn("INTO", select_into_result)
        request_mock.assert_not_awaited()

    def test_large_query_response_preserves_custom_columns(self):
        rows = [
            {
                "ContainerId": f"id-{index}",
                "ContainerName": f"SN{index:07d}",
                "CustomConfiguredColumn": "x" * 50,
            }
            for index in range(30)
        ]

        with patch.object(response, "MAX_RESPONSE_LENGTH", 700):
            result = response.smart_query_response(rows)

        self.assertIn("Query response was too large", result)
        self.assertIn("ContainerId", result)
        self.assertIn("CustomConfiguredColumn", result)
        self.assertNotIn("id-29", result)


if __name__ == "__main__":
    unittest.main()

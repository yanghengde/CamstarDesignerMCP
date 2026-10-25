"""Requests shared by the test workbench API, saved cases and execution boundary."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Environment(StrictModel):
    project_id: str = Field(default='', pattern=r'^(?:[0-9a-f]{32})?$')
    artifact_id: str = Field(default='', pattern=r'^(?:[0-9a-f]{32})?$')
    name: str = Field(min_length=1, max_length=100)
    endpoint: str = Field(min_length=1, max_length=1000)
    business_user: str = Field(min_length=1, max_length=200)
    password: str | None = Field(default=None, max_length=2000)
    server_name: str = Field(default='', max_length=200)
    timeout: int = Field(default=60, ge=5, le=180)
    version: int = Field(default=0, ge=0)


class Assertion(StrictModel):
    path: str = Field(min_length=1, max_length=300, pattern=r'^[A-Za-z_][A-Za-z0-9_.]*$')
    operator: Literal['exists', 'equals', 'contains'] = 'equals'
    value: object = None


class TestSpec(StrictModel):
    kind: Literal['wcf', 'clf'] = 'wcf'
    project_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    artifact_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    client_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    environment_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    environment_version: int = Field(ge=1)
    service: str = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]*$', max_length=200)
    method: str = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]*$', max_length=200)
    data: dict = Field(default_factory=dict)
    return_fields: list[str] = Field(default_factory=list, max_length=100)
    selection_limit: int = Field(default=0, ge=0, le=5)
    expected_outcome: Literal['success', 'rejected'] = 'success'
    message_contains: str = Field(default='', max_length=2000)
    assertions: list[Assertion] = Field(default_factory=list, max_length=100)
    clf_name: str = Field(default='', max_length=200)
    collect_trace: bool = False
    allow_write: bool = False
    readback: bool = False
    readback_data: dict = Field(default_factory=dict)

    @model_validator(mode='after')
    def validate_clf(self):
        if self.selection_limit and (self.method != 'GetEnvironment' or self.data or self.allow_write):
            raise ValueError('列表查询只允许不带写入数据的 GetEnvironment 请求')
        if self.kind == 'clf' and not self.clf_name:
            raise ValueError('CLF 测试需要选择真实 CLF')
        for path in self.return_fields:
            Assertion(path=path, operator='exists')
        return self


class RunRequest(StrictModel):
    username: str
    request_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    spec: TestSpec


class CaseRequest(StrictModel):
    username: str
    name: str = Field(min_length=1, max_length=200)
    spec: TestSpec
    group: Literal['normal', 'exception', 'boundary', 'custom'] = 'custom'
    parent_run_id: str = Field(default='', pattern=r'^(?:[0-9a-f]{32})?$')
    parent_case_id: str = Field(default='', pattern=r'^(?:[0-9a-f]{32})?$')


class BatchRequest(StrictModel):
    username: str
    request_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    case_ids: list[str] = Field(min_length=1, max_length=30)
    allow_write: bool = False

"""Focused, read-only CLF MCP tools; saving remains in the existing designer flow."""
import asyncio
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from designer import clf
from designer.clf_parameters import AddDeclaration, UpdateDeclaration, RemoveDeclaration, MigrateBinding, MigrateCall
from tools import mcp


class ClfArguments(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class CreateClf(ClfArguments):
    action: Literal['create_clf']
    name: str
    clf_type: str
    description: str = ''


class CopyClf(ClfArguments):
    action: Literal['copy_clf']
    name: str
    template: str


class AddCall(ClfArguments):
    action: Literal['add_clf_function']
    owner: str
    function: str
    sequence: int = Field(ge=1)


class ReplaceCall(ClfArguments):
    action: Literal['replace_clf_function']
    owner: str
    call_id: int = Field(ge=1)
    function: str
    expected_function: str


class RemoveCall(ClfArguments):
    action: Literal['remove_clf_function']
    owner: str
    call_id: int = Field(ge=1)
    expected_function: str


class ReorderCalls(ClfArguments):
    action: Literal['reorder_clf_functions']
    owner: str
    function_ids: list[int]
    expected_function_ids: list[int]


class SetParameter(ClfArguments):
    action: Literal['set_clf_parameter']
    owner: str
    call_id: int = Field(ge=1)
    parameter: str
    value: str
    expected_value: str


class BindEvent(ClfArguments):
    action: Literal['bind_event']
    owner: str
    event: str
    clf: str
    feature: str
    field: str = ''


class ReplaceEvent(ClfArguments):
    action: Literal['replace_event_binding']
    owner: str
    event: str
    clf: str
    feature: str
    expected_clf: str
    field: str = ''


class UnbindEvent(ClfArguments):
    action: Literal['unbind_event']
    owner: str
    event: str
    expected_clf: str
    field: str = ''


ClfOperation = Annotated[CreateClf | CopyClf | AddCall | ReplaceCall | RemoveCall | ReorderCalls |
                         SetParameter | BindEvent | ReplaceEvent | UnbindEvent |
                         AddDeclaration | UpdateDeclaration | RemoveDeclaration | MigrateBinding | MigrateCall, Field(discriminator='action')]
_operation = TypeAdapter(ClfOperation)


class ClfTestCase(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=200)
    inputs: dict = Field(default_factory=dict)
    expected: str = Field(min_length=1, max_length=3000)


@mcp.tool
async def list_designer_clfs(mdb_file: str, search: str = '', offset: int = 0, limit: int = 50) -> dict:
    """只读搜索当前 MDB 的真实 CLF，返回名称、说明、工作区和来源 SHA256；按 total 分页。"""
    return await asyncio.to_thread(clf.list_clfs, mdb_file, search, offset, limit)


@mcp.tool
async def get_designer_clf(mdb_file: str, name: str, expected_sha256: str = '', include_analysis: bool = True) -> dict:
    """只读一次获取 CLF 有序调用、表达式、声明参数、官方引用及直接函数定义、函数参数和显式 LabelId 标签。

    解释规则优先一次调用本工具，按返回证据回答；不要例行再查整份业务对象、相同 CLF/schema 或重复分页原始回执。
    include_analysis=false 只读取调用与引用。重复函数按 call_id 区分；引用/参数失败或截断需说明，嵌套 CLF、callsuper 和动态调用不属于自动展开范围。
    """
    read = clf.analysis_context if include_analysis else clf.get_clf
    return await asyncio.to_thread(read, mdb_file, name, expected_sha256)


@mcp.tool
async def inspect_designer_clf_plan(mdb_file: str, expected_sha256: str, operations: list[ClfOperation],
                                    workspace: str = '', requirements: list[str] | None = None,
                                    test_cases: list[ClfTestCase] | None = None) -> dict:
    """只读预检 CLF 完整方案，返回修改前/官方内存预演后的调用与参数、冲突及待执行测试用例。

    参数契约沿用 generate_designer_design_package 的 CLF 专用 actions。先读取真实 call_id、函数、参数和原值。
    支持 create_clf/copy_clf、调用增删替换/排序、set_clf_parameter、事件绑定及声明参数专用增改删。
    migrate_clf_binding 需读取工作区返回的完整入口迁移合同，显式提供目标独立客户 CLF，保留全部入口表达式；不自动处理系统/继承入口或直接调用者。
    migrate_clf_call 需按需读取直接调用和全部入口快照，明确目标声明、库表达式和各入口的完整拟定表达式；仅独立客户调用者，同一调用者每份方案一个调用。
    声明参数使用 add/update/remove_clf_declaration，definition/expected 属性沿用官方名称；已有入口或调用者时结构变更需单独迁移。
    不 SaveAll、不编译、不更新数据库、不运行测试，metadata_preflight_passed 不能当作业务验证通过。
    """
    operations = [_operation.validate_python(item).model_dump(exclude_unset=True) for item in operations]
    cases = [(item if isinstance(item, ClfTestCase) else ClfTestCase.model_validate(item)).model_dump()
             for item in test_cases or []]
    return await asyncio.to_thread(clf.inspect_plan, mdb_file, expected_sha256, operations, workspace, requirements, cases)

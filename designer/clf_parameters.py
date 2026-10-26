"""Strict declaration contracts; these are not function-call expression values."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Declaration(Strict):
    Name: str = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]{0,29}$')
    Direction: Literal['In', 'Out', 'In/Out', 'Return']
    DataType: str = Field(min_length=1, max_length=100)
    ParamMultiplicity: Literal['0..1', '1..1', '0..*', '1..*']
    TypeMultiplicity: Literal['Scalar', 'List', 'ListOrScalar']
    FunctionParmUsage: str = Field(default='', max_length=100)
    CDOType: str = Field(default='', max_length=100)
    Description: str = Field(default='', max_length=3000)
    Notes: str = Field(default='', max_length=10000)
    DefaultValue: str = Field(default='', max_length=10000)

    @model_validator(mode='after')
    def object_type(self):
        if self.CDOType and self.DataType != 'Object':
            raise ValueError('CDOType 仅用于 Object 参数')
        return self


class AddDeclaration(Strict):
    action: Literal['add_clf_declaration']
    owner: str = Field(min_length=1, max_length=100)
    definition: Declaration


class UpdateDeclaration(Strict):
    action: Literal['update_clf_declaration']
    owner: str = Field(min_length=1, max_length=100)
    parameter: str = Field(min_length=1, max_length=100)
    expected: Declaration
    definition: Declaration


class RemoveDeclaration(Strict):
    action: Literal['remove_clf_declaration']
    owner: str = Field(min_length=1, max_length=100)
    parameter: str = Field(min_length=1, max_length=100)
    expected: Declaration


class AttachedValue(Strict):
    value_id: int = Field(ge=1)
    call_id: int = Field(ge=1)
    parameter_id: int = Field(ge=1)
    parameter: str = Field(min_length=1, max_length=100)
    value: str = Field(max_length=100000)
    dynamic: bool


class MigrateBinding(Strict):
    action: Literal['migrate_clf_binding']
    owner: str = Field(min_length=1, max_length=100)
    field: str = Field(default='', max_length=100)
    event: str = Field(min_length=1, max_length=100)
    binding_id: int = Field(ge=1)
    expected_clf: str = Field(min_length=1, max_length=100)
    clf: str = Field(min_length=1, max_length=100)
    expected_feature_id: int = Field(ge=0)
    expected_parameters: list[AttachedValue] = Field(max_length=5000)

    @model_validator(mode='after')
    def unique_identity(self):
        if self.clf.casefold() == self.expected_clf.casefold():
            raise ValueError('入口迁移必须指定不同的客户规则')
        if len({p.value_id for p in self.expected_parameters}) != len(self.expected_parameters):
            raise ValueError('入口参数身份重复')
        if len({(p.call_id, p.parameter_id) for p in self.expected_parameters}) != len(self.expected_parameters):
            raise ValueError('入口调用参数重复')
        return self


class CallValue(Strict):
    parameter: str = Field(min_length=1, max_length=100)
    value: str = Field(max_length=100000)
    dynamic: bool


class CallerBinding(Strict):
    binding_id: int = Field(ge=1)
    cdo_id: int = Field(ge=1)
    caller_id: int = Field(ge=1)
    event_id: int = Field(ge=1)
    feature_id: int = Field(ge=0)
    owner: str = Field(min_length=1, max_length=100)
    field: str = Field(max_length=100)
    event: str = Field(min_length=1, max_length=100)
    parameters: list[AttachedValue] = Field(max_length=5000)


class CallBindingValues(Strict):
    binding_id: int = Field(ge=1)
    parameters: list[CallValue] = Field(max_length=50)


class MigrateCall(Strict):
    action: Literal['migrate_clf_call']
    owner: str = Field(min_length=1, max_length=100)
    call_id: int = Field(ge=1)
    expected_clf: str = Field(min_length=1, max_length=100)
    clf: str = Field(min_length=1, max_length=100)
    expected_sequence: int = Field(ge=1)
    expected_active: bool
    expected_parameters: list[AttachedValue] = Field(max_length=50)
    expected_bindings: list[CallerBinding] = Field(max_length=20)
    target_declarations: list[Declaration] = Field(max_length=50)
    parameters: list[CallValue] = Field(max_length=50)
    bindings: list[CallBindingValues] = Field(max_length=20)

    @model_validator(mode='after')
    def complete_scope(self):
        if self.clf.casefold() in {self.expected_clf.casefold(), self.owner.casefold()} or self.owner.casefold() == self.expected_clf.casefold():
            raise ValueError('直接调用迁移需要不同的调用者、原规则和目标客户规则')
        names = [p.Name for p in self.target_declarations]
        if len({n.casefold() for n in names}) != len(names):
            raise ValueError('目标声明参数重复')
        for parameters in [self.expected_parameters, *[b.parameters for b in self.expected_bindings]]:
            if len({p.value_id for p in parameters}) != len(parameters) or len({(p.call_id,p.parameter_id) for p in parameters}) != len(parameters):
                raise ValueError('原参数身份重复')
        before, after = [b.binding_id for b in self.expected_bindings], [b.binding_id for b in self.bindings]
        if len(set(before)) != len(before) or len(set(after)) != len(after) or set(before) != set(after):
            raise ValueError('必须核对调用者的全部入口，不得遗漏或增加入口')
        for parameters in [self.parameters, *[b.parameters for b in self.bindings]]:
            actual = [p.parameter for p in parameters]
            if len(set(actual)) != len(actual) or set(actual) != set(names):
                raise ValueError('每组拟定表达式必须覆盖全部目标声明参数，且不得重复')
        return self


ADAPTERS = {model.model_fields['action'].annotation.__args__[0]: TypeAdapter(model)
            for model in (AddDeclaration, UpdateDeclaration, RemoveDeclaration, MigrateBinding, MigrateCall)}


def values(record):
    return {key: record.get(key) or '' for key in Declaration.model_fields}

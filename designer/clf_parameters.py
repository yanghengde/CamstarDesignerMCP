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


ADAPTERS = {model.model_fields['action'].annotation.__args__[0]: TypeAdapter(model)
            for model in (AddDeclaration, UpdateDeclaration, RemoveDeclaration, MigrateBinding)}


def values(record):
    return {key: record.get(key) or '' for key in Declaration.model_fields}

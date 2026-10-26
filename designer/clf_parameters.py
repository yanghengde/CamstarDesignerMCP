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


ADAPTERS = {model.model_fields['action'].annotation.__args__[0]: TypeAdapter(model)
            for model in (AddDeclaration, UpdateDeclaration, RemoveDeclaration)}


def values(record):
    return {key: record.get(key) or '' for key in Declaration.model_fields}

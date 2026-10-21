"""One field contract shared by SDK output, public tools and WCF acceptance."""
from typing_extensions import NotRequired, TypedDict
from pydantic import ConfigDict
import re


class ExpectedField(TypedDict):
    __pydantic_config__ = ConfigDict(extra='forbid', strict=True)
    owner: str
    name: str
    present: bool
    data_type: NotRequired[str]
    field_type: NotRequired[str]
    precision: NotRequired[int]
    scale: NotRequired[int]
    is_list: NotRequired[bool]


CLR_TYPES = {'String': 'System.String', 'Integer': 'System.Int32', 'Boolean': 'System.Boolean',
             'Float': 'System.Double', 'Fixed': 'System.Decimal', 'Timestamp': 'System.DateTime'}


def _metadata_name(value):
    # Existing vendor definitions may use names forbidden for *new* designs.
    # Reflection checks need valid identifier syntax, not creation-time policy.
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', value):
        raise ValueError(f'验收名称格式不正确：{value}')


def names(value, label='验收对象', *, limit=None):
    if not isinstance(value, list) or not value:
        raise ValueError(f'{label}需要至少一个名称')
    result = []
    seen = set()
    for index, name in enumerate(value, 1):
        if not isinstance(name, str):
            raise ValueError(f'{label}第 {index} 项必须为名称字符串')
        _metadata_name(name)
        if name.casefold() in seen:
            raise ValueError(f'{label}名称重复：{name}')
        seen.add(name.casefold())
        result.append(name)
    if limit is not None and len(result) > limit:
        raise ValueError(f'{label}最多支持 {limit} 个名称')
    return result


def fields(value, verify_types=None, *, enriched=False):
    """Accept legacy presence checks and the richer schema emitted by the SDK."""
    if not isinstance(value, list):
        raise ValueError('expected_fields 必须为字段验收列表')
    scope = set(verify_types) if verify_types is not None else None
    result, seen = [], set()
    for index, row in enumerate(value, 1):
        label = f'expected_fields 第 {index} 项'
        if not isinstance(row, dict):
            raise ValueError(label + '必须为字段对象')
        unknown = set(row) - ExpectedField.__annotations__.keys()
        if unknown:
            raise ValueError(label + '包含未知属性：' + '、'.join(sorted(map(str, unknown))))
        for key in ('owner', 'name'):
            if not isinstance(row.get(key), str):
                raise ValueError(label + f'缺少有效的 {key} 名称')
            _metadata_name(row[key])
        label += f"（{row['owner']}.{row['name']}）"
        if type(row.get('present')) is not bool:
            raise ValueError(label + '的 present 必须为布尔值')
        if scope is not None and row['owner'] not in scope:
            raise ValueError(label + '的 owner 未纳入验收对象')
        key = (row['owner'].casefold(), row['name'].casefold())
        if key in seen:
            raise ValueError(label + '重复定义了同一字段的验收规则')
        seen.add(key)
        for key in ('data_type', 'field_type'):
            if key in row and (not isinstance(row[key], str) or not row[key].strip()):
                raise ValueError(label + f'的 {key} 必须为非空字符串')
        for key in ('precision', 'scale'):
            if key in row and (type(row[key]) is not int or row[key] < 0):
                raise ValueError(label + f'的 {key} 必须为非负整数')
        if 'is_list' in row and type(row['is_list']) is not bool:
            raise ValueError(label + '的 is_list 必须为布尔值')
        if ('data_type' in row) != ('is_list' in row):
            raise ValueError(label + '的 data_type 与 is_list 必须一并提供')
        if enriched and row['present'] and not {'data_type', 'is_list', 'field_type', 'precision', 'scale'}.issubset(row):
            raise ValueError(label + '缺少编译 MDB 的完整类型、长度和列表信息')
        result.append(dict(row))
    return result


def _contracts(value, label):
    if not isinstance(value, list):
        raise ValueError(f'WCF {label}契约列表格式不正确')
    result = {}
    for row in value:
        if (not isinstance(row, dict) or not isinstance(row.get('name'), str)
                or not isinstance(row.get('properties'), list)
                or any(not isinstance(name, str) for name in row['properties'])
                or not isinstance(row.get('field_schemas', {}), dict)):
            raise ValueError(f'WCF {label}契约结构不完整')
        key = row['name'].split('.')[-1]
        if key in result and (result[key]['name'] != row['name']
                              or set(result[key]['properties']) != set(row['properties'])
                              or result[key].get('field_schemas', {}) != row.get('field_schemas', {})):
            raise ValueError(f'WCF {label}存在冲突的同名契约：{key}')
        result[key] = row
    return result


def verify_result(result, expected_fields, verify_types, *, allow_partial=False):
    """Validate both DLL contracts at the public boundary and in progress replay."""
    verify_types = names(verify_types)
    expected_fields = fields(expected_fields)
    if (result.get('status') != 'services_generated' or result.get('source_unchanged') is not True
            or type(result.get('partial_package', False)) is not bool
            or result.get('partial_package', False) and not allow_partial):
        raise ValueError('WCF 全量生成未通过核对')
    if {row['owner'] for row in expected_fields} - set(verify_types):
        raise ValueError('WCF 验收范围遗漏当前设计字段，不能标记全量通过')
    client = _contracts(result.get('type_checks', []), '客户端')
    server = _contracts(result.get('server_type_checks', []), '服务端')
    for row in expected_fields:
        owner = row['owner'] if row['owner'] in client else row['owner'] + 'Changes'
        if (row['name'] in client.get(owner, {}).get('properties', [])) != row['present']:
            raise ValueError(f"WCF 当前字段不匹配：{row['owner']}.{row['name']}，预期{'存在' if row['present'] else '不存在'}")
        if (row['name'] in server.get(owner, {}).get('properties', [])) != row['present']:
            raise ValueError(f"WCF 服务端当前字段不匹配：{row['owner']}.{row['name']}")
        if row['present'] and 'data_type' in row:
            a = client.get(owner, {}).get('field_schemas', {}).get(row['name'])
            b = server.get(owner, {}).get('field_schemas', {}).get(row['name'])
            if (not isinstance(a, dict) or a != b or type(a.get('is_list')) is not bool
                    or a['is_list'] != row['is_list'] or not isinstance(a.get('type'), str)
                    or not a['type'] or not isinstance(a.get('value_type'), str) or not a['value_type']):
                raise ValueError(f"WCF 客户端/服务端字段契约不一致：{row['owner']}.{row['name']}")
            expected_clr = CLR_TYPES.get(row['data_type'])
            if expected_clr and a['value_type'] != expected_clr:
                raise ValueError(f"WCF 字段 CLR 类型不匹配：{row['owner']}.{row['name']}")
    for name in verify_types:
        contract = name if name in client else name + 'Changes'
        if contract not in client:
            raise ValueError('WCF 缺少验收对象：' + name)
        if contract not in server:
            raise ValueError('WCF 服务端缺少验收对象：' + name)
    if any(type(result.get(key)) is not int or result[key] < 1 for key in ('data_contract_count', 'service_count')) or not isinstance(result.get('files_sha256'), dict) or not result['files_sha256']:
        raise ValueError('WCF 产物或生成计数不完整')
    output = {name.replace('\\', '/').casefold() for name in result['files_sha256'] if isinstance(name, str)}
    if not {'client/camstar.wcfclient.dll', 'server/bin/camstar.wcfservice.dll'}.issubset(output):
        raise ValueError('WCF 客户端或服务端程序集缺失')
    return result

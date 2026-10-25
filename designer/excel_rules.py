"""Locate explicit business-rule columns without interpreting cell text as code."""
import re

HEADERS = {
    'rule': {'业务规则', '业务逻辑', '规则说明', '校验规则', '验证规则', 'clf要求', 'clf规则', 'clf', 'businessrule', 'businessrules', 'validationrule'},
    'owner': {'对象', '对象名', '所属对象', '对象名称', 'cdo', 'cdoname', 'object', 'objectname'},
    'field': {'字段', '字段名', '字段名称', 'field', 'fieldname'},
    'trigger': {'触发时机', '触发事件', '事件', 'trigger', 'event'},
    'error': {'错误提示', '错误信息', 'errormessage'},
}


def extract(document):
    result = []
    for sheet in document.get('sheets', []):
        columns = {}
        for row in sheet['rows']:
            found = {}
            for column, value in row['cells'].items():
                normalized = re.sub(r'[\s_\-：:]+', '', str(value)).casefold()
                for kind, names in HEADERS.items():
                    if normalized in names:
                        found.setdefault(kind, []).append(column)
            if found.get('rule') and (not columns or found.get('owner') or found.get('field')):
                columns = found
                continue
            for column in columns.get('rule', []):
                text = row['cells'].get(column)
                if text is None or text == '':
                    continue
                def value(kind):
                    candidates = [row['cells'][c] for c in columns.get(kind, []) if c in row['cells']]
                    return candidates[0] if len(candidates) == 1 else None
                result.append({'attachment_id': document['id'], 'attachment_sha256': document['sha256'],
                               'file': document['name'], 'sheet': sheet['name'], 'hidden': sheet.get('hidden', False),
                               'row': row['row'], 'cell': f"{column}{row['row']}", 'rule': str(text),
                               'owner': value('owner'), 'field': value('field'), 'trigger': value('trigger'),
                               'error_message': value('error'), 'source_cells': dict(row['cells']),
                               'status': 'needs_analysis'})
    return result

"""Read the documented InSiteMetaData XML subset; do not imply XSD validation."""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from defusedxml.ElementTree import fromstring
from defusedxml.common import DefusedXmlException

MAX_XML_BYTES = 20 * 1024 * 1024
MAX_XML_NODES = 100000
RESERVED_WORDS = frozenset((Path(__file__).with_name("reserved_words.txt")).read_text(encoding="utf-8").split())


def load_xml(path: Path) -> tuple[ET.Element, str]:
    with path.open("rb") as stream:
        data = stream.read(MAX_XML_BYTES + 1)
    if len(data) > MAX_XML_BYTES:
        raise ValueError("XML 超过 20 MiB 限制")
    try:
        root = fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (ET.ParseError, DefusedXmlException) as exc:
        raise ValueError(f"无法安全解析 XML：{exc}") from exc
    if root.tag != "InSiteMetaData":
        raise ValueError("仅支持手册中的 InSiteMetaData XML 格式（无命名空间）")
    if root.findtext("Header/Version") != "1.0" or root.find("Import") is None:
        raise ValueError("XML 必须包含 Header/Version=1.0 和 Import")
    if sum(1 for _ in root.iter()) > MAX_XML_NODES:
        raise ValueError("XML 节点数超出限制")
    stack = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > 128:
            raise ValueError("XML 嵌套深度超过 128 层")
        stack.extend((child, depth + 1) for child in node)
    return root, sha256(data).hexdigest()


def cdo_nodes(root: ET.Element) -> list[ET.Element]:
    return list(root.findall("Import/CDODefinitions/CDODefinition"))


def fields(cdo: ET.Element) -> list[ET.Element]:
    return list(cdo.findall("CDOFieldDefinition")) + list(
        cdo.findall("CDOFieldDefinitions/CDOFieldDefinition")
    )


def find_named(nodes: list[ET.Element], name: str) -> ET.Element:
    matches = [node for node in nodes if node.get("Name", "").casefold() == name.casefold()]
    if len(matches) != 1:
        raise ValueError(f"定义 {name} 不存在或存在重复，无法唯一定位")
    return matches[0]


def effective(node: ET.Element) -> ET.Element:
    """Resolve export NewValue/ExpectedValue wrappers to their effective value."""
    result = deepcopy(node)
    for item in result.iter():
        new = item.find("NewValue")
        if new is not None:
            item.text = new.text
            item[:] = [deepcopy(child) for child in new]
        item.attrib.pop("Action", None)
    return result


def canonical(node: ET.Element) -> tuple:
    children = tuple(canonical(child) for child in node)
    return (
        node.tag, tuple(sorted(node.attrib.items())), (node.text or "").strip(),
        tuple(sorted(children, key=repr)) if node.tag == "Attributes" else children,
    )


def definition_index(root: ET.Element) -> dict[str, tuple]:
    index = {}

    def visit(node: ET.Element, context: str):
        # Official CLF diffs carry separate before/after function lists. A
        # function may appear more than once in either list; Sequence identifies
        # its call site, while Name identifies the shared function definition.
        if node.tag in {"BaseCLFFunctions", "NewCLFFunctions"}:
            context = f"{context}/{node.tag}"
        name = node.get("Name")
        if name is not None:
            identity = name.casefold()
            if node.tag == "CLFFunction":
                sequence = effective(node).findtext("Attributes/Sequence")
                if sequence is None or not sequence.strip().isdigit():
                    raise ValueError("CLFFunction 必须包含有效的 Attributes/Sequence")
                identity += f"@{int(sequence)}"
            context = f"{context}/{node.tag}:{identity}"
            if context in index:
                raise ValueError(f"XML 存在重复定义：{context}")
            own = ET.Element(node.tag, node.attrib)
            own.text = node.text
            own.extend(
                deepcopy(child) for child in node
                if child.tag == "Attributes" or not any("Name" in item.attrib for item in child.iter())
            )
            index[context] = canonical(effective(own))
        for child in node:
            if child.tag != "Attributes":
                visit(child, context)

    visit(root.find("Import"), "")
    return index


def validate(root: ET.Element) -> dict:
    index = definition_index(root)
    for node in root.iter():
        if "Action" in node.attrib and node.attrib["Action"] not in {"Create", "Import", "Rename"}:
            raise ValueError(f"未知的 Action：{node.attrib['Action']}")
        if node.find("ExpectedValue") is not None and node.find("NewValue") is None:
            raise ValueError(f"{node.tag} 有 ExpectedValue，但没有 NewValue")
    return {
        "valid": True, "validation_scope": "documented_structure_only",
        "definition_count": len(index), "cdo_count": len(cdo_nodes(root)),
        "warnings": [
            "未执行 Siemens XSD 或 Designer 导入验证。",
            "导出 XML 可能仅包含差异，不能据此证明字段在目标 MDB 中不存在。",
        ],
    }


def identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("标识符必须以字母或下划线开头，只能包含英文字母、数字和下划线")
    if value.casefold() in RESERVED_WORDS or value.casefold().startswith("sys_"):
        raise ValueError(f"{value} 是 Designer 手册附录 A 中的保留字")
    return value


def build_field_draft(
    root: ET.Element, target_cdo: str, template_cdo: str, template_field: str,
    field_name: str, description: str | None,
) -> tuple[ET.Element, dict]:
    """Clone a conservative nonpersistent field subset from supplied metadata."""
    for value in (target_cdo, template_cdo, template_field, field_name):
        identifier(value)
    validate(root)
    cdos = cdo_nodes(root)
    target = find_named(cdos, target_cdo)
    if any(f.get("Name", "").casefold() == field_name.casefold() for f in fields(target)):
        raise ValueError("目标 CDO 已包含同名字段")
    seen = {target_cdo.casefold()}
    parent = effective(target).findtext("Attributes/ParentCDO/Name")
    missing_parent = None
    while parent:
        if parent.casefold() in seen:
            raise ValueError("发现 CDO 继承循环")
        seen.add(parent.casefold())
        candidates = [c for c in cdos if c.get("Name", "").casefold() == parent.casefold()]
        if not candidates:
            missing_parent = parent
            break
        ancestor = find_named(cdos, parent)
        if any(f.get("Name", "").casefold() == field_name.casefold() for f in fields(ancestor)):
            raise ValueError("父 CDO 已定义同名字段；本阶段不生成字段覆盖")
        parent = effective(ancestor).findtext("Attributes/ParentCDO/Name")
    template_owner = find_named(cdos, template_cdo)
    template = find_named(fields(template_owner), template_field)
    if set(template.attrib) - {"Name", "Action"}:
        raise ValueError("模板包含未验证的标识属性，不能复制")
    if any(child.tag != "Attributes" for child in template):
        raise ValueError("仅支持含 Attributes 的简单字段模板，不支持映射或嵌套定义")
    draft = effective(template)
    attrs = draft.find("Attributes")
    if attrs is None or not len(attrs):
        raise ValueError("字段模板缺少属性，无法保留类型")
    allowed_refs = {"FieldDef", "DataType", "FieldType", "DataTypeDef"}
    for attr in attrs:
        low = attr.tag.casefold()
        if any(word in low for word in ("column", "table", "map", "fieldid", "cdoid")):
            raise ValueError(f"模板含存储映射或标识属性 {attr.tag}，需要专门验证的映射模板")
        if ("persistent" in low or "list" in low) and (attr.text or "").strip().casefold() not in {"false", "0"}:
            raise ValueError("仅支持非持久化、非列表字段模板")
        if len(attr) and (attr.tag not in allowed_refs or any(child.tag != "Name" or len(child) for child in attr)):
            raise ValueError(f"模板含未验证的复合引用 {attr.tag}")
        if attr.attrib or any(child.attrib for child in attr):
            raise ValueError("模板属性含未验证的 XML 属性")
    if not any(attr.tag in allowed_refs for attr in attrs):
        raise ValueError("模板必须包含已知类型引用 FieldDef/DataType/FieldType/DataTypeDef")
    if description is not None:
        if any(
            ord(char) not in (9, 10, 13)
            and not (32 <= ord(char) <= 0xD7FF or 0xE000 <= ord(char) <= 0xFFFD or 0x10000 <= ord(char) <= 0x10FFFF)
            for char in description
        ):
            raise ValueError("描述包含 XML 1.0 不允许的字符")
        node = attrs.find("FieldDescription")
        if node is None:
            node = ET.SubElement(attrs, "FieldDescription")
        node[:] = []
        node.text = description
    draft.set("Name", field_name)
    draft.set("Action", "Create")
    output = ET.Element("InSiteMetaData")
    header = ET.SubElement(output, "Header")
    ET.SubElement(header, "Version").text = "1.0"
    expected = ET.SubElement(ET.SubElement(header, "Defaults"), "ExpectedValues")
    for tag in ("ActionIfDifferent", "ActionIfDifferentForDescriptions", "ActionIfDifferentForQueryText", "ActionIfDifferentForLabelText"):
        ET.SubElement(expected, tag).text = "Stop"
    imports = ET.SubElement(output, "Import")
    cdo = ET.SubElement(ET.SubElement(imports, "CDODefinitions"), "CDODefinition", {"Name": target.get("Name"), "Action": "Import"})
    if template in list(template_owner.findall("CDOFieldDefinition")):
        cdo.append(draft)
    else:
        ET.SubElement(cdo, "CDOFieldDefinitions").append(draft)
    validate(output)
    return output, {
        "target_cdo": target.get("Name"), "field_name": field_name,
        "template_cdo": template_cdo, "template_field": template_field,
        "attributes_xml": ET.tostring(attrs, encoding="unicode"),
        "unresolved_parent": missing_parent,
        "storage_mapping": "not_generated",
    }


def serialize(root: ET.Element) -> bytes:
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)

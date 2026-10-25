"""Invoke the installed, generated WCF client in a bounded private process."""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import threading
from urllib.parse import urlsplit, urlunsplit
import xml.etree.ElementTree as ET

from defusedxml.ElementTree import fromstring

import config
from designer.files import root_dir
from designer.vendor import digest

_compile_lock = threading.Lock()
READ_METHODS = frozenset({'Load', 'GetEnvironment'})


def endpoint(value):
    parsed = urlsplit(value.strip())
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or any(c in value for c in '\r\n\x00')):
        raise ValueError('服务地址应为不含账号、查询参数的 HTTP/HTTPS 基础目录')
    if parsed.path.lower().endswith('.svc'):
        raise ValueError('请填写 WCF 基础目录，不要包含单个 .svc 文件名')
    try:
        parsed.port
    except ValueError:
        raise ValueError('WCF 服务地址的端口无效') from None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip('/') + '/', '', ''))


def artifact(identity, *, verify=True):
    if not re.fullmatch(r'[0-9a-f]{32}', identity):
        raise ValueError('无效的 WCF 产物编号')
    folder = (root_dir() / 'artifacts' / identity).resolve()
    if not folder.is_relative_to(root_dir()):
        raise ValueError('WCF 产物不能位于项目根目录之外')
    receipt = folder / 'generation_result.json'
    if not receipt.is_file():
        raise ValueError('没有找到 WCF 生成回执')
    report = json.loads(receipt.read_text(encoding='utf-8-sig'))
    if report.get('status') != 'services_generated' or report.get('source_unchanged') is not True:
        raise ValueError('WCF 生成尚未通过检查')
    approved = {}
    for relative, expected in report.get('files_sha256', {}).items():
        relative = PurePosixPath(relative.replace('\\', '/'))
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('WCF 回执包含越界文件')
        if not relative.parts or relative.parts[0].lower() != 'client':
            continue
        path = (folder / str(relative)).resolve()
        if not path.is_relative_to(folder) or not path.is_file():
            raise ValueError('WCF 客户端文件缺失或路径越界')
        if verify and digest(path) != expected:
            raise ValueError('WCF 客户端产物已变化，请重新生成或选择正确版本')
        approved[path.name.lower()] = (path, expected)
    if not {'camstar.wcfclient.dll', 'camstar.wcfclientbase.dll', 'app.config'}.issubset(approved):
        raise ValueError('WCF 产物缺少客户端 DLL、基础组件或配置')
    # The resolver may load only files covered by the original generation receipt.
    if verify:
        for path in (folder / 'client').glob('*.dll'):
            if path.name.lower() not in approved:
                raise ValueError('客户端目录含未登记的 DLL，请核对生成产物')
    return {'id': identity, 'folder': folder, 'report': report, 'approved': approved,
            'client_sha256': approved['camstar.wcfclient.dll'][1],
            'compiled_sha256': report.get('compiled_sha256', '')}


def artifacts():
    rows = []
    for path in (root_dir() / 'artifacts').glob('*/generation_result.json'):
        try:
            item = artifact(path.parent.name, verify=False)
            rows.append({'id': item['id'], 'client_sha256': item['client_sha256'],
                         'compiled_sha256': item['compiled_sha256'], 'created': path.stat().st_mtime,
                         'service_count': item['report'].get('service_count'),
                         'partial_package': item['report'].get('partial_package', False),
                         'objects': [row['name'].split('.')[-1] for row in item['report'].get('type_checks', [])]})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(rows, key=lambda row: row['created'], reverse=True)[:50]


def redact(value, secrets=()):
    secret_keys = {'password', 'protected_password', 'sessionid', 'session_id', 'authorization', 'token', 'encryptedfield'}
    if isinstance(value, dict):
        named_secret = str(value.get('Name', value.get('name', ''))).lower() in secret_keys
        return {key: '[已隐藏]' if key.lower() in secret_keys or named_secret and key.lower() in {'value', 'expression'} else redact(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, '[已隐藏]')
        value = re.sub(r'(?is)(<(?:\w+:)?(?:Password|SessionID|Authorization)\b[^>]*>).*?(</[^>]+>)', r'\1[已隐藏]\2', value)
        value = re.sub(r'(?i)((?:password|pwd|sessionid|authorization)\s*[=:]\s*)[^\s;,<]+', r'\1[已隐藏]', value)
        return value
    return value


def _worker(item, environment):
    if os.name != 'nt':
        raise ValueError('真实 WCF 测试需要 Windows .NET Framework')
    source = Path(__file__).with_name('TestWcfClient.cs')
    address = endpoint(environment.get('endpoint') or 'http://localhost/')
    timeout = min(environment.get('timeout', config.DESIGNER_TEST_TIMEOUT), config.DESIGNER_TEST_TIMEOUT)
    key = sha256((digest(source) + item['client_sha256'] + item['approved']['app.config'][1] + address + str(timeout)).encode()).hexdigest()
    folder = (root_dir() / 'testing-runtime' / key).resolve()
    if not folder.is_relative_to(root_dir()):
        raise ValueError('测试运行目录越界')
    executable = folder / 'TestWcfClient.exe'
    with _compile_lock:
        if executable.is_file() and executable.with_suffix('.exe.config').is_file():
            return executable, timeout
        folder.mkdir(parents=True, exist_ok=True)
        original = fromstring(item['approved']['app.config'][0].read_bytes())
        model = original.find('system.serviceModel')
        if model is None or model.find('client') is None:
            raise ValueError('生成配置中没有官方 WCF 客户端终结点')
        model = deepcopy(model)
        for node in model.findall('client/endpoint'):
            filename = PurePosixPath(urlsplit(node.get('address', '')).path).name
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*\.svc', filename):
                raise ValueError('生成配置包含不支持的服务终结点')
            node.set('address', address + filename)
        duration = '00:%02d:%02d' % divmod(timeout, 60)
        for binding in model.findall('bindings/basicHttpBinding/binding'):
            for attribute in ('openTimeout', 'closeTimeout', 'sendTimeout', 'receiveTimeout'):
                binding.set(attribute, duration)
            security = binding.find('security')
            if security is None:
                security = ET.SubElement(binding, 'security')
            security.set('mode', 'Transport' if address.startswith('https:') else 'None')
        document = ET.Element('configuration')
        document.append(model)
        ET.ElementTree(document).write(executable.with_suffix('.exe.config'), encoding='utf-8', xml_declaration=True)
        framework = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'Microsoft.NET' / 'Framework64' / 'v4.0.30319'
        if not (framework / 'csc.exe').is_file():
            framework = framework.parent.parent / 'Framework' / 'v4.0.30319'
        completed = subprocess.run([str(framework / 'csc.exe'), '/nologo', '/target:exe', '/out:' + str(executable),
                                    '/reference:System.Web.Extensions.dll', str(source)], capture_output=True,
                                   timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if completed.returncode:
            raise ValueError('WCF 测试适配器编译失败：' + completed.stdout.decode(errors='replace')[:2000])
    return executable, timeout


def invoke(item, mode, environment=None, **arguments):
    environment = environment or {}
    executable, timeout = _worker(item, environment)
    env = os.environ.copy()
    env['CAMSTAR_TEST_CLIENT_DIRECTORY'] = str(item['folder'] / 'client')
    payload = {'mode': mode, 'business_user': environment.get('business_user', ''),
               'password': environment.get('password', ''), 'server_name': environment.get('server_name', ''), **arguments}
    try:
        completed = subprocess.run([str(executable)], input=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                                   capture_output=True, timeout=timeout + 5, env=env,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired:
        raise ValueError('WCF 请求超时，实际业务结果未知；写入操作请先核对业务数据再重试。') from None
    text = completed.stdout.decode('utf-8-sig', errors='replace').strip()
    try:
        response = json.loads(text.splitlines()[-1])
    except (ValueError, IndexError):
        raise ValueError('WCF 客户端未返回有效回执，实际业务结果未知。') from None
    if not response.get('ok'):
        raise ValueError(redact(response.get('error', 'WCF 客户端执行失败'), [payload['password']]))
    return redact(response['result'], [payload['password']])

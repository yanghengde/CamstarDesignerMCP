"""Read installed Designer entry points through an authenticated server share."""
import json
import os
from pathlib import Path, PureWindowsPath
import subprocess

import config
from designer.files import artifact_dir
from designer.publication import redact


def inspect_installation() -> dict:
    if not all((config.DESIGNER_DB_SERVER,config.DESIGNER_SERVER_SHARE,config.DESIGNER_WINDOWS_USER,
                config.DESIGNER_WINDOWS_PASSWORD,config.DESIGNER_UI_EXE,config.DESIGNER_SERVER_IMPORT_EXE)):
        raise ValueError("请配置Designer服务器共享、Windows凭据、Designer界面及原生导入程序的路径")
    if config.DESIGNER_SERVER_SHARE.casefold()!=('\\\\'+config.DESIGNER_DB_SERVER+'\\C').casefold():
        raise ValueError("共享必须是已配置Designer服务器的C共享")
    for value in (config.DESIGNER_UI_EXE,config.DESIGNER_SERVER_IMPORT_EXE):
        path=PureWindowsPath(value)
        if not path.is_absolute() or path.drive.casefold()!='c:' or '..' in path.parts or any(c in value for c in '\r\n\x00\"') or path.suffix.lower()!='.exe':
            raise ValueError("Designer程序必须是C盘上的明确exe绝对路径")
    env=os.environ.copy()
    for key in list(env):
        if key.casefold()=='psmodulepath':env.pop(key)
    env.update(DESIGNER_SERVER_SHARE=config.DESIGNER_SERVER_SHARE,DESIGNER_WINDOWS_USER=config.DESIGNER_WINDOWS_USER,
               DESIGNER_WINDOWS_PASSWORD=config.DESIGNER_WINDOWS_PASSWORD,DESIGNER_UI_EXE=config.DESIGNER_UI_EXE,
               DESIGNER_SERVER_IMPORT_EXE=config.DESIGNER_SERVER_IMPORT_EXE)
    folder=artifact_dir(); result=folder/'installation.json'
    shell=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    try:
        completed=subprocess.run([str(shell),'-NoProfile','-NonInteractive','-File',str(Path(__file__).with_name('inspect_installation.ps1')),
                                  '-ResultFile',str(result)],env=env,capture_output=True,timeout=60,
                                  creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if completed.returncode:
            raise ValueError(redact(completed.stderr.decode('utf-8',errors='replace'))[:2000])
        report=json.loads(result.read_text(encoding='utf-8-sig'))
        report['note']='存在程序不表示导入已验收；当前原生导入需单独运行及往返核对。'
    except (subprocess.TimeoutExpired,OSError,ValueError) as exc:
        raise ValueError('Designer安装检查失败：'+redact(str(exc))) from None
    return {**report,'result_file':str(result)}

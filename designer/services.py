"""Bounded official WCF generation on a configured server through SQL Agent."""
import json
import os
from pathlib import Path,PureWindowsPath
import shutil
import subprocess
import time

import config
from designer.files import artifact_dir,source_path
from designer.publication import target_connection,redact
from designer.vendor import digest
from designer.metadata import identifier


def worker_environment() -> dict:
    if not config.DESIGNER_TEST_TARGET_CONFIRMED:
        raise ValueError("服务器生成需要已确认测试目标")
    if not all((config.DESIGNER_SERVER_SHARE,config.DESIGNER_WINDOWS_USER,config.DESIGNER_WINDOWS_PASSWORD,config.DESIGNER_WCF_DIRECTORY,config.DESIGNER_WCF_ADDRESS)):
        raise ValueError("请配置服务器共享、Windows凭据、WCF目录和实际服务地址")
    if config.DESIGNER_SERVER_SHARE.casefold()!=('\\\\'+config.DESIGNER_DB_SERVER+'\\C').casefold():
        raise ValueError("共享必须是已确认数据库服务器的C共享")
    for value in (config.DESIGNER_WCF_DIRECTORY,config.DESIGNER_SERVER_SITEINFO,config.DESIGNER_SERVER_WCF_GENERATOR):
        path=PureWindowsPath(value)
        if not path.is_absolute() or path.drive.casefold()!='c:' or '..' in path.parts or any(c in value for c in '\r\n\x00\"'):
            raise ValueError("服务器组件路径必须是C盘上的明确绝对路径")
    env=os.environ.copy()
    # PowerShell 7's inherited module path can break Windows PowerShell 5.1.
    for key in list(env):
        if key.casefold()=="psmodulepath": env.pop(key)
    env.update(DESIGNER_SERVER_SHARE=config.DESIGNER_SERVER_SHARE,DESIGNER_WINDOWS_USER=config.DESIGNER_WINDOWS_USER,
               DESIGNER_WINDOWS_PASSWORD=config.DESIGNER_WINDOWS_PASSWORD,DESIGNER_WCF_DIRECTORY=config.DESIGNER_WCF_DIRECTORY,
               DESIGNER_SERVER_WCF_GENERATOR=config.DESIGNER_SERVER_WCF_GENERATOR)
    return env


def transfer(folder: Path, mode: str, env: dict) -> None:
    shell=Path(os.environ.get("SystemRoot","C:/Windows"))/"System32/WindowsPowerShell/v1.0/powershell.exe"
    run=subprocess.run([str(shell),"-NoProfile","-NonInteractive","-File",str(Path(__file__).with_name("service_transfer.ps1")),
                        "-LocalFolder",str(folder),"-Mode",mode],env=env,capture_output=True,timeout=120,
                        creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    if run.returncode:
        raise ValueError("服务器文件传输失败："+redact(run.stderr.decode("utf-8",errors="replace"))[:1500]) from None


def generate_wcf(compiled_mdb: str, expected_sha256: str, verify_types: list[str], service_names: list[str] | None = None) -> dict:
    source=source_path(compiled_mdb,".mdb")
    if digest(source)!=expected_sha256: raise ValueError("编译MDB的SHA256不匹配")
    if not isinstance(verify_types,list) or not 1<=len(verify_types)<=20: raise ValueError("必须提供1～20个验收对象名")
    for name in verify_types: identifier(name)
    if service_names is not None:
        if not isinstance(service_names,list) or not 1<=len(service_names)<=20 or len(set(service_names))!=len(service_names):
            raise ValueError("service_names必须是1～20个不重复的服务名；省略为全量")
        for name in service_names: identifier(name)
    env=worker_environment(); folder=artifact_dir(); server=PureWindowsPath("C:/Temp/DesignerMCP")/folder.name
    report={"status":"preparing","server":config.DESIGNER_DB_SERVER,"compiled_sha256":expected_sha256,"server_folder":str(server),"deployed":False,
            "service_scope":service_names if service_names is not None else "all","partial_package":service_names is not None}
    output=folder/"generation_result.json"; job="DesignerMCP_WCF_"+folder.name
    try:
        transfer(folder,"settings",env)
        settings=json.loads((folder/"server_settings.json").read_text(encoding="utf-8-sig"))
        shutil.copyfile(source,folder/"compiled.mdb")
        if digest(source)!=expected_sha256 or digest(folder/"compiled.mdb")!=expected_sha256: raise ValueError("复制期间来源发生变化")
        for name in ("WcfWorker.cs","WorkerProcess.cs","wcf_worker.ps1"): shutil.copyfile(Path(__file__).with_name(name),folder/name)
        request={"compiled_mdb":str(server/"compiled.mdb"),"siteinfo_mdb":config.DESIGNER_SERVER_SITEINFO,
                 "generator_directory":config.DESIGNER_SERVER_WCF_GENERATOR,"address":config.DESIGNER_WCF_ADDRESS,
                 "server_connection":settings["server_connection"],"verify_types":",".join(verify_types),"timeout_seconds":str(config.DESIGNER_SERVICE_TIMEOUT)}
        if service_names is not None: request["service_names"]=",".join(service_names)
        (folder/"request.json").write_text(json.dumps(request),encoding="utf-8")
        transfer(folder,"stage",env)
        with target_connection("msdb") as conn:
            cur=conn.cursor()
            cur.execute("EXEC dbo.sp_add_job @job_name=?,@description=?",job,"Temporary official Designer WCF generation")
            running=False
            try:
                command='powershell.exe -NoProfile -NonInteractive -Mta -File "'+str(server/"wcf_worker.ps1")+'" -RequestFile "'+str(server/"request.json")+'"'
                cur.execute("EXEC dbo.sp_add_jobstep @job_name=?,@step_name='GenerateWCF',@subsystem='CmdExec',@command=?,@output_file_name=?",job,command,str(server/"worker.log"))
                cur.execute("EXEC dbo.sp_add_jobserver @job_name=?",job); cur.execute("EXEC dbo.sp_start_job @job_name=?",job); running=True
                deadline=time.monotonic()+config.DESIGNER_SERVICE_TIMEOUT+60
                while time.monotonic()<deadline:
                    row=cur.execute("SELECT TOP 1 h.run_status FROM dbo.sysjobhistory h JOIN dbo.sysjobs j ON j.job_id=h.job_id WHERE j.name=? AND h.step_id=0 ORDER BY h.instance_id DESC",job).fetchone()
                    if row:
                        running=False; transfer(folder,"fetch",env)
                        if not (folder/"result.json").is_file(): raise ValueError("WCF工作进程未生成回执，请检查worker.log")
                        result=json.loads((folder/"result.json").read_text(encoding="utf-8-sig"))
                        if row[0]!=1 or not result.get("ok"): raise ValueError(result.get("error","WCF生成作业失败"))
                        report.update(result["result"])
                        break
                    time.sleep(3)
                else: raise ValueError("WCF生成超时")
            finally:
                if running:
                    cur.execute("EXEC dbo.sp_stop_job @job_name=?",job)
                    # SQL Agent cancellation is asynchronous; wait for completion
                    # before deleting the job so no untracked worker is left running.
                    for _ in range(20):
                        row=cur.execute("SELECT TOP 1 h.run_status FROM dbo.sysjobhistory h JOIN dbo.sysjobs j ON j.job_id=h.job_id WHERE j.name=? AND h.step_id=0 ORDER BY h.instance_id DESC",job).fetchone()
                        if row: break
                        time.sleep(1)
                cur.execute("EXEC dbo.sp_delete_job @job_name=?",job)
        local_files={str(p.relative_to(folder)):digest(p) for directory in (folder/"client",folder/"server") if directory.is_dir() for p in directory.rglob("*") if p.is_file()}
        if not local_files: raise ValueError("服务产物未复制回本地")
        report.update(files_sha256=local_files,source_unchanged=digest(source)==expected_sha256)
    except Exception as exc:
        report.update(status="generation_failed",error=redact(str(exc)))
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        raise ValueError(f"WCF生成未完成；记录：{output}；{report['error']}") from None
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return {**report,"result_file":str(output)}

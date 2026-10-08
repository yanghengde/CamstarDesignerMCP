using System;
using System.Diagnostics;
using System.Runtime.InteropServices;

// A child compiler must not survive cancellation of its SQL Agent launcher.
public static class DesignerWorkerProcess {
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern IntPtr CreateJobObject(IntPtr attrs,string name);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool SetInformationJobObject(IntPtr job,int type,ref ExtendedLimits info,uint size);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr job,IntPtr process);
 [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
 [StructLayout(LayoutKind.Sequential)] struct BasicLimits {
  public long processTime,jobTime; public uint flags; public UIntPtr minWorking,maxWorking;
  public uint activeProcesses; public UIntPtr affinity; public uint priority,scheduling;
 }
 [StructLayout(LayoutKind.Sequential)] struct IoCounters {public ulong a,b,c,d,e,f;}
 [StructLayout(LayoutKind.Sequential)] struct ExtendedLimits {
  public BasicLimits basic; public IoCounters io; public UIntPtr processMemory,jobMemory,peakProcess,peakJob;
 }
 public static int Run(string executable,string request,string result,int seconds) {
  return RunArguments(executable,"\""+request+"\" \""+result+"\"",seconds);
 }
 public static int RunArguments(string executable,string arguments,int seconds) {
  return RunStartInfo(new ProcessStartInfo(executable,arguments),seconds);
 }
 static int RunStartInfo(ProcessStartInfo start,int seconds) {
  IntPtr job=CreateJobObject(IntPtr.Zero,null);
  if(job==IntPtr.Zero) throw new System.ComponentModel.Win32Exception();
  try {
   var limits=new ExtendedLimits(); limits.basic.flags=0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
   if(!SetInformationJobObject(job,9,ref limits,(uint)Marshal.SizeOf(typeof(ExtendedLimits)))) throw new System.ComponentModel.Win32Exception();
   start.UseShellExecute=false; start.CreateNoWindow=true; start.RedirectStandardOutput=true; start.RedirectStandardError=true;
   using(var process=Process.Start(start)) {
    if(!AssignProcessToJobObject(job,process.Handle)) {process.Kill(); throw new System.ComponentModel.Win32Exception();}
    process.OutputDataReceived+=(s,e)=>{if(e.Data!=null)Console.WriteLine(e.Data);};
    process.ErrorDataReceived+=(s,e)=>{if(e.Data!=null)Console.Error.WriteLine(e.Data);};
    process.BeginOutputReadLine(); process.BeginErrorReadLine();
    if(!process.WaitForExit(seconds*1000)) throw new TimeoutException("Designer worker exceeded time limit");
    process.WaitForExit(); return process.ExitCode;
   }
  } finally {CloseHandle(job);}
 }
}

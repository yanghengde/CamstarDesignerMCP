using System;
using System.IO;
using System.Collections.Generic;
using System.Web.Script.Serialization;
using System.Reflection;
using System.Linq;
using Camstar.WCF.Generator;
using Camstar.WCF.Generator.Helper;

public static class DesignerWcfWorker {
 static bool ExposeService(IService node,string name) {
  if(node.Name==name) {
   node.ExposedState=ExposedServiceStateType.Exposed;
   foreach(var method in node.Methods) method.ExposedState=ExposedMethodStateType.Exposed;
   return true;
  }
  foreach(var child in node.Children) {
   if(ExposeService(child,name)) {node.ExposedState=ExposedServiceStateType.Base;return true;}
  }
  return false;
 }
 public static int Main(string[] args) {
  if(args.Length!=2) return 2;
  Run(args[0],args[1]);
  return File.ReadAllText(args[1]).Contains("\"ok\":true")?0:1;
 }
 public static void Run(string requestFile,string resultFile) {
  var json=new JavaScriptSerializer();
  try {
   var request=json.Deserialize<Dictionary<string,string>>(File.ReadAllText(requestFile));
   string folder=Path.GetDirectoryName(resultFile);
   var settings=new GeneratorSettings();
   settings.DatabaseConnectionString="Provider="+(request.ContainsKey("mdb_provider")?request["mdb_provider"]:"Microsoft.ACE.OLEDB.12.0")+";Data Source="+request["compiled_mdb"];
   settings.IsGetFromRegistry=false; settings.IsGenerateAll=!request.ContainsKey("service_names"); settings.IsGenerateSilverlight=false;
   settings.ServerOutputDirectory=Path.Combine(folder,"server"); settings.ClientOutputDirectory=Path.Combine(folder,"client");
   settings.ClientSilverlightOutputDirectory=Path.Combine(folder,"silverlight");
   settings.ClientOutputConfigPath=Path.Combine(folder,"client","App.config");
   settings.PortalStudioClientOutputConfigPath=Path.Combine(folder,"client","portalstudioweb.config");
   settings.TargetFramework="4.8"; settings.Address=request["address"];
   settings.DefaultServerConnectionString="Server"; settings.LogPath=Path.Combine(folder,"logs"); settings.LogNaming="time";
   var connection=new ConnectionStringElement(); connection.Name="Server"; connection.Value=request["server_connection"];
   settings.ConnectionStrings.Add(connection);
   Console.WriteLine("Loading compiled definitions");
   var runner=new Runner(settings);
   // The installed generator exposes no logger injection point. Keep root
   // diagnostics in this workspace, while private executable config prevents
   // internal loggers from flooding Windows Event Log when registry is absent.
   var logger=(Logger)typeof(Runner).GetField("_log",BindingFlags.NonPublic|BindingFlags.Instance).GetValue(runner);
   logger.FilePath=Path.Combine(folder,"generator.log"); logger.LogLevel=3;
   runner.Init();
   if(request.ContainsKey("service_names")) {
    var root=(IService)typeof(Runner).GetField("_RootService",BindingFlags.NonPublic|BindingFlags.Instance).GetValue(runner);
    foreach(string name in request["service_names"].Split(',')) {
     if(!ExposeService(root,name)) throw new InvalidOperationException("Unknown WCF service: "+name);
    }
   }
   // This installed version invokes StateChanged with an incompatible argument
   // count. Root diagnostics provide phase/count logging without subscriptions.
   Console.WriteLine("Generating WCF assemblies"); var info=runner.Generate();
   string client=Path.Combine(folder,"client","Camstar.WCFClient.dll"),service=Path.Combine(folder,"server","bin","Camstar.WCFService.dll");
   if(!info.IsSuccess || info.DCCount<1 || info.SCCount<1 || !File.Exists(client) || !File.Exists(service))
    throw new InvalidOperationException("Incomplete WCF output: success="+info.IsSuccess+", contracts="+info.DCCount+", services="+info.SCCount+", client="+File.Exists(client)+", service="+File.Exists(service),info.Exception);
   var assembly=Assembly.LoadFrom(client);
   var checks=new List<object>();
   foreach(string name in request["verify_types"].Split(new[]{','},StringSplitOptions.RemoveEmptyEntries)) {
    // Revisioned/named object contracts use the vendor's <CDO>Changes suffix.
    var type=assembly.GetType("Camstar.WCF.ObjectStack."+name,false) ?? assembly.GetType("Camstar.WCF.ObjectStack."+name+"Changes",false);
    if(type==null) throw new InvalidOperationException("Generated client missing type: "+name);
    checks.Add(new {name=type.FullName,properties=type.GetProperties().Select(p=>p.Name).ToArray()});
   }
   File.WriteAllText(resultFile,json.Serialize(new {ok=true,result=new {status="services_generated",data_contract_count=info.DCCount,
    service_count=info.SCCount,client_assembly=client,service_assembly=service,type_checks=checks,deployed=false}}));
  } catch(Exception error) {File.WriteAllText(resultFile,json.Serialize(new {ok=false,error=error.ToString()}));}
 }
}

using System;
using System.Collections;
using System.IO;
using System.Collections.Generic;
using System.Web.Script.Serialization;
using System.Reflection;
using System.Linq;
using Camstar.WCF.Generator;
using Camstar.WCF.Generator.Helper;

public static class DesignerWcfWorker {
 static string Shape(Type type) {
  if(type.IsArray) return Shape(type.GetElementType())+"[]";
  string name=type.IsGenericType ? type.Name.Split('`')[0] : type.FullName;
  name=name.Replace("Camstar.WCF.ObjectStack.","").Replace("Camstar.WCF.Service.","");
  return type.IsGenericType ? name+"<"+String.Join(",",type.GetGenericArguments().Select(Shape).ToArray())+">" : name;
 }
 static string ValueType(Type type) {
  if(type.IsArray) return ValueType(type.GetElementType());
  if(type.IsGenericType && type.GetGenericArguments().Length==1) return ValueType(type.GetGenericArguments()[0]);
  return Shape(type);
 }
 static Dictionary<string,object> Schema(Type type) {
  return new Dictionary<string,object>{{"type",Shape(type)},{"value_type",ValueType(type)},
   {"is_list",type.IsArray || Shape(type).IndexOf("List",StringComparison.OrdinalIgnoreCase)>=0}};
 }
 static void VerifySchema(Dictionary<string,object> expected,Dictionary<string,object> actual,string owner,string name) {
  if(expected.ContainsKey("is_list") && (bool)expected["is_list"]!=(bool)actual["is_list"])
   throw new InvalidOperationException("Generated list shape mismatch: "+owner+"."+name);
  var primitives=new Dictionary<string,string>(StringComparer.OrdinalIgnoreCase) {
   {"String","System.String"},{"Integer","System.Int32"},{"Boolean","System.Boolean"},
   {"Float","System.Double"},{"Fixed","System.Decimal"},{"Timestamp","System.DateTime"}};
  string clr;
  if(expected.ContainsKey("data_type") && primitives.TryGetValue(Convert.ToString(expected["data_type"]),out clr) && (string)actual["value_type"]!=clr)
   throw new InvalidOperationException("Generated CLR type mismatch: "+owner+"."+name+" expected "+clr+" got "+actual["value_type"]);
 }
 static object Property(object item,string name) {
  return item.GetType().GetProperty(name,BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Instance).GetValue(item,null);
 }
 static string ContractName(string name,Dictionary<string,object> types) {
  if(types.ContainsKey(name)) return name;
  if(types.ContainsKey(name+"Changes")) return name+"Changes";
  throw new InvalidOperationException("WCF contract preflight: no reachable contract for "+name);
 }
 static HashSet<string> ContractFields(object type,Dictionary<string,object> types) {
  var fields=new HashSet<string>(); var seen=new HashSet<string>();
  while(type!=null && seen.Add((string)Property(type,"Name"))) {
   var getFields=type.GetType().GetMethod("GetFields",BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Instance);
   foreach(var field in (IEnumerable)getFields.Invoke(type,null)) fields.Add((string)Property(field,"Name"));
   object parent; string parentName=(string)Property(type,"ParentName");
   type=!String.IsNullOrEmpty(parentName) && types.TryGetValue(parentName,out parent)?parent:null;
  }
  return fields;
 }
 static object PreflightContracts(Runner runner,Dictionary<string,string> request,JavaScriptSerializer json) {
  var collect=typeof(Runner).GetMethod("CollectInformation",BindingFlags.NonPublic|BindingFlags.Instance);
  var cacheField=typeof(Runner).GetField("_TypeCache",BindingFlags.NonPublic|BindingFlags.Instance);
  if(collect==null || cacheField==null) throw new InvalidOperationException("Unsupported WCF generator contract preflight API");
  collect.Invoke(runner,null);
  var cache=cacheField.GetValue(runner);
  var sorted=cache.GetType().GetMethod("GetSortedDCTypes",BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Instance);
  var types=((IEnumerable)sorted.Invoke(cache,null)).Cast<object>().ToDictionary(t=>(string)Property(t,"Name"));
  var checks=new List<object>();
  foreach(var name in request["verify_types"].Split(new[]{','},StringSplitOptions.RemoveEmptyEntries)) ContractName(name,types);
  if(request.ContainsKey("expected_fields")) foreach(var raw in (object[])json.DeserializeObject(request["expected_fields"])) {
   var field=(Dictionary<string,object>)raw;
   string owner=(string)field["owner"],name=(string)field["name"],contract=ContractName(owner,types);
   bool present=ContractFields(types[contract],types).Contains(name),expected=(bool)field["present"];
   if(present!=expected) throw new InvalidOperationException("WCF contract preflight failed: "+owner+"."+name+
    " expected "+(expected?"present":"absent")+" in "+contract+"; check maintenance ObjectChanges fields before generation");
   checks.Add(new {owner=owner,name=name,contract=contract,present=present});
  }
  return new {status="contract_preflight_passed",data_contract_count=types.Count,fields=checks};
 }
 static void CountExposure(IService node,ref int services,ref int methods) {
  if(node.ExposedState==ExposedServiceStateType.Exposed || node.ExposedState==ExposedServiceStateType.BaseAndExposed) {
   services++;
   foreach(var method in node.Methods) {
    if(method.ExposedState==ExposedMethodStateType.Exposed || method.ExposedState==ExposedMethodStateType.BaseAndExposed) methods++;
   }
  }
  foreach(var child in node.Children) CountExposure(child,ref services,ref methods);
 }
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
  var json=new JavaScriptSerializer { MaxJsonLength=67108864, RecursionLimit=64 };
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
    var root=runner.GetRootService();
    foreach(string name in request["service_names"].Split(',')) {
     if(!ExposeService(root,name)) throw new InvalidOperationException("Unknown WCF service: "+name);
    }
   }
   // This installed version invokes StateChanged with an incompatible argument
   // count. Root diagnostics provide phase/count logging without subscriptions.
   int exposedServices=0,exposedMethods=0;
   CountExposure(runner.GetRootService(),ref exposedServices,ref exposedMethods);
   Console.WriteLine("Exposure scope: services="+exposedServices+", method entries="+exposedMethods);
   if(request.ContainsKey("expected_fields") || request.ContainsKey("preflight_only")) {
    Console.WriteLine("Checking reachable WCF contracts before generation");
    var preflight=PreflightContracts(runner,request,json);
    File.WriteAllText(Path.Combine(folder,"contract_preflight.json"),json.Serialize(preflight));
    if(request.ContainsKey("preflight_only")) {
     File.WriteAllText(resultFile,json.Serialize(new {ok=true,result=preflight}));return;
    }
   }
   Console.WriteLine("Generating WCF assemblies"); var info=runner.Generate();
   string client=Path.Combine(folder,"client","Camstar.WCFClient.dll"),service=Path.Combine(folder,"server","bin","Camstar.WCFService.dll");
   if(!info.IsSuccess || info.DCCount<1 || info.SCCount<1 || !File.Exists(client) || !File.Exists(service))
    throw new InvalidOperationException("Incomplete WCF output: success="+info.IsSuccess+", contracts="+info.DCCount+", services="+info.SCCount+", client="+File.Exists(client)+", service="+File.Exists(service),info.Exception);
   var assembly=Assembly.LoadFrom(client);
   var serverAssembly=Assembly.LoadFrom(service);
   var checks=new List<object>();
   var serverChecks=new List<object>();
   var clientFields=new Dictionary<string,HashSet<string>>();
   var serverFields=new Dictionary<string,HashSet<string>>();
   var clientSchemas=new Dictionary<string,Dictionary<string,Dictionary<string,object>>>();
   var serverSchemas=new Dictionary<string,Dictionary<string,Dictionary<string,object>>>();
   foreach(string name in request["verify_types"].Split(new[]{','},StringSplitOptions.RemoveEmptyEntries)) {
    // Revisioned/named object contracts use the vendor's <CDO>Changes suffix.
    var type=assembly.GetType("Camstar.WCF.ObjectStack."+name,false) ?? assembly.GetType("Camstar.WCF.ObjectStack."+name+"Changes",false);
    if(type==null) throw new InvalidOperationException("Generated client missing type: "+name);
    var properties=type.GetProperties().Select(p=>p.Name).ToArray();
    var required=new HashSet<string>();
    if(request.ContainsKey("expected_fields")) foreach(var raw in (object[])json.DeserializeObject(request["expected_fields"])) {
     var expected=(Dictionary<string,object>)raw; string owner=(string)expected["owner"];
     if(owner==name || owner==type.Name || owner+"Changes"==type.Name) required.Add((string)expected["name"]);
    }
    var clientSchema=type.GetProperties().Where(p=>required.Contains(p.Name)).GroupBy(p=>p.Name).ToDictionary(g=>g.Key,g=>Schema(g.First().PropertyType));
    checks.Add(new {name=type.FullName,properties=properties,field_schemas=clientSchema});clientFields[name]=new HashSet<string>(properties);clientSchemas[name]=clientSchema;
    var serverType=serverAssembly.GetType("Camstar.WCF.Service."+type.Name,false);
    if(serverType==null) throw new InvalidOperationException("Generated server missing type: "+type.Name);
    var members=serverType.GetFields().Select(f=>f.Name).Concat(serverType.GetProperties().Select(p=>p.Name)).Distinct().ToArray();
    var serverSchema=serverType.GetFields().Select(f=>new {name=f.Name,type=f.FieldType}).Concat(serverType.GetProperties().Select(p=>new {name=p.Name,type=p.PropertyType}))
     .Where(f=>required.Contains(f.name)).GroupBy(f=>f.name).ToDictionary(g=>g.Key,g=>Schema(g.First().type));
    serverChecks.Add(new {name=serverType.FullName,properties=members,field_schemas=serverSchema});serverFields[name]=new HashSet<string>(members);serverSchemas[name]=serverSchema;
   }
   if(request.ContainsKey("expected_fields")) foreach(var raw in (object[])json.DeserializeObject(request["expected_fields"])) {
    var field=(Dictionary<string,object>)raw;string owner=(string)field["owner"],name=(string)field["name"];bool expected=(bool)field["present"];
    if(clientFields[owner].Contains(name)!=expected || serverFields[owner].Contains(name)!=expected)
     throw new InvalidOperationException("Generated client/server field mismatch: "+owner+"."+name);
    if(expected) {
     if(json.Serialize(clientSchemas[owner][name])!=json.Serialize(serverSchemas[owner][name]))
      throw new InvalidOperationException("Generated client/server schema mismatch: "+owner+"."+name);
     VerifySchema(field,clientSchemas[owner][name],owner,name);
    }
   }
   File.WriteAllText(resultFile,json.Serialize(new {ok=true,result=new {status="services_generated",data_contract_count=info.DCCount,
    service_count=info.SCCount,exposed_method_entries=exposedMethods,client_assembly=client,service_assembly=service,type_checks=checks,
    server_type_checks=serverChecks,deployed=false}}));
  } catch(Exception error) {File.WriteAllText(resultFile,json.Serialize(new {ok=false,error=error.ToString()}));}
 }
}

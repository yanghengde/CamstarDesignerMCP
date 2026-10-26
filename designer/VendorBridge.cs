// Uses the customer's installed Camstar assembly; no vendor binaries are distributed.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.IO;
using System.Web.Script.Serialization;
using Camstar.Metadata;
using Camstar.Metadata.Objects;

public static class DesignerVendorBridge {
 public static void Serve() {
  string line,identity=null;
  while((line=Console.ReadLine())!=null) {
   string completion=null;
   try {
    var command=Map(Json.DeserializeObject(line));
    string input=Text(command,"input"),output=Text(command,"output"),key=Text(command,"identity");
    completion=output+".done";
    var request=Map(Json.DeserializeObject(File.ReadAllText(input)));
    string mode=Text(request,"mode");
    if(!ReadModes.Contains(mode) && mode!="read_batch") throw new ArgumentException("Worker accepts read-only queries only");
    if(mode=="read_batch") foreach(var item in (object[])request["requests"])
     if(!ReadModes.Contains(Text(Map(item),"mode"))) throw new ArgumentException("Worker accepts read-only queries only");
    if(identity!=key || BatchSet==null) {
     BatchSet=null; identity=null;
     BatchSet=Load(Text(request,"mdb"),Path.GetDirectoryName(output),""); identity=key;
    }
    Run(input,output);
    File.WriteAllText(completion+".tmp","ok"); File.Move(completion+".tmp",completion);
    Console.WriteLine("__DESIGNER_READ_DONE__"); Console.Out.Flush();
   } catch(Exception e) {
    if(completion!=null && !File.Exists(completion)) {File.WriteAllText(completion+".tmp",e.Message);File.Move(completion+".tmp",completion);}
    Console.WriteLine("__DESIGNER_READ_ERROR__"+e.Message);Console.Out.Flush();
   }
  }
  BatchSet=null;
 }
 static MDBMetadataSet BatchSet;
 static readonly List<CDOField> AssociatedFields=new List<CDOField>();
 static readonly List<object> AssociatedOperations=new List<object>();
 static CDODefinition MaintenanceChanges(MDBMetadataSet set,CDODefinition cdo) {
  if(!(cdo.IsNamed || cdo.IsRevisioned) || cdo.MaintenanceCDOID==0) return null;
  var maint=set.CDODefinitions[cdo.MaintenanceCDOID];
  var target=maint.GetFieldByName("ObjectToChange",true); var link=maint.GetFieldByName("ObjectChanges",true);
  return target!=null && link!=null && target.FieldDefinition.CDODefId==cdo.CDODefId
   ? set.CDODefinitions[link.FieldDefinition.CDODefId] : null;
 }
 static void PairMutation(MDBMetadataSet set,CDOField source,Dictionary<string,object> op) {
  var owner=(CDODefinition)Find(set,"cdo",Text(op,"owner"),"");
  var changes=MaintenanceChanges(set,owner);
  if(changes==null) return;
  var field=changes.AllFields.Values.FirstOrDefault(f=>f.ModifiedStatus!=ModifiedStatusEnum.Deleted && f.FieldName.Equals(source.FieldName,StringComparison.OrdinalIgnoreCase));
  if(field==null) {
   if(source.IsNonPersistent || (op.ContainsKey("portal_editable") && !Flag(op,"portal_editable"))) return;
   throw new ArgumentException("Missing maintenance field; repair or explicitly use portal_editable=false: "+changes.Name+"."+source.FieldName);
  }
  if(source.CDODefinition.CDODefId!=owner.CDODefId) throw new ArgumentException("Paired changes to inherited data fields require an explicit Designer migration");
  if(field.FieldDefId!=source.FieldDefId || field.IsListType!=source.IsListType)
   throw new ArgumentException("Maintenance field type/list conflict: "+changes.Name+"."+source.FieldName);
  if(field.CDODefinition.CDODefId!=changes.CDODefId || field.WorkspaceCode!=set.WorkSpace || field.InheritedID!=0)
   throw new ArgumentException("Paired mutation requires a customer-owned maintenance field without inheritance: "+changes.Name+"."+source.FieldName);
  var paired=new Dictionary<string,object>(op); paired["owner"]=changes.Name; paired.Remove("portal_editable");
  string action=Text(op,"action");
  if(action=="patch") {
   var values=Map(op["changes"]); var copy=new Dictionary<string,object>(); var expected=new Dictionary<string,object>();
   foreach(var item in values) {
    if(new[]{"DBColumnID","FieldDefId","CDODefId","InheritedID","IsNonPersistent"}.Contains(item.Key,StringComparer.OrdinalIgnoreCase))
     throw new ArgumentException("Paired structural property requires an explicit migration: "+item.Key);
    if(new[]{"Name","FieldName","FieldDescription","IsListType","AllowClientToRead","AllowClientToUpdate"}.Contains(item.Key)) {
     copy[item.Key]=item.Value; expected[item.Key]=Scalar(field.GetType().GetProperty(item.Key).GetValue(field,null));
    }
   }
   if(copy.Count==0) return;
   paired["changes"]=copy; paired["expected"]=expected;
  }
  if(action=="remove_field_override") throw new ArgumentException("Restore the paired field overrides explicitly in Designer before continuing");
  var target=(CDOField)Apply(set,paired); AssociatedFields.Add(target); AssociatedOperations.Add(paired);
 }
 static readonly HashSet<string> ReadModes=new HashSet<string>(new[]{"catalog","list","get","schema","impact","fields","inheritance"});
 static JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength=20971520, RecursionLimit=64 };
 static Dictionary<string,object> Map(object value) { return (Dictionary<string,object>)value; }
 static string Text(Dictionary<string,object> d, string k, string fallback="") { return d.ContainsKey(k) ? Convert.ToString(d[k]) : fallback; }
 static int Number(Dictionary<string,object> d,string k,int fallback=0) { return d.ContainsKey(k) ? Convert.ToInt32(d[k]) : fallback; }
 static bool Flag(Dictionary<string,object> d,string k,bool fallback=false) { return d.ContainsKey(k) ? Convert.ToBoolean(d[k]) : fallback; }
 static Dictionary<string,object> Dict(params object[] pairs) {
  var d=new Dictionary<string,object>(); for(int i=0;i<pairs.Length;i+=2) d.Add((string)pairs[i],pairs[i+1]); return d;
 }
 static string Name(object obj) {
  foreach(var key in new[]{"Name","FieldName","FieldDefName","WorkspaceCode","FunctionName","DataTypeDescription"}) {
   var p=obj.GetType().GetProperty(key); if(p!=null) return Convert.ToString(p.GetValue(obj,null));
  } return obj.ToString();
 }
 static List<object> Values(object collection) {
  var d=collection as IDictionary;
  return d!=null ? d.Values.Cast<object>().ToList() : ((IEnumerable)collection).Cast<object>().ToList();
 }
 static readonly Dictionary<string,string> Collections=new Dictionary<string,string> {
  {"cdo","CDODefinitions"},{"field_type","FieldDefinitions"},{"clf","CLFDefinitions"},
  {"function","FunctionDefinitions"},{"event","CLFEventDefinitions"},{"table","DBTableDefinitions"},
  {"column","DBColumns"},{"index","DBIndexes"},{"query","QueryDefinitions"},{"map","CDOMapDefinitions"},
  {"label","AllLabels"},{"label_category","LabelCategories"},{"workspace","Workspaces"},
  {"data_type","CPPDataTypes"},{"query_type","QueryTypes"},{"clf_type","CLFTypes"},
  {"sql_type","SQLDataTypes"},{"db_type","DBTypes"},{"storage_category","DBCategories"},{"cdo_usage","CDOUsages"}
  ,{"event_binding","CLFEventMaps"},{"feature","InSiteFeatures"}
 };
 static List<object> Objects(MDBMetadataSet set,string kind,string owner) {
  if(kind=="field") return Values(((CDODefinition)Find(set,"cdo",owner,"")).AllFields);
  if(kind=="field_map") return Values(((CDOMapDefinition)Find(set,"map",owner,"")).FieldMaps);
  if(kind=="event_binding" && owner!="") return Values(((CDODefinition)Find(set,"cdo",owner,"")).Events);
  if(kind=="clf_function") return Values(((CLFDefinition)Find(set,"clf",owner,"")).CLFFunctions);
  if(kind=="clf_parameter") return Values(((CLFDefinition)Find(set,"clf",owner,"")).Parameters);
  if(kind=="query_text") return Values(((QueryDef)Find(set,"query",owner,"")).QueryTexts);
  if(kind=="query_parameter") return Values(((QueryDef)Find(set,"query",owner,"")).Parameters);
  if(kind=="index_entry") return Values(((DBIndexDefinition)Find(set,"index",owner,"")).IndexEntries);
  if(kind=="function_parameter") return Values(((FunctionDefinition)Find(set,"function",owner,"")).Parameters);
  if(kind=="column" && owner!="") return Values(((DBTableDefinition)Find(set,"table",owner,"")).Columns);
  if(kind=="index" && owner!="") return Values(((DBTableDefinition)Find(set,"table",owner,"")).Indexes);
  if(kind=="workspace") return set.GetWorkspaces().Cast<object>().ToList();
  if(!Collections.ContainsKey(kind)) throw new ArgumentException("Unknown Designer kind: "+kind);
  return Values(typeof(MetadataSetBase).GetProperty(Collections[kind],BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Instance).GetValue(set,null));
 }
 static object Find(MDBMetadataSet set,string kind,string name,string owner) {
  var candidates=Objects(set,kind,owner).Where(x=>!(x is BaseMetadataObject) || ((BaseMetadataObject)x).ModifiedStatus!=ModifiedStatusEnum.Deleted).ToList(); int identity;
  var found=(kind=="clf_function" || kind=="field_map") && name.StartsWith("id:") && Int32.TryParse(name.Substring(3),out identity)
   ? candidates.Where(x=>(kind=="clf_function" ? ((CLFFunction)x).CLFFunctionID : ((CDOFieldMapDefinition)x).CDOFieldMapID)==Int32.Parse(name.Substring(3))).ToList()
   : candidates.Where(x=>String.Equals(Name(x),name,StringComparison.OrdinalIgnoreCase)).ToList();
   if(found.Count==0) {
    object renamed;
    if(Aliases.TryGetValue(kind+":"+owner+":"+name,out renamed) && ((BaseMetadataObject)renamed).ModifiedStatus!=ModifiedStatusEnum.Deleted)
     return renamed;
   }
   if(found.Count!=1) throw new ArgumentException("Definition missing or ambiguous (matches="+found.Count+"): "+kind+":"+owner+":"+name);
  return found[0];
 }
 static Dictionary<string,object> Aliases=new Dictionary<string,object>(StringComparer.OrdinalIgnoreCase);
 static Dictionary<string,object> RemovedIdentities=new Dictionary<string,object>();
 static void RememberRemoval(BaseMetadataObject obj) {RemovedIdentities[Identity(obj)]=Record(obj);}
 static void Changed(BaseMetadataObject obj) {if(obj.ModifiedStatus!=ModifiedStatusEnum.New) obj.SetModifiedStatus(ModifiedStatusEnum.Modified);}
 static readonly Dictionary<string,string> IdentityProperties=new Dictionary<string,string> {
  {"CDODefinition","CDODefId"},{"CDOField","FieldID"},{"FieldDefinition","FieldDefID"},
  {"CLFDefinition","CLFID"},{"CLFFunction","CLFFunctionID"},{"CLFFunParm","CLFFunctionParmValueID"},
  {"FunctionDefinition","FunctionID"},{"FunctionParameter","FunctionParamID"},
  {"QueryDef","QueryDefID"},{"QueryText","QueryTextID"},{"QueryParm","QueryParmID"},
  {"DBTableDefinition","DBTableId"},{"DBColumn","DBColumnID"},{"DBIndexDefinition","DBIndexID"},
  {"DBIndexEntry","DBIndexEntryID"},{"CDOMapDefinition","CDOMapID"},
  {"CDOFieldMapDefinition","CDOFieldMapID"},{"CLFEventMap","CLFEventMapID"},{"Label","LabelID"}
 };
 static string Identity(BaseMetadataObject obj) {
  string property;
  if(!IdentityProperties.TryGetValue(obj.GetType().Name,out property)) throw new ArgumentException("No stable identity for "+obj.GetType().Name);
  var p=obj.GetType().GetProperties().First(x=>x.Name.Equals(property,StringComparison.OrdinalIgnoreCase));
  var workspace=obj as WorkspaceControlledObject;
  return obj.GetType().Name+":"+p.GetValue(obj,null)+":"+(workspace==null ? "" : workspace.WorkspaceCode);
 }
 static List<BaseMetadataObject> AllObjects(MDBMetadataSet set,HashSet<string> types) {
  var objects=new List<object>();
  foreach(var kind in Collections.Keys.Where(x=>!ReadOnlyKinds.Contains(x))) {
   var values=Objects(set,kind,""); if(values.Count>0 && types.Contains(values[0].GetType().Name)) objects.AddRange(values);
  }
  if(types.Contains("CDOField")) foreach(var cdo in set.CDODefinitions.Values) objects.AddRange(Values(cdo.AllFields));
  if(types.Overlaps(new[]{"FunctionParameter","CLFFunction","CLFFunParm"})) foreach(var clf in set.CLFDefinitions.Values) {
   if(types.Contains("FunctionParameter")) objects.AddRange(Values(clf.Parameters));
   if(types.Contains("CLFFunction")) objects.AddRange(Values(clf.CLFFunctions));
   if(types.Contains("CLFFunParm")) foreach(var call in clf.CLFFunctions) objects.AddRange(Values(call.Parameters));
  }
  if(types.Overlaps(new[]{"QueryText","QueryParm"})) foreach(var query in set.QueryDefinitions.Values) {
   if(types.Contains("QueryText")) objects.AddRange(Values(query.QueryTexts));
   if(types.Contains("QueryParm")) objects.AddRange(Values(query.Parameters));
  }
  if(types.Contains("CDOFieldMapDefinition")) foreach(var map in set.CDOMapDefinitions.Values) objects.AddRange(Values(map.FieldMaps));
  if(types.Contains("DBIndexEntry")) foreach(var index in set.DBIndexes.Values) objects.AddRange(Values(index.IndexEntries));
  return objects.OfType<BaseMetadataObject>().Where(x=>IdentityProperties.ContainsKey(x.GetType().Name)).Distinct().ToList();
 }
 static string[] CoreProperties(BaseMetadataObject obj) {
  if(obj is CDODefinition) return new[]{"Name","ParentCDOID","StorageCategoryId"};
  if(obj is CDOField) return new[]{"Name","FieldName","FieldDescription","FieldDefId","IsListType","IsNonPersistent","AllowClientToRead","AllowClientToUpdate"};
  if(obj is FieldDefinition) return new[]{"FieldDefName","PrecisionValue","Scale","CPPDataTypeID"};
  if(obj is CLFFunction) return new[]{"FunctionID","Sequence","CLFID"};
  if(obj is CLFFunParm) return new[]{"ValueExpression"};
  if(obj is FunctionParameter) return DeclarationFields.Concat(new[]{"FunctionID","ParmNumber","DataTypeId","CDODefId","ParamMultiplicityId","TypeMultiplicityId","FunctionParmUsageID"}).ToArray();
  if(obj is CDOFieldMapDefinition) return new[]{"SourceCDOFieldID","TargetCDOFieldID"};
  if(obj is CLFEventMap) return new[]{"CLFID","CLFEventID","CallerID","CDODefID"};
  return new[]{"Name"};
 }
 static object Scalar(object v) {
  if(v==null) return null;
  var t=v.GetType();
  if(t.IsEnum) return v.ToString();
  if(t.IsPrimitive || v is string || v is decimal || v is DateTime) return v;
  if(v is BaseMetadataObject) return Dict("type",t.Name,"name",Name(v));
  if(v is IEnumerable) return Dict("count",((IEnumerable)v).Cast<object>().Count());
  return v.ToString();
 }
 static Dictionary<string,object> Record(object o) {
  var d=Dict("object_type",o.GetType().Name,"name",Name(o));
  foreach(var p in o.GetType().GetProperties()) {
   if(p.GetIndexParameters().Length!=0 || !p.CanRead || p.Name=="Metadata") continue;
   try { d[p.Name]=Scalar(p.GetValue(o,null)); } catch { /* Report only readable properties. */ }
  } return d;
 }
 static Dictionary<string,object> ClfRecord(CLFDefinition clf) {
  var record=Record(clf);
  record["functions"]=clf.CLFFunctions.OrderBy(x=>x.Sequence).ThenBy(x=>x.CLFFunctionID).Select(x=>{
   var row=Record(x);row["parameters"]=Values(x.Parameters).Select(Record).ToArray();return row;
  }).ToArray();
  record["declared_parameters"]=Values(clf.Parameters).Cast<FunctionParameter>().Where(p=>p.ModifiedStatus!=ModifiedStatusEnum.Deleted).OrderBy(p=>p.ParmNumber).Select(DeclarationRecord).ToArray();
  return record;
 }
 static readonly string[] DeclarationFields={"Name","Direction","DataType","ParamMultiplicity","TypeMultiplicity","FunctionParmUsage","CDOType","Description","Notes","DefaultValue"};
 static List<object> ParameterChoices(MDBMetadataSet set,string property) {
  return Values(typeof(MetadataSetBase).GetProperty(property,BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Instance).GetValue(set,null));
 }
 static object ParameterChoice(MDBMetadataSet set,string property,string name) {
  var choices=ParameterChoices(set,property).Where(x=>Name(x)==name).ToList();
  if(choices.Count!=1) throw new ArgumentException("Unknown official parameter choice: "+property+":"+name);
  return choices[0];
 }
 static int ChoiceId(object choice,string property) {return Convert.ToInt32(choice.GetType().GetProperty(property).GetValue(choice,null));}
 static string DeclarationValue(MDBMetadataSet set,FunctionParameter parameter,string key,bool strict) {
  if(key=="CDOType" && parameter.CDODefId!=0) {
   CDODefinition cdo;if(set.CDODefinitions.TryGetValue(parameter.CDODefId,out cdo)) return cdo.Name;
   if(strict) throw new ArgumentException("具体对象类型 ID 无法由官方目录解析");
  }
  string collection=null,idProperty=null;int identity=0;
  if(key=="DataType") {collection="CPPDataTypes";idProperty="DataTypeId";identity=parameter.DataTypeId;}
  if(key=="ParamMultiplicity") {collection="ParamMultiplicities";idProperty="MultiplicityId";identity=parameter.ParamMultiplicityId;}
  if(key=="TypeMultiplicity") {collection="TypeMultiplicities";idProperty="MultiplicityId";identity=parameter.TypeMultiplicityId;}
  if(key=="FunctionParmUsage") {collection="FunParmUsages";idProperty="FunctionParmUsageId";identity=parameter.FunctionParmUsageID;}
  if(collection!=null) {
   var values=ParameterChoices(set,collection).Where(x=>ChoiceId(x,idProperty)==identity).ToList();
   if(values.Count==1) return key=="FunctionParmUsage" && identity==0?"":Name(values[0]);
   if(strict) throw new ArgumentException("声明参数的官方目录身份无法解析："+key);
  }
  return Convert.ToString(parameter.GetType().GetProperty(key).GetValue(parameter,null));
 }
 static Dictionary<string,object> DeclarationRecord(FunctionParameter parameter) {
  var record=Record(parameter);var set=(MDBMetadataSet)parameter.Metadata;
  record["raw_reference_values"]=Dict("CDOType",parameter.CDOType,"FunctionParmUsage",parameter.FunctionParmUsage);
  foreach(var key in DeclarationFields) record[key]=DeclarationValue(set,parameter,key,false);
  record["reference_resolution"]="official_metadata_ids_when_available";return record;
 }
 static Dictionary<string,object> DeclarationImpact(MDBMetadataSet set,CLFDefinition clf) {
  var bindings=set.CLFEventMaps.Values.Where(x=>x.CLFID==clf.CLFID && x.ModifiedStatus!=ModifiedStatusEnum.Deleted).ToList();
  var callers=set.CLFDefinitions.Values.SelectMany(c=>c.CLFFunctions.Where(f=>f.FunctionID==clf.FunctionID && f.ModifiedStatus!=ModifiedStatusEnum.Deleted).Select(f=>Dict("clf",c.Name,"call_id",f.CLFFunctionID))).ToArray();
  bool owned=clf.WorkspaceCode==(String.IsNullOrEmpty(set.WorkSpace)?clf.ActiveWorkspaceCode:set.WorkSpace) && !clf.ExistsInLowerWorkspace;
  bool used=clf.DefaultFunctionDefinition!=null && clf.DefaultFunctionDefinition.IsInUse();
  return Dict("customer_owned",owned,"structural_edit_allowed",owned && bindings.Count==0 && callers.Length==0 && !used,
   "binding_count",bindings.Count,"bindings",bindings.Select(x=>Dict("binding_id",x.CLFEventMapID,"cdo_id",x.CDODefID,"caller_id",x.CallerID)).ToArray(),
   "rule_callers",callers,"vendor_function_in_use",used,"dynamic_usage_verified",false,
   "notice","声明参数属于 CLF 生命周期。已有入口或调用者时需要显式迁移；不会自动重绑或覆盖附着值。动态调用范围未核实。");
 }
 static Dictionary<string,object> DeclarationEditor(MDBMetadataSet set,CLFDefinition clf) {
  return Dict("impact",DeclarationImpact(set,clf),"directions",new[]{"In","Out","In/Out","Return"},
   "data_types",ParameterChoices(set,"CPPDataTypes").Where(x=>Convert.ToBoolean(Record(x)["IsParamType"])).Select(Record).ToArray(),
   "param_multiplicities",ParameterChoices(set,"ParamMultiplicities").Select(Record).ToArray(),
   "type_multiplicities",ParameterChoices(set,"TypeMultiplicities").Select(Record).ToArray(),
   "usages",ParameterChoices(set,"FunParmUsages").Select(Record).ToArray());
 }
 static void SetDeclaration(MDBMetadataSet set,FunctionParameter parameter,Dictionary<string,object> definition) {
  string name=Text(definition,"Name");
  if(!System.Text.RegularExpressions.Regex.IsMatch(name,@"^[A-Za-z_][A-Za-z0-9_]{0,29}$")) throw new ArgumentException("Invalid declaration name");
  if(!new[]{"In","Out","In/Out","Return"}.Contains(Text(definition,"Direction"))) throw new ArgumentException("Unknown declaration direction");
  var data=ParameterChoice(set,"CPPDataTypes",Text(definition,"DataType"));
  if(!Convert.ToBoolean(Record(data)["IsParamType"])) throw new ArgumentException("Data type is not allowed for parameters");
  var multiplicity=ParameterChoice(set,"ParamMultiplicities",Text(definition,"ParamMultiplicity"));
  var type=ParameterChoice(set,"TypeMultiplicities",Text(definition,"TypeMultiplicity"));
  string usage=Text(definition,"FunctionParmUsage");
  var special=ParameterChoice(set,"FunParmUsages",usage==""?"Default":usage);
  string cdoName=Text(definition,"CDOType");
  if(cdoName!="" && Text(definition,"DataType")!="Object") throw new ArgumentException("Specific CDO type requires Object data type");
  var cdo=cdoName==""?null:(CDODefinition)Find(set,"cdo",cdoName,"");
  parameter.Name=name;parameter.Direction=Text(definition,"Direction");
  parameter.DataType=Name(data);parameter.DataTypeId=ChoiceId(data,"DataTypeId");
  parameter.ParamMultiplicity=Name(multiplicity);parameter.ParamMultiplicityId=ChoiceId(multiplicity,"MultiplicityId");
  parameter.TypeMultiplicity=Name(type);parameter.TypeMultiplicityId=ChoiceId(type,"MultiplicityId");
  parameter.FunctionParmUsage=ChoiceId(special,"FunctionParmUsageId")==0?"":Name(special);parameter.FunctionParmUsageID=ChoiceId(special,"FunctionParmUsageId");
  parameter.CDOType=cdo==null?"":cdo.Name;parameter.CDODefId=cdo==null?0:cdo.CDODefId;
  parameter.Description=Text(definition,"Description");parameter.Notes=Text(definition,"Notes");parameter.DefaultValue=Text(definition,"DefaultValue");
 }
 static BaseMetadataObject ChangeDeclaration(MDBMetadataSet set,Dictionary<string,object> op) {
  var clf=(CLFDefinition)Find(set,"clf",Text(op,"owner"),"");
  if(clf.WorkspaceCode!=set.WorkSpace || clf.ExistsInLowerWorkspace) throw new ArgumentException("声明参数仅允许在独立客户 CLF 上修改，请先复制客户规则");
  string action=Text(op,"action");var parameters=clf.Parameters.Values.Where(p=>p.ModifiedStatus!=ModifiedStatusEnum.Deleted).ToList();
  var parameter=action=="add_clf_declaration"?null:parameters.SingleOrDefault(p=>p.Name==Text(op,"parameter"));
  if(action!="add_clf_declaration") {
   if(parameter==null || parameter.WorkspaceCode!=set.WorkSpace || parameter.ExistsInLowerWorkspace) throw new ArgumentException("声明参数不存在或不属于当前客户工作区");
   var expected=Map(op["expected"]);
   foreach(var key in DeclarationFields) if(!expected.ContainsKey(key) || DeclarationValue(set,parameter,key,true)!=Text(expected,key)) throw new ArgumentException("声明参数原值已变化："+key);
  }
  var definition=action=="remove_clf_declaration"?null:Map(op["definition"]);
  bool structural=action!="update_clf_declaration" || DeclarationFields.Except(new[]{"Description","Notes"}).Any(key=>DeclarationValue(set,parameter,key,true)!=Text(definition,key));
  if(structural && !(bool)DeclarationImpact(set,clf)["structural_edit_allowed"]) throw new ArgumentException("该 CLF 已有事件绑定或调用者，声明结构变更需要明确的迁移方案；请先复制为未附着客户规则，不会自动重绑");
  if(parameter!=null && (action=="remove_clf_declaration" || parameter.Name!=Text(definition,"Name"))) {
   string expression=@"\b(?:DBCLF|CLF)::"+System.Text.RegularExpressions.Regex.Escape(parameter.Name)+@"\b";
   if(clf.CLFFunctions.Where(f=>f.ModifiedStatus!=ModifiedStatusEnum.Deleted).SelectMany(f=>f.Parameters.Values).Any(p=>System.Text.RegularExpressions.Regex.IsMatch(p.ValueExpression??"",expression,System.Text.RegularExpressions.RegexOptions.IgnoreCase))) throw new ArgumentException("规则表达式仍引用此声明参数，先明确修改引用后再改名或删除");
  }
  if(action=="remove_clf_declaration") {RememberRemoval(parameter);parameter.SetModifiedStatus(ModifiedStatusEnum.Deleted);Changed(clf.DefaultFunctionDefinition);Changed(clf);return parameter;}
  if(parameters.Any(p=>p!=parameter && p.Name.Equals(Text(definition,"Name"),StringComparison.OrdinalIgnoreCase))) throw new ArgumentException("同一 CLF 已有同名声明参数");
  bool fresh=parameter==null;
  if(fresh) {parameter=clf.DefaultFunctionDefinition.NewParameter(set);parameter.FunctionID=clf.FunctionID;parameter.ParentFunctionDefinition=clf.DefaultFunctionDefinition;parameter.WorkspaceCode=set.WorkSpace;parameter.ParmNumber=parameters.Select(p=>p.ParmNumber).DefaultIfEmpty(0).Max()+1;}
  SetDeclaration(set,parameter,definition);
  if(fresh){parameter.SetModifiedStatus(ModifiedStatusEnum.New);if(!clf.Parameters.ContainsKey(parameter.FunctionParamID))clf.AddParameter(parameter);}else Changed(parameter);
  Changed(clf.DefaultFunctionDefinition);Changed(clf);return parameter;
 }
 static readonly HashSet<string> ReadOnlyKinds=new HashSet<string>(new[]{"workspace","event","feature","data_type","sql_type","db_type","storage_category","cdo_usage","query_type","clf_type","label_category"});
 static readonly HashSet<string> Protected=new HashSet<string>(new[]{
  "Metadata","WorkspaceCode","WorkspaceOverride","LowerWorkspaceOverride","OverrideToDelete","ExistsInLowerWorkspace",
  "ModifiedStatus","InheritedID","InheritedField","InheritMask","SequenceNumber","Sequence","CDODefinition","ParentFunctionDefinition",
  "DBTable","ParentCLFDefinition","CDOName","FieldDefId","CPPDataTypeID","CPPDataTypeId","CDODefId","FieldID",
  "ParentQueryDef","ParentCLFFunction","ParentCLFEventMap","ParentCLFDefintion","CPPDataType","CDOType",
  "CDOUsageMask","ParentCDO","ParentCDOName","StorageCategoryName"
 });
 static bool Editable(PropertyInfo p) {
  if(!p.CanWrite || Protected.Contains(p.Name) || p.Name.EndsWith("Id",StringComparison.OrdinalIgnoreCase)
   || p.Name.EndsWith("Modified") || p.Name.StartsWith("IsAssociated")) return false;
  var t=p.PropertyType;
  // Reference setters in this assembly do not consistently synchronize their ID
  // properties. Relationships must use dedicated vendor factory/change methods.
  return t.IsPrimitive || t.IsEnum || t==typeof(string);
 }
 static object Schema(object obj,string kind) {
  return obj.GetType().GetProperties().Where(p=>p.GetIndexParameters().Length==0 && p.Name!="Metadata").Select(p=>Dict(
   "name",p.Name,"type",p.PropertyType.Name,"editable",!ReadOnlyKinds.Contains(kind)&&Editable(p),
   "enum_values",p.PropertyType.IsEnum?(object)Enum.GetNames(p.PropertyType):null)).ToArray();
 }
 static void SetValue(MDBMetadataSet set,object obj,string key,object value) {
  var p=obj.GetType().GetProperty(key);
  if(p==null || !Editable(p)) throw new ArgumentException("Property cannot be edited: "+key);
  object converted;
  if(typeof(BaseMetadataObject).IsAssignableFrom(p.PropertyType)) {
   var r=Map(value); converted=Find(set,Text(r,"kind"),Text(r,"name"),Text(r,"owner"));
   if(!p.PropertyType.IsInstanceOfType(converted)) throw new ArgumentException("Reference type mismatch: "+key);
   if(key=="ParentCDO") {
    for(var a=(CDODefinition)converted;a!=null;a=a.ParentCDO) if(a==obj) throw new ArgumentException("CDO inheritance cycle");
   }
  } else if(p.PropertyType.IsEnum) {
   converted=Enum.Parse(p.PropertyType,Convert.ToString(value),false);
   if(!Enum.IsDefined(p.PropertyType,converted)) throw new ArgumentException("Unknown enum value: "+key);
  }
  else converted=Convert.ChangeType(value,p.PropertyType,System.Globalization.CultureInfo.InvariantCulture);
  if(key=="PrecisionValue" && obj is FieldDefinition && ((FieldDefinition)obj).CPPDataType=="String" && Convert.ToInt32(converted)<1)
   throw new ArgumentException("String maximum length must be positive");
  p.SetValue(obj,converted,null);
 }
 static void CheckName(MDBMetadataSet set,string kind,string name,string owner) {
  if(Objects(set,kind,owner).Any(x=>String.Equals(Name(x),name,StringComparison.OrdinalIgnoreCase))) throw new ArgumentException("Name already exists: "+name);
 }
 static Dictionary<string,object> TypeProfile(FieldDefinition type) {
  return Dict("field_type",type.FieldDefName,"data_type",type.CPPDataType,
   "precision",type.PrecisionValue,"scale",type.Scale,"workspace",type.WorkspaceCode);
 }
 static void CheckExpectedType(FieldDefinition type,Dictionary<string,object> op) {
  if(!op.ContainsKey("expected_type")) return;
  var expected=Map(op["expected_type"]);
  if(expected.ContainsKey("data_type") && !String.Equals(type.CPPDataType,Text(expected,"data_type"),StringComparison.OrdinalIgnoreCase))
   throw new ArgumentException("引用字段类型 "+type.FieldDefName+" 的数据类型不符：期望 "+Text(expected,"data_type")+"，实际 "+type.CPPDataType);
  if(expected.ContainsKey("precision") && type.PrecisionValue!=Number(expected,"precision"))
   throw new ArgumentException("引用字段类型 "+type.FieldDefName+" 的长度/精度不符：期望 "+Number(expected,"precision")+"，实际 "+type.PrecisionValue);
  if(expected.ContainsKey("scale") && type.Scale!=Number(expected,"scale"))
   throw new ArgumentException("引用字段类型 "+type.FieldDefName+" 的小数位不符：期望 "+Number(expected,"scale")+"，实际 "+type.Scale);
 }
 static Dictionary<string,object> FieldProfile(CDOField field) {
  var origin=field; var seen=new HashSet<int>();
  while(origin.InheritedID!=0 && seen.Add(origin.FieldID) && origin.InheritedField!=null) origin=origin.InheritedField;
  var type=field.FieldDefinition;
  return Dict("name",field.FieldName,"field_type",type.FieldDefName,"data_type",type.CPPDataType,
   "precision",type.PrecisionValue,"scale",type.Scale,"persistent",!field.IsNonPersistent,
   "is_list",field.IsListType,"origin",origin.CDODefinition.Name,"field_usage",field.CDOFieldUsageName,
   "system_field",field.CDOFieldUsageId!=0,"description",field.FieldDescription);
 }
 static Dictionary<string,object> FieldCatalog(CDODefinition cdo) {
  var columns=new[]{"name","field_type","data_type","precision","scale","persistent","is_list","origin","description"};
  var rows=Values(cdo.AllFields).Cast<CDOField>().OrderBy(x=>x.FieldName).Select(field=>{
   var profile=FieldProfile(field); return columns.Select(column=>profile[column]).ToArray();
  }).ToArray();
  return Dict("format","designer_field_catalog_v1","owner",cdo.Name,"total_fields",rows.Length,
              "complete",true,"columns",columns,"rows",rows);
 }
 static string Category(CDODefinition cdo) {
  var ancestors=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
  for(var item=cdo;item!=null && ancestors.Add(item.Name);item=item.ParentCDO) {}
  if(ancestors.Contains("HistoryMainline") || ancestors.Contains("HistoryDetails")) return "历史记录";
  if(ancestors.Contains("Inquiry")) return "查询服务";
  if(ancestors.Contains("Service")) return "业务服务";
  if(ancestors.Contains("Subentity")) return "子实体／明细";
  if(ancestors.Contains("RevisionedObject")) return "版本化主数据";
  if(ancestors.Contains("NamedDataObject")) return "命名主数据";
  return "高级对象";
 }
 static Dictionary<string,object> ObjectProfile(CDODefinition cdo) {
  var fields=cdo.AllFields.Values.OrderBy(f=>f.FieldName,StringComparer.OrdinalIgnoreCase).Select(FieldProfile).ToArray();
  return Dict("name",cdo.Name,"parent",cdo.ParentCDOName,"category",Category(cdo),
   "usage",cdo.CDOUsageMask,"usage_id",cdo.CDOUsageMaskId,"storage_category",cdo.StorageCategoryName,
   "total_fields",fields.Length,"fields",fields,"complete",true);
 }
 static string CollisionBeforeFactoryRename(string generated,IEnumerable<string> priorNames) {
  // The installed factory's GetUniqueCDOName appends _<number> on collision.
  // Inspect its actual output instead of guessing Maint/Base/Changes templates.
  foreach(var prior in priorNames) {
   string prefix=prior+"_"; int suffix;
   if(generated.StartsWith(prefix,StringComparison.OrdinalIgnoreCase) &&
      Int32.TryParse(generated.Substring(prefix.Length),out suffix) && suffix>0) return prior;
  }
  return null;
 }
 static bool NeedsMaintenanceField(MDBMetadataSet set,CDODefinition cdo,Dictionary<string,object> op) {
  if(op.ContainsKey("portal_editable")) return Flag(op,"portal_editable");
  if(!Flag(op,"persistent") || !(cdo.IsNamed || cdo.IsRevisioned) || cdo.MaintenanceCDOID==0) return false;
  // An inherited maintenance service may still target the parent data object.
  // Default pairing applies only to this object's actual maintenance service.
  var maint=set.CDODefinitions[cdo.MaintenanceCDOID];
  var target=maint.GetFieldByName("ObjectToChange",true);
  return target!=null && target.FieldDefinition.CDODefId==cdo.CDODefId;
 }
 static CDOField AddMaintenanceField(MDBMetadataSet set,CDODefinition cdo,CDOField source) {
  if(!(cdo.IsNamed || cdo.IsRevisioned) || cdo.MaintenanceCDOID==0)
   throw new ArgumentException("Portal editing requires an existing named/revisioned object's maintenance service: "+cdo.Name);
  var maint=set.CDODefinitions[cdo.MaintenanceCDOID];
  var target=maint.GetFieldByName("ObjectToChange",true);
  var link=maint.GetFieldByName("ObjectChanges",true);
  if(target==null || link==null || target.FieldDefinition.CDODefId!=cdo.CDODefId)
   throw new ArgumentException("Maintenance ObjectToChange/ObjectChanges links do not match "+cdo.Name);
  var changes=set.CDODefinitions[link.FieldDefinition.CDODefId];
  var existing=changes.AllFields.Values.FirstOrDefault(f=>f.FieldName.Equals(source.FieldName,StringComparison.OrdinalIgnoreCase));
  if(existing!=null) {
   if(existing.FieldDefId!=source.FieldDefId || existing.IsListType!=source.IsListType ||
      !existing.AllowClientToRead || !existing.AllowClientToUpdate)
    throw new ArgumentException("Maintenance field conflicts; inspect before changing: "+changes.Name+"."+source.FieldName);
   return existing;
  }
  if(changes.WorkspaceCode!=set.WorkSpace) changes.CreateWorkspaceOverride(set.WorkSpace,ModifiedStatusEnum.Modified);
  var field=changes.AddField(source.FieldDefinition,source.IsListType,source.FieldName,source.FieldDescription,0,0,false);
  field.AllowClientToRead=true;field.AllowClientToUpdate=true;
  AssociatedFields.Add(field);
  AssociatedOperations.Add(Dict("action","add_field","owner",changes.Name,"name",field.FieldName,"field_type",field.FieldDefinition.FieldDefName,"persistent",false,"is_list",field.IsListType));
  return field;
 }
 static object Preflight(MDBMetadataSet set,Dictionary<string,object> request) {
  var profiles=new List<object>(); var conflicts=new List<object>();
  var categories=new List<object>();
  foreach(var parentName in new[]{"NamedDataObject","RevisionedObject","Subentity","Service","Inquiry","HistoryMainline","HistoryDetails"}) {
   var candidate=set.CDODefinitions.Values.FirstOrDefault(c=>c.Name.Equals(parentName,StringComparison.OrdinalIgnoreCase));
   if(candidate!=null) categories.Add(Dict("category",Category(candidate),"parent",candidate.Name,
    "usage",candidate.CDOUsageMask,"total_fields",candidate.AllFields.Count));
  }
  Aliases.Clear(); RemovedIdentities.Clear(); AssociatedFields.Clear(); AssociatedOperations.Clear();
  int index=0;
  foreach(var item in (object[])request["operations"]) {
   index++; var op=Map(item); string action=Text(op,"action"),name=Text(op,"name"),owner=Text(op,"owner");
   try {
    if(action=="create_field_type") {
     var existingTypes=set.FieldDefinitions.Values.Where(t=>t.FieldDefName.Equals(name,StringComparison.OrdinalIgnoreCase)).ToArray();
     if(existingTypes.Length==1) {
      var existing=existingTypes[0]; string dataType=Text(op,"data_type");
      string precisionKey=dataType=="String" ? "max_length" : "precision";
      var requested=Dict("field_type",name,"data_type",dataType,
       "precision",op.ContainsKey(precisionKey) ? (object)Number(op,precisionKey) : null,
       "scale",op.ContainsKey("scale") ? (object)Number(op,"scale") : null,"workspace",set.WorkSpace);
      bool compatible=String.Equals(existing.CPPDataType,dataType,StringComparison.OrdinalIgnoreCase) &&
       (!op.ContainsKey(precisionKey) || existing.PrecisionValue==Number(op,precisionKey)) &&
       (!op.ContainsKey("scale") || existing.Scale==Number(op,"scale"));
      conflicts.Add(Dict("operation",index,"kind","field_type_name","owner",owner,"name",name,
       "existing_type",TypeProfile(existing),"requested_type",requested,"compatible_shape",compatible,
       "message",compatible ? "已有同名字段类型，已指定的类型与精度相符；请核对其他语义后明确引用现有 field_type，并移除新增类型操作。" : "已有同名字段类型，但请求的类型或精度不符；请明确使用其他类型名或核对现有类型，不能重复创建或修改共享类型。"));
      continue;
     }
    }
    if(action=="add_field") {
     var cdo=(CDODefinition)Find(set,"cdo",owner,"");
     var existing=cdo.AllFields.Values.FirstOrDefault(f=>f.FieldName.Equals(name,StringComparison.OrdinalIgnoreCase));
     if(existing!=null) {
      var type=(FieldDefinition)Find(set,"field_type",Text(op,"field_type"),"");
      bool compatible=existing.FieldDefinition.CPPDataType==type.CPPDataType && existing.FieldDefinition.PrecisionValue==type.PrecisionValue &&
       existing.FieldDefinition.Scale==type.Scale && existing.IsListType==Flag(op,"is_list") && !existing.IsNonPersistent==Flag(op,"persistent") &&
       (type.CPPDataType!="Object" || existing.FieldDefId==type.FieldDefID);
      conflicts.Add(Dict("operation",index,"kind","field_name","owner",owner,"name",name,
       "existing",FieldProfile(existing),"requested",Dict("field_type",type.FieldDefName,"data_type",type.CPPDataType,
        "precision",type.PrecisionValue,"scale",type.Scale,"persistent",Flag(op,"persistent"),"is_list",Flag(op,"is_list")),
       "compatible_shape",compatible,"message",compatible ? "已有同名字段，类型、精度、持久化和列表属性相符；请明确复用并移除新增操作，不能重复创建。" : "已有同名字段，类型或属性不同；请明确改名或使用允许的客户字段覆盖。"));
      continue;
     }
    }
    var beforeCDOs=new HashSet<int>(set.CDODefinitions.Keys);
    var beforeTables=new HashSet<int>(set.DBTableDefinitions.Keys);
    var beforeCDONames=set.CDODefinitions.Values.Select(c=>c.Name).ToArray();
    var beforeTableNames=set.DBTableDefinitions.Values.Select(t=>t.Name).ToArray();
    var target=Apply(set,op);
    if(action=="create_cdo") {
     var cdo=(CDODefinition)target;
     var profile=ObjectProfile(cdo);
     profile["parent"] = Text(op,"parent");
     profile["create_table"]=Flag(op,"create_table");
     profile["create_revision_base"]=Flag(op,"create_revision_base");
     profile["create_maintenance"]=Flag(op,"create_maintenance");
     profile["related_objects"]=set.CDODefinitions.Values.Where(c=>!beforeCDOs.Contains(c.CDODefId) && c.CDODefId!=cdo.CDODefId).Select(c=>Dict("name",c.Name,"parent",c.ParentCDOName)).ToArray();
     profile["tables"]=set.DBTableDefinitions.Values.Where(t=>!beforeTables.Contains(t.DBTableId)).Select(t=>t.Name).ToArray();
     foreach(var related in set.CDODefinitions.Values.Where(c=>!beforeCDOs.Contains(c.CDODefId))) {
      if(set.CDODefinitions.Values.Count(c=>c.Name.Equals(related.Name,StringComparison.OrdinalIgnoreCase))>1)
       conflicts.Add(Dict("operation",index,"kind","derived_name","owner",name,"name",related.Name,"message","厂商生成的对象名称与现有定义重复，请调整对象名或派生结构。"));
      var collided=CollisionBeforeFactoryRename(related.Name,beforeCDONames);
      if(collided!=null && related.CDODefId!=cdo.CDODefId)
       conflicts.Add(Dict("operation",index,"kind","derived_name","owner",name,"name",related.Name,"existing_name",collided,
        "message","厂商因已有对象 "+collided+" 自动改名为 "+related.Name+"；请明确调整对象名或派生结构后重新预检。"));
     }
     foreach(var table in set.DBTableDefinitions.Values.Where(t=>!beforeTables.Contains(t.DBTableId))) {
      if(set.DBTableDefinitions.Values.Count(t=>t.Name.Equals(table.Name,StringComparison.OrdinalIgnoreCase))>1)
       conflicts.Add(Dict("operation",index,"kind","table_name","owner",name,"name",table.Name,"message","厂商生成的表名与现有表重复，请明确表名。"));
      var collided=CollisionBeforeFactoryRename(table.Name,beforeTableNames);
      var requestedTable=Text(op,"table_name");
      if(collided!=null && !table.Name.Equals(String.IsNullOrEmpty(requestedTable)?name:requestedTable,StringComparison.OrdinalIgnoreCase))
       conflicts.Add(Dict("operation",index,"kind","table_name","owner",name,"name",table.Name,"existing_name",collided,
        "message","厂商因已有表 "+collided+" 自动改名为 "+table.Name+"；请明确表名后重新预检。"));
     }
     profiles.Add(profile);
    }
   } catch(Exception error) {
    conflicts.Add(Dict("operation",index,"kind","invalid_operation","owner",owner,"name",name,
     "message",error.Message));
   }
  }
  // Factories and setters above affect only this isolated in-memory graph.
  // Deliberately do not call SaveAll or export/compile in preflight mode.
  try {FieldContracts(set,Dict("fields",ChangedContractFields(request)));}
  catch(Exception error) {conflicts.Add(Dict("kind","maintenance_contract","message",error.Message));}
  var clfNames=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
  foreach(var item in (object[])request["operations"]) {
   var op=Map(item);string action=Text(op,"action");
   if(action=="create_clf" || action=="copy_clf") clfNames.Add(Text(op,"name"));
   else if(action.Contains("clf")) clfNames.Add(Text(op,"owner"));
   if(op.ContainsKey("clf")) clfNames.Add(Text(op,"clf"));
  }
  var clfPreviews=set.CLFDefinitions.Values.Where(c=>clfNames.Contains(c.Name)).Select(ClfRecord).ToArray();
  return Dict("ready",conflicts.Count==0,"objects",profiles,"conflicts",conflicts,"categories",categories,"clfs",clfPreviews,
   "associated_operations",AssociatedOperations.ToArray(),
   "maintenance_fields",AssociatedFields.Select(f=>Dict("owner",f.CDODefinition.Name,"name",f.FieldName,"field_type",f.FieldDefinition.FieldDefName)).ToArray(),
   "usage_definition_count",set.CDOUsages.Count,
   "workspace",set.WorkSpace,"complete",true,"read_only",true,"method","Camstar.Metadata.CreateNewCDO/AllFields (no SaveAll)");
 }
 static object[] ChangedContractFields(Dictionary<string,object> request) {
  var fields=new Dictionary<string,object>();
  foreach(var raw in (object[])request["operations"]) {
   var op=Map(raw); string action=Text(op,"action"),owner=Text(op,"owner"),name=Text(op,"name");
   if(owner=="" || !(Text(op,"kind")=="field" || action=="add_field" || action=="change_field_type" || action=="remove_field_override")) continue;
   fields[owner+"."+name]=Dict("owner",owner,"name",name,"present",false);
   if(action=="patch") {
    var changes=Map(op["changes"]); string renamed=Text(changes,"FieldName",Text(changes,"Name",name));
    fields[owner+"."+renamed]=Dict("owner",owner,"name",renamed,"present",false);
   }
  }
  return fields.Values.ToArray();
 }
 static object FieldContracts(MDBMetadataSet set,Dictionary<string,object> request) {
  var result=new List<object>();
  foreach(var raw in (object[])request["fields"]) {
   var row=Map(raw); string owner=Text(row,"owner"),name=Text(row,"name");
   var cdo=(CDODefinition)Find(set,"cdo",owner,"");
   var field=cdo.AllFields.Values.FirstOrDefault(f=>f.ModifiedStatus!=ModifiedStatusEnum.Deleted && f.FieldName==name);
   if(Flag(row,"present") && field==null) throw new ArgumentException("Final MDB missing expected field: "+owner+"."+name);
   var changes=MaintenanceChanges(set,cdo);
   var paired=changes==null ? null : changes.AllFields.Values.FirstOrDefault(f=>f.ModifiedStatus!=ModifiedStatusEnum.Deleted && f.FieldName==name);
   if(paired!=null && (field==null || paired.FieldDefId!=field.FieldDefId || paired.IsListType!=field.IsListType))
    throw new ArgumentException("Final MDB maintenance field mismatch: "+owner+"."+name);
   if(paired!=null && (!paired.AllowClientToRead || !paired.AllowClientToUpdate))
    throw new ArgumentException("Portal maintenance field requires client read/update access: "+changes.Name+"."+name);
   // Direct edits to ObjectChanges must obey the same relationship as edits
   // initiated from the data object. Never accept two different field shapes.
   foreach(var data in set.CDODefinitions.Values.Where(c=>c.IsNamed || c.IsRevisioned)) {
    var linked=MaintenanceChanges(set,data);
    if(linked==null || linked.CDODefId!=cdo.CDODefId) continue;
    var source=data.AllFields.Values.FirstOrDefault(f=>f.ModifiedStatus!=ModifiedStatusEnum.Deleted && f.FieldName==name);
    if(source!=null && (field==null || source.FieldDefId!=field.FieldDefId || source.IsListType!=field.IsListType))
     throw new ArgumentException("Final MDB reverse maintenance field mismatch: "+owner+"."+name+" -> "+data.Name);
    if(source!=null && field!=null && (!field.AllowClientToRead || !field.AllowClientToUpdate))
     throw new ArgumentException("Portal maintenance field requires client read/update access: "+owner+"."+name);
   }
   var output=new Dictionary<string,object>(row); output["present"]=field!=null;
   if(field!=null) {
    var type=field.FieldDefinition;
    output["data_type"]=type.CPPDataType;output["precision"]=type.PrecisionValue;output["scale"]=type.Scale;
    output["is_list"]=field.IsListType;output["field_type"]=type.FieldDefName;
   }
   result.Add(output);
  }
  return Dict("fields",result.ToArray(),"complete",true);
 }
 static object Apply(MDBMetadataSet set,Dictionary<string,object> op) {
  string action=Text(op,"action"), kind=Text(op,"kind"), name=Text(op,"name"), owner=Text(op,"owner");
  if((action=="patch" || action=="delete") && (kind=="clf_parameter" || kind=="function_parameter" && set.CLFDefinitions.Values.Any(c=>c.FunctionID==((FunctionParameter)Find(set,kind,name,owner)).FunctionID))) throw new ArgumentException("CLF 声明参数必须使用专用操作，不能通过函数参数别名绕过生命周期检查");
  if(action=="patch") {
   if(ReadOnlyKinds.Contains(kind)) throw new ArgumentException("This definition category is read-only");
   var obj=Find(set,kind,name,owner); var changes=Map(op["changes"]); var expected=Map(op["expected"]);
   if(obj is FieldDefinition && changes.Keys.Any(key=>new[]{"PrecisionValue","Scale","CPPDataTypeID","CPPDataType"}.Contains(key,StringComparer.OrdinalIgnoreCase)) &&
      set.AllCDOFields.Values.Any(f=>f.ModifiedStatus!=ModifiedStatusEnum.Deleted && f.FieldDefId==((FieldDefinition)obj).FieldDefID))
    throw new ArgumentException("Referenced field type shape cannot be patched globally; create a private type and use change_field_type so data/maintenance fields migrate together: "+name);
   foreach(var item in changes) {
    if(!expected.ContainsKey(item.Key)) throw new ArgumentException("Expected value required: "+item.Key);
    var p=obj.GetType().GetProperty(item.Key);
    if(p==null || !Editable(p)) throw new ArgumentException("Property cannot be edited: "+item.Key);
    if(new[]{"Name","FieldName","FieldDefName"}.Contains(item.Key) && !String.Equals(Name(obj),Convert.ToString(item.Value),StringComparison.OrdinalIgnoreCase))
     CheckName(set,kind,Convert.ToString(item.Value),owner);
    if(Json.Serialize(Scalar(p.GetValue(obj,null)))!=Json.Serialize(expected[item.Key])) throw new ArgumentException("Expected value conflict: "+item.Key);
   }
   if(obj is CDOField) PairMutation(set,(CDOField)obj,op);
   if(kind=="field" && !String.Equals(((CDOField)obj).CDODefinition.Name,owner,StringComparison.OrdinalIgnoreCase)) {
    var target=(CDODefinition)Find(set,"cdo",owner,"");
    if(target.WorkspaceCode!=set.WorkSpace) target.CreateWorkspaceOverride(set.WorkSpace,ModifiedStatusEnum.Modified);
    obj=target.AddFieldOverride((CDOField)obj);
   }
   var workspaceObj=obj as WorkspaceControlledObject;
   if(workspaceObj!=null && workspaceObj.WorkspaceCode!=set.WorkSpace) workspaceObj.CreateWorkspaceOverride(set.WorkSpace,ModifiedStatusEnum.Modified);
   bool isNew=((BaseMetadataObject)obj).ModifiedStatus==ModifiedStatusEnum.New;
   foreach(var item in changes) SetValue(set,obj,item.Key,item.Value);
    if(obj is QueryText) {
     var query=((QueryText)obj).ParentQueryDef;
     query.SyncParametersWithQueryText();
     if(query.ModifiedStatus!=ModifiedStatusEnum.New) query.SetModifiedStatus(ModifiedStatusEnum.Modified);
    }
    if(changes.Keys.Any(x=>new[]{"Name","FieldName","FieldDefName"}.Contains(x))) Aliases[kind+":"+owner+":"+name]=obj;
   ((BaseMetadataObject)obj).SetModifiedStatus(isNew?ModifiedStatusEnum.New:ModifiedStatusEnum.Modified);
    return obj;
  }
  if(action=="create_cdo") {
   if(Flag(op,"create_table") && String.IsNullOrWhiteSpace(Text(op,"table_description")))
    throw new ArgumentException("create_table=true 时必须指定非空 table_description；Designer 保存不接受空表描述");
   CheckName(set,"cdo",name,""); var parent=(CDODefinition)Find(set,"cdo",Text(op,"parent"),"");
   var cdo=new CDODefinition(); cdo.Metadata=set;
   cdo=cdo.CreateNewCDO(name,parent,Text(op,"description"),parent.CDOUsageMaskId,name,Flag(op,"create_table"),parent.StorageCategoryId,
    Text(op,"table_name"),Text(op,"table_description"),Flag(op,"create_revision_base"),Flag(op,"create_maintenance"));
   return (cdo);
  }
  if(action=="change_parent") {
   var cdo=(CDODefinition)Find(set,"cdo",name,"");
   var parent=(CDODefinition)Find(set,"cdo",Text(op,"parent"),"");
   if(cdo.WorkspaceCode!=set.WorkSpace || cdo.ExistsInLowerWorkspace) throw new ArgumentException("Parent changes require a customer-owned CDO without a lower workspace definition");
   if(cdo.ParentCDOName!=Text(op,"expected_parent")) throw new ArgumentException("Parent precondition failed");
   for(var ancestor=parent;ancestor!=null;ancestor=ancestor.ParentCDO)
    if(ancestor.CDODefId==cdo.CDODefId) throw new ArgumentException("CDO inheritance cycle");
   if(cdo.CDOUsageMaskId!=parent.CDOUsageMaskId) throw new ArgumentException("Cross-category inheritance requires Designer migration; choose a parent in the same CDO usage category");
   foreach(var field in cdo.Fields.Values) {
    var inherited=parent.AllFields.Values.FirstOrDefault(x=>x.FieldName.Equals(field.FieldName,StringComparison.OrdinalIgnoreCase));
    if(inherited!=null && field.InheritedID!=inherited.FieldID) throw new ArgumentException("New parent has a conflicting field: "+field.FieldName);
    if(field.InheritedID!=0 && (inherited==null || field.InheritedID!=inherited.FieldID)) throw new ArgumentException("Field override does not exist on the new parent: "+field.FieldName);
   }
   var previous=cdo.ParentCDO;
   previous.ChildCDOs.Remove(cdo.CDODefId);
   parent.ChildCDOs[cdo.CDODefId]=cdo;
   cdo.ParentCDO=parent; cdo.ParentCDOID=parent.CDODefId;
   Changed(cdo);
   return cdo;
  }
  if(action=="change_storage_category") {
   var cdo=(CDODefinition)Find(set,"cdo",name,"");
   Find(set,"storage_category",Text(op,"category"),"");
   if(cdo.StorageCategoryName!=Text(op,"expected_category")) throw new ArgumentException("Storage category precondition failed");
   if(cdo.WorkspaceCode!=set.WorkSpace) cdo.CreateWorkspaceOverride(set.WorkSpace,ModifiedStatusEnum.Modified);
   bool fresh=cdo.ModifiedStatus==ModifiedStatusEnum.New;
   cdo.StorageCategoryName=Text(op,"category"); cdo.SetModifiedStatus(fresh?ModifiedStatusEnum.New:ModifiedStatusEnum.Modified);
   return cdo;
  }
  if(action=="sync_query_parameters") {
   var query=(QueryDef)Find(set,"query",owner,"");
   if(query.WorkspaceCode!=set.WorkSpace) query.CreateWorkspaceOverride(set.WorkSpace,ModifiedStatusEnum.Modified);
   query.SyncParametersWithQueryText();
   if(query.ModifiedStatus!=ModifiedStatusEnum.New) query.SetModifiedStatus(ModifiedStatusEnum.Modified);
   return query;
  }
  if(action=="remove_field_override") {
   var cdo=(CDODefinition)Find(set,"cdo",owner,""); var field=(CDOField)Find(set,"field",name,owner);
   if(field.WorkspaceCode!=set.WorkSpace || field.CDODefinition.CDODefId!=cdo.CDODefId) throw new ArgumentException("Only a current customer field override can be removed");
   if(field.FieldDefinition.FieldDefName!=Text(op,"expected_field_type")) throw new ArgumentException("Field override precondition failed");
   PairMutation(set,field,op);
   RememberRemoval(field);
   if(field.InheritedID!=0) cdo.RemoveCDOField(field.FieldName);
   else if(field.ExistsInLowerWorkspace) field.RemoveWorkspaceOverride();
   else throw new ArgumentException("This is an owned field, not an inherited override");
   Changed(cdo); return cdo;
  }
  if(action=="replace_clf_function" || action=="remove_clf_function") {
   var clf=(CLFDefinition)Find(set,"clf",owner,"");
   if(clf.WorkspaceCode!=set.WorkSpace) throw new ArgumentException("Call changes require customer-owned CLF");
   var call=clf.CLFFunctions.Single(x=>x.CLFFunctionID==Number(op,"call_id"));
   if(call.FunctionName!=Text(op,"expected_function")) throw new ArgumentException("Function precondition failed");
   var events=set.CLFEventMaps.Values.Where(x=>x.CLFID==clf.CLFID && x.ModifiedStatus!=ModifiedStatusEnum.Deleted).ToList();
   if(events.Any(x=>x.WorkspaceCode!=set.WorkSpace)) throw new ArgumentException("A bound event belongs to another workspace; use Designer to migrate it");
   foreach(var map in events) {map.RemoveCLFFunParmAttachedToCLFFunction(set,call); Changed(map);}
   RememberRemoval(call);
   foreach(var parameter in call.Parameters.Values) {RememberRemoval(parameter); parameter.SetModifiedStatus(ModifiedStatusEnum.Deleted);}
   int sequence=call.Sequence;
   // SaveCLFFunctions deletes only calls still present in the parent's list.
   call.SetModifiedStatus(ModifiedStatusEnum.Deleted);
   if(action=="replace_clf_function") {
    var function=(FunctionDefinition)Find(set,"function",Text(op,"function"),"");
    var replacement=new CLFFunction(function,set); replacement.AttachFunctionToCLF(clf,sequence);
    foreach(var map in events) if(!map.AllFunctionParameters.Values.Any(x=>x.CLFFunctionID==replacement.CLFFunctionID)) map.AddCLFFunParmToAttachedCLFFunction(set,replacement);
    Changed(clf); return replacement;
   }
   int index=1;
   foreach(var remaining in clf.CLFFunctions.Where(x=>x.ModifiedStatus!=ModifiedStatusEnum.Deleted).OrderBy(x=>x.Sequence)) {remaining.Sequence=index++; Changed(remaining);}
   Changed(clf); return clf;
  }
  if(action=="update_field_map" || action=="remove_field_map") {
   var map=(CDOMapDefinition)Find(set,"map",owner,"");
   if(map.WorkspaceCode!=set.WorkSpace) throw new ArgumentException("Field map changes require customer-owned map");
   var existing=(CDOFieldMapDefinition)Find(set,"field_map",name,owner);
   if(existing.SourceCDOFieldName!=Text(op,"expected_source_field") || existing.TargetCDOFieldName!=Text(op,"expected_target_field")) throw new ArgumentException("Field map precondition failed");
   RememberRemoval(existing); map.RemoveFieldMap(existing.SourceCDOFieldName); Changed(map);
   if(action=="remove_field_map") return map;
   var source=(CDOField)Find(set,"field",Text(op,"source_field"),Text(op,"source_cdo"));
   var target=(CDOField)Find(set,"field",Text(op,"target_field"),Text(op,"target_cdo"));
   var sourceCDO=(CDODefinition)Find(set,"cdo",Text(op,"source_cdo"),"");
   var targetCDO=(CDODefinition)Find(set,"cdo",Text(op,"target_cdo"),"");
   if(sourceCDO.CDODefId!=map.SourceCDOID || targetCDO.CDODefId!=map.TargetCDOID) throw new ArgumentException("Field mapping must retain the map's source and target CDOs");
   if(map.FieldMaps.Values.Any(x=>x.SourceCDOFieldID==source.FieldID && x.ModifiedStatus!=ModifiedStatusEnum.Deleted)) throw new ArgumentException("Another mapping already uses the new source field");
   return map.AddFieldMapDef(source,target);
  }
  if(action=="create_field_type") {
   CheckName(set,"field_type",name,""); string dataType=Text(op,"data_type");
   if(dataType=="Object") throw new ArgumentException("Object field types are created with the CDO");
   Find(set,"data_type",dataType,""); var fieldType=new FieldDefinition(); fieldType.Metadata=set;
   fieldType.CreateNewFieldDefinition(dataType); fieldType.FieldDefName=name; fieldType.Description=Text(op,"description");
   if(dataType=="String" && Number(op,"max_length")<1) throw new ArgumentException("String maximum length must be positive");
   if(op.ContainsKey("max_length")) fieldType.PrecisionValue=Number(op,"max_length");
   if(op.ContainsKey("precision")) fieldType.PrecisionValue=Number(op,"precision");
   if(op.ContainsKey("scale")) fieldType.Scale=Number(op,"scale");
   return (fieldType);
  }
  if(action=="add_field") {
   CheckName(set,"field",name,owner); var cdo=(CDODefinition)Find(set,"cdo",owner,"");
   var type=(FieldDefinition)Find(set,"field_type",Text(op,"field_type"),"");
   CheckExpectedType(type,op);
   if(cdo.WorkspaceCode!=set.WorkSpace) cdo.CreateWorkspaceOverride(set.WorkSpace,ModifiedStatusEnum.Modified);
   var f=cdo.AddField(type,Flag(op,"is_list"),name,Text(op,"description"),0,0,Flag(op,"persistent"));
   f.IsNonPersistent=!Flag(op,"persistent");
   if(NeedsMaintenanceField(set,cdo,op)) AddMaintenanceField(set,cdo,f);
   return (f);
  }
  if(action=="copy_clf") {
   CheckName(set,"clf",name,""); var src=(CLFDefinition)Find(set,"clf",Text(op,"template"),"");
   var dest=new CLFDefinition(set); dest.CopyAsNew(src,name); dest.CLFTypeId=src.CLFTypeId;
   set.CLFDefinitions.Add(dest.CLFID,dest); return (dest);
  }
  if(action=="create_query") {
   CheckName(set,"query",name,""); var q=new QueryDef(set);
   q.CreateNew((QueryType)Find(set,"query_type",Text(op,"query_type"),"")); q.Name=name; q.Description=Text(op,"description",name);
   q.AddQueryText(Number(op,"db_type_id"),Text(op,"text")); q.SyncParametersWithQueryText(); return (q);
  }
  if(action=="create_map") {
   var source=(CDODefinition)Find(set,"cdo",owner,""); var target=(CDODefinition)Find(set,"cdo",Text(op,"target"),"");
   var map=new CDOMapDefinition(); map.Metadata=set; map=map.CreateNewCDOMapDefinition(source.CDODefId,target.CDODefId,0);
   return (map);
  }
  if(action=="create_clf") {
   CheckName(set,"clf",name,"");
   var clf=CLFDefinition.New(set,(CLFType)Find(set,"clf_type",Text(op,"clf_type"),""));
   clf.Name=name; clf.Description=Text(op,"description",name); return (clf);
  }
  if(action=="add_clf_function") {
   var clf=(CLFDefinition)Find(set,"clf",owner,"");
   if(clf.WorkspaceCode!=set.WorkSpace) throw new ArgumentException("Copy CLF to customer workspace before changing its function list");
   var function=(FunctionDefinition)Find(set,"function",Text(op,"function"),"");
   var call=new CLFFunction(function,set); call.AttachFunctionToCLF(clf,Number(op,"sequence"));
   if(clf.ModifiedStatus!=ModifiedStatusEnum.New) clf.SetModifiedStatus(ModifiedStatusEnum.Modified); return (call);
  }
  if(action=="create_label") {
   CheckName(set,"label",name,""); var label=new Label(set,name,Text(op,"text"),Number(op,"category_id")); return (label);
  }
  if(action=="add_column") {
   CheckName(set,"column",name,owner);
   var table=(DBTableDefinition)Find(set,"table",owner,"");
   if(table.WorkspaceCode!=set.WorkSpace) table.CreateWorkspaceOverride(set.WorkSpace,ModifiedStatusEnum.Modified);
   var column=table.AddColumn(Number(op,"sql_type_id"),0); column.Name=name; column.Description=Text(op,"description");
   column.Precision=Number(op,"precision"); column.Scale=Number(op,"scale"); return (column);
  }
  if(action=="add_query_text") {
   var query=(QueryDef)Find(set,"query",owner,"");
   if(query.WorkspaceCode!=set.WorkSpace) query.CreateWorkspaceOverride(set.WorkSpace,ModifiedStatusEnum.Modified);
   if(query.QueryTexts.Values.Any(t=>t.DBTypeID==Number(op,"db_type_id"))) throw new ArgumentException("Query text already exists for DB type");
   query.AddQueryText(Number(op,"db_type_id"),Text(op,"text")); query.SyncParametersWithQueryText(); return (query);
  }
  if(action=="delete") {
   var obj=(WorkspaceControlledObject)Find(set,kind,name,owner);
   if(obj.WorkspaceCode!=set.WorkSpace || ReadOnlyKinds.Contains(kind)) throw new ArgumentException("Only customer-owned definitions can be deleted");
   var inUse=obj.GetType().GetMethod("IsInUse",Type.EmptyTypes);
   if(inUse==null || (bool)inUse.Invoke(obj,null)) throw new ArgumentException("Cannot prove definition is unused");
   if(obj is CDOField) PairMutation(set,(CDOField)obj,op);
   RememberRemoval(obj); obj.SetModifiedStatus(ModifiedStatusEnum.Deleted); return (obj);
  }
  if(action=="change_field_type") {
   var cdo=(CDODefinition)Find(set,"cdo",owner,"");
   var field=(CDOField)Find(set,"field",name,owner);
   if(field.FieldDefinition.FieldDefName!=Text(op,"expected_field_type")) throw new ArgumentException("Field type precondition failed");
   var type=(FieldDefinition)Find(set,"field_type",Text(op,"field_type"),"");
   PairMutation(set,field,op);
   if(cdo.WorkspaceCode!=set.WorkSpace) cdo.CreateWorkspaceOverride(set.WorkSpace,ModifiedStatusEnum.Modified);
   cdo.ChangeCDOFieldDefinition(name,type.FieldDefID);
   return (Find(set,"field",name,owner));
  }
  if(action=="create_index") {
   CheckName(set,"index",name,owner); var table=(DBTableDefinition)Find(set,"table",owner,"");
   if(table.WorkspaceCode!=set.WorkSpace) table.CreateWorkspaceOverride(set.WorkSpace,ModifiedStatusEnum.Modified);
   var index=new DBIndexDefinition(); index.Metadata=set; index.DBIndexID=set.GetNextId(index.GetType());
   index.WorkspaceCode=set.WorkSpace; index.DBTableID=table.DBTableId; index.DBTable=table; index.Name=name;
   index.Description=Text(op,"description",name); index.IsUnique=Flag(op,"is_unique");
   index.SetModifiedStatus(ModifiedStatusEnum.New); table.AddIndex(index);
   foreach(var value in (object[])op["columns"]) {
    var col=(DBColumn)Find(set,"column",Convert.ToString(value),owner); index.CreateNewEntry(col.DBColumnID);
   } return (index);
  }
  if(action=="add_field_map") {
   var map=(CDOMapDefinition)Find(set,"map",Text(op,"map"),"");
   if(map.WorkspaceCode!=set.WorkSpace) throw new ArgumentException("Field maps require a customer-owned map");
   var source=(CDOField)Find(set,"field",Text(op,"source_field"),Text(op,"source_cdo"));
   var target=(CDOField)Find(set,"field",Text(op,"target_field"),Text(op,"target_cdo"));
   if(map.SourceCDOID!=((CDODefinition)Find(set,"cdo",Text(op,"source_cdo"),"")).CDODefId || map.TargetCDOID!=((CDODefinition)Find(set,"cdo",Text(op,"target_cdo"),"")).CDODefId) throw new ArgumentException("Field mapping CDOs do not match the owner map");
   if(map.FieldMaps.Values.Any(x=>x.SourceCDOFieldID==source.FieldID && x.ModifiedStatus!=ModifiedStatusEnum.Deleted)) throw new ArgumentException("Source field is already mapped; use update_field_map");
   return (map.AddFieldMapDef(source,target));
  }
  if(action=="reorder_clf_functions") {
   var clf=(CLFDefinition)Find(set,"clf",owner,"");
   if(clf.WorkspaceCode!=set.WorkSpace) throw new ArgumentException("Function reorder requires customer-owned CLF");
   var ordered=clf.CLFFunctions.Where(x=>x.ModifiedStatus!=ModifiedStatusEnum.Deleted).OrderBy(x=>x.Sequence).ToList();
   var expected=((object[])op["expected_function_ids"]).Select(Convert.ToInt32).ToArray();
   var ids=((object[])op["function_ids"]).Select(Convert.ToInt32).ToArray();
   if(!ordered.Select(x=>x.CLFFunctionID).SequenceEqual(expected)) throw new ArgumentException("CLF order precondition failed");
   if(ids.Distinct().Count()!=ids.Length || !ids.OrderBy(x=>x).SequenceEqual(expected.OrderBy(x=>x))) throw new ArgumentException("Reorder must retain every function exactly once");
   bool isNew=clf.ModifiedStatus==ModifiedStatusEnum.New;
   for(int i=0;i<ids.Length;i++) {
    var f=ordered.Single(x=>x.CLFFunctionID==ids[i]); bool fresh=f.ModifiedStatus==ModifiedStatusEnum.New;
    f.Sequence=i+1; f.SetModifiedStatus(fresh?ModifiedStatusEnum.New:ModifiedStatusEnum.Modified);
   }
   clf.SetModifiedStatus(isNew?ModifiedStatusEnum.New:ModifiedStatusEnum.Modified); return (clf);
  }
  if(action=="set_clf_parameter") {
   var clf=(CLFDefinition)Find(set,"clf",owner,"");
   if(clf.WorkspaceCode!=set.WorkSpace) throw new ArgumentException("Parameter changes require customer-owned CLF");
   var call=clf.CLFFunctions.Single(x=>x.CLFFunctionID==Number(op,"call_id"));
   var parameter=call.ParametersByName[Text(op,"parameter")];
   if(parameter.ValueExpression!=Text(op,"expected_value")) throw new ArgumentException("Parameter value precondition failed");
   bool fresh=parameter.ModifiedStatus==ModifiedStatusEnum.New;
   parameter.ValueExpression=Text(op,"value"); parameter.SetModifiedStatus(fresh?ModifiedStatusEnum.New:ModifiedStatusEnum.Modified);
   return (parameter);
  }
  if(action=="add_clf_declaration" || action=="update_clf_declaration" || action=="remove_clf_declaration") return ChangeDeclaration(set,op);
  if(action=="bind_event" || action=="replace_event_binding" || action=="unbind_event") {
   var cdo=(CDODefinition)Find(set,"cdo",owner,"");
   if(cdo.WorkspaceCode!=set.WorkSpace) throw new ArgumentException("Event binding requires customer-owned CDO");
   var clf=action=="unbind_event" ? null : (CLFDefinition)Find(set,"clf",Text(op,"clf"),"");
   var ev=(CLFEventDefinition)Find(set,"event",Text(op,"event"),"");
   var feature=action=="unbind_event" ? null : (InSiteFeature)Find(set,"feature",Text(op,"feature"),"");
   string fieldName=Text(op,"field"); var field=fieldName=="" ? null : (CDOField)Find(set,"field",fieldName,owner);
   if(ev.CallerType.ToString()!=(field==null ? "CDO" : "Field")) throw new ArgumentException("Event caller type does not match CDO/field target");
   if(field!=null && field.WorkspaceCode!=set.WorkSpace) throw new ArgumentException("Field event binding requires customer-owned field");
   var events=field==null ? cdo.Events : field.Events;
   var existing=events.Values.SingleOrDefault(x=>x.CLFEventID==ev.CLFEventID);
   if(action!="bind_event") {
    if(existing==null || existing.CLFDefinition.Name!=Text(op,"expected_clf")) throw new ArgumentException("Event binding precondition failed");
    if(existing.WorkspaceCode!=set.WorkSpace) throw new ArgumentException("Only customer-owned event bindings can be changed");
    if(action=="unbind_event") {
     RememberRemoval(existing);
     foreach(var call in existing.CLFDefinition.CLFFunctions) existing.RemoveCLFFunParmAttachedToCLFFunction(set,call);
     existing.SetModifiedStatus(ModifiedStatusEnum.Deleted); events.Remove(existing.CLFEventMapID);
     if(field==null) {cdo.EventsModified=true; Changed(cdo); return cdo;}
     field.EventsModified=true; Changed(field); return field;
    }
    foreach(var call in existing.CLFDefinition.CLFFunctions) existing.RemoveCLFFunParmAttachedToCLFFunction(set,call);
    existing.CLFDefinition=clf; existing.CLFID=clf.CLFID; existing.FeatureId=feature.FeatureId;
    existing.CopyAllParametersFromCLF(set); existing.ParamsModified=true; Changed(existing);
    if(field==null) {cdo.EventsModified=true; Changed(cdo);} else {field.EventsModified=true; Changed(field);}
    return existing;
   }
   if(existing!=null) throw new ArgumentException("Event already bound; use replace_event_binding with expected_clf");
   var map=new CLFEventMap(); map.Metadata=set;
   map.CreateNewEventMap(clf,ev,field==null ? cdo.CDODefId : field.FieldID,cdo.CDODefId,feature.Name);
   set.CLFEventMaps.Add(map.CLFEventMapID,map); events.Add(map.CLFEventMapID,map);
   if(field==null) { cdo.EventsModified=true; if(cdo.ModifiedStatus!=ModifiedStatusEnum.New) cdo.SetModifiedStatus(ModifiedStatusEnum.Modified); }
   else { field.EventsModified=true; if(field.ModifiedStatus!=ModifiedStatusEnum.New) field.SetModifiedStatus(ModifiedStatusEnum.Modified); }
   return (map);
  }
  throw new ArgumentException("Unsupported Designer operation: "+action);
 }
 static MDBMetadataSet Load(string path,string folder,string workspace) {
  var set=new MDBMetadataSet("Provider=Microsoft.ACE.OLEDB.12.0;Data Source="+path, "");
  set.DisableAutoRepair=true; set.isConsole=true; set.ErrorFilePath=folder; set.LoadAllMetadata(null);
  if(set.hasErrors || set.CDODefinitions.Count==0 || set.AllCDOFields.Count==0)
   throw new InvalidOperationException("Vendor metadata load failed or returned an incomplete object graph");
  if(workspace!="") {
   var ws=set.GetWorkspaces().SingleOrDefault(x=>x.WorkspaceCode==workspace && x.IsActive);
   if(ws==null || workspace=="csi") throw new ArgumentException("Active customer workspace required");
   set.WorkSpace=workspace;
  } return set;
 }
 public static void Run(string requestFile,string resultFile) {
  try {
   var req=Map(Json.DeserializeObject(File.ReadAllText(requestFile))); string mode=Text(req,"mode");
   string folder=Path.GetDirectoryName(resultFile); object result;
   if(mode=="read_batch") {
    var requests=(object[])req["requests"];
    if(requests.Length<1 || requests.Length>20) throw new ArgumentException("Read batch requires 1-20 queries");
    foreach(var item in requests) if(!ReadModes.Contains(Text(Map(item),"mode"))) throw new ArgumentException("Read-only batch required");
    var results=new List<object>();
    bool ownsSet=BatchSet==null;
    try {
     if(ownsSet) BatchSet=Load(Text(req,"mdb"),folder,"");
     foreach(var item in requests) {
      var child=Map(item); child["mdb"]=Text(req,"mdb");
      string input=Path.Combine(folder,"batch-request.json"),output=Path.Combine(folder,"batch-result.json");
      File.WriteAllText(input,Json.Serialize(child)); Run(input,output);
      results.Add(Json.DeserializeObject(File.ReadAllText(output)));
     }
    } finally {if(ownsSet) BatchSet=null;}
    result=Dict("results",results);
   } else if(mode=="export") {
    // Vendor HTML conversion shows a modal dialog if its UI stylesheet is absent.
    // XML export itself is headless. Python renders a separate escaped report.
    new Camstar.Metadata.Comparison.CompareAndExport(Text(req,"base"),Text(req,"mdb"),Path.Combine(folder,"changes.xml"),"",false,false,true).Go();
    result=Dict("status","exported");
   } else if(mode=="compile") {
    string target=Path.Combine(folder,"compiled.mdb"), error="";
    new Camstar.Metadata.Util.MetadataCompile().CompileMDB(Text(req,"mdb"),target,ref error,true,true);
    if(error!="" || !File.Exists(target)) throw new InvalidOperationException("Metadata compile failed: "+error);
    result=Dict("status","compiled_test_copy","compiled_mdb",target);
   } else {
    var set=BatchSet ?? Load(Text(req,"mdb"),folder,Text(req,"workspace"));
    string kind=Text(req,"kind"),owner=Text(req,"owner"),name=Text(req,"name");
    if(mode=="field_contracts") {
     result=FieldContracts(set,req);
    } else if(mode=="preflight") {
     result=Preflight(set,req);
    } else if(mode=="inheritance") {
     var root=(CDODefinition)Find(set,"cdo",name,"");
     var descendants=new List<object>();
     foreach(var candidate in set.CDODefinitions.Values.OrderBy(x=>x.Name,StringComparer.OrdinalIgnoreCase)) {
      if(candidate.ModifiedStatus==ModifiedStatusEnum.Deleted || candidate==root) continue;
      var seen=new HashSet<CDODefinition>(); int depth=0;
      for(var parent=candidate.ParentCDO;parent!=null && seen.Add(parent);parent=parent.ParentCDO) {
       depth++;
       if(parent==root) {
        descendants.Add(Dict("name",candidate.Name,"parent",candidate.ParentCDO.Name,"depth",depth)); break;
       }
      }
     }
     result=Dict("root",root.Name,"parent",root.ParentCDO==null?null:root.ParentCDO.Name,
      "total",descendants.Count,"records",descendants,"complete",true);
    } else if(mode=="fields") {
     result=FieldCatalog((CDODefinition)Find(set,"cdo",name,""));
    } else if(mode=="catalog" && kind=="clf" && name=="rule_browser") {
     var owners=set.CDODefinitions.Values.Where(x=>x.ModifiedStatus!=ModifiedStatusEnum.Deleted).ToDictionary(x=>x.CDODefId);
     var bindings=set.CLFEventMaps.Values.Where(x=>x.ModifiedStatus!=ModifiedStatusEnum.Deleted && owners.ContainsKey(x.CDODefID))
      .Select(x=>Dict("clf",x.CLFReference,"object",owners[x.CDODefID].Name,
       "event",x.EventDefinition==null?"":x.EventDefinition.Name,"field_level",x.CallerID!=x.CDODefID)).ToArray();
     result=Dict("objects",owners.Values.OrderBy(x=>x.Name).Select(x=>Dict("name",x.Name,"label",x.DisplayNameLabel,
       "parent",x.ParentCDOName,"maintenance",x.MaintenanceCDOName,"is_service",x.IsService)).ToArray(),
      "rules",set.CLFDefinitions.Values.Where(x=>x.ModifiedStatus!=ModifiedStatusEnum.Deleted).OrderBy(x=>x.Name)
       .Select(x=>Dict("name",x.Name,"description",x.Description,"workspace",x.WorkspaceCode,
        "customer",x.WorkspaceCode==x.ActiveWorkspaceCode)).ToArray(),"bindings",bindings);
    } else if(mode=="catalog") {
     var inventory=new Dictionary<string,object>(); foreach(var k in Collections.Keys) inventory[k]=Objects(set,k,"").Count;
     result=Dict("counts",inventory,"workspaces",set.GetWorkspaces().Select(Record).ToArray());
    } else if(mode=="list") {
     var found=Objects(set,kind,owner).Where(x=>Name(x).IndexOf(Text(req,"search"),StringComparison.OrdinalIgnoreCase)>=0).OrderBy(Name).ToList();
     result=Dict("total",found.Count,"records",found.Skip(Number(req,"offset")).Take(Number(req,"limit",20)).Select(Record).ToArray());
    } else if(mode=="get" || mode=="schema" || mode=="impact") {
     var obj=Find(set,kind,name,owner);
     if(mode=="schema") result=Dict("properties",Schema(obj,kind));
     else if(mode=="impact") {
      var method=obj.GetType().GetMethod("GetWhereUsed",new[]{typeof(bool)});
      if(method==null && obj is CDOField) {
       var field=(CDOField)obj; var references=new List<object>();
       foreach(var map in set.CDOMapDefinitions.Values) foreach(var entry in map.FieldMaps.Values)
        if(entry.SourceCDOFieldID==field.FieldID || entry.TargetCDOFieldID==field.FieldID) references.Add(Dict("kind","field_map","owner",map.Name,"definition",Record(entry)));
       foreach(var ev in set.CLFEventMaps.Values.Where(x=>x.CallerID==field.FieldID && x.CDODefID==field.CDODefinition.CDODefId)) references.Add(Dict("kind","event_binding","definition",Record(ev)));
       foreach(var child in set.AllCDOFields.Values.Where(x=>x.InheritedID==field.FieldID)) references.Add(Dict("kind","field_override","owner",child.CDODefinition.Name,"definition",Record(child)));
       var inUse=field.GetType().GetMethod("IsInUse",Type.EmptyTypes);
       result=Dict("where_used",references.Take(100).ToArray(),"total",references.Count,"truncated",references.Count>100,
                   "in_use",inUse==null ? null : inUse.Invoke(field,null),"complete",false,
                   "coverage","field maps, direct event bindings and inherited overrides; expressions and runtime usage require Designer review");
      } else {
       if(method==null) throw new ArgumentException("Official where-used method unavailable for this kind");
       var used=method.Invoke(obj,new object[]{false});
       var table=used as System.Data.DataTable;
       if(table!=null) {
        // This SDK returns tabular references for FieldDefinition.GetWhereUsed.
        // Preserve its actual columns; a DataTable is not an IEnumerable.
        var rows=table.Rows.Cast<System.Data.DataRow>().Take(100).Select(row=>
         table.Columns.Cast<System.Data.DataColumn>().ToDictionary(column=>column.ColumnName,
          column=>row.IsNull(column) ? null : Scalar(row[column]))).ToArray();
        result=Dict("where_used",rows,"total",table.Rows.Count,"truncated",table.Rows.Count>100);
       } else {
        var references=Values(used);
        result=Dict("where_used",references.Take(100).Select(Record).ToArray(),"total",references.Count,"truncated",references.Count>100);
       }
      }
     } else {
      var record=Record(obj); if(obj is CDODefinition) {
       var fields=Values(((CDODefinition)obj).AllFields); record["fields"]=fields.Select(Record).ToArray();
       record["total_fields"]=fields.Count; record["fields_truncated"]=false;
       record["field_catalog"]=FieldCatalog((CDODefinition)obj);
       record["events"]=Values(((CDODefinition)obj).Events).Select(Record).ToArray();
      }
      if(obj is CDOField) record["events"]=Values(((CDOField)obj).Events).Select(Record).ToArray();
      if(obj is CLFDefinition) record=ClfRecord((CLFDefinition)obj);
      if(obj is CLFDefinition && owner=="attached_parameters") {
       var clf=(CLFDefinition)obj;
       record["declaration_editor"]=DeclarationEditor(set,clf);
       record["parameter_definitions"]=clf.CLFFunctions.Where(x=>x.FunctionDefinition!=null).Select(x=>x.FunctionDefinition)
        .GroupBy(x=>x.FunctionID).Select(group=>Dict("name",group.First().Name,
         "parameters",Values(group.First().Parameters).Select(Record).ToArray())).ToArray();
       record["bindings"]=set.CLFEventMaps.Values.Where(x=>x.CLFID==clf.CLFID && x.ModifiedStatus!=ModifiedStatusEnum.Deleted).Select(map=>{
        var binding=Record(map);
        CDODefinition cdo;
        binding["owner_name"]=set.CDODefinitions.TryGetValue(map.CDODefID,out cdo)?cdo.Name:"";
        binding["field_name"]=cdo==null || map.CallerID==map.CDODefID?"":Values(cdo.AllFields).Where(x=>((CDOField)x).FieldID==map.CallerID).Select(Name).FirstOrDefault();
        binding["attached_parameters"]=map.AllFunctionParameters.Values.Select(Record).ToArray();
        binding["parameter_scope"]="selected_vendor_event_map";
        return binding;
       }).ToArray();
      }
      if(obj is CLFFunction) record["parameters"]=Values(((CLFFunction)obj).Parameters).Select(Record).ToArray();
      if(obj is QueryDef) { record["query_texts"]=Values(((QueryDef)obj).QueryTexts).Select(Record).ToArray(); record["parameters"]=Values(((QueryDef)obj).Parameters).Select(Record).ToArray(); }
      result=record;
     }
    } else if(mode=="apply") {
     Aliases.Clear(); RemovedIdentities.Clear(); AssociatedFields.Clear(); AssociatedOperations.Clear();
     var targets=new List<BaseMetadataObject>(); var properties=new Dictionary<string,HashSet<string>>();
     foreach(var item in (object[])req["operations"]) {
      var op=Map(item); var target=(BaseMetadataObject)Apply(set,op); targets.Add(target);
      string identity=Identity(target);
      if(!properties.ContainsKey(identity)) properties[identity]=new HashSet<string>(CoreProperties(target));
      if(Text(op,"action")=="patch") foreach(var key in Map(op["changes"]).Keys) properties[identity].Add(key);
     }
     foreach(var associated in AssociatedFields) {
      targets.Add(associated);properties[Identity(associated)]=new HashSet<string>(CoreProperties(associated));
     }
     FieldContracts(set,Dict("fields",ChangedContractFields(req)));
     var records=targets.Select(Record).ToList();
     set.SaveAll();
     var verify=Load(Text(req,"mdb"),folder,Text(req,"workspace"));
     var verified=new List<object>();
     var types=new HashSet<string>(targets.Select(x=>x.GetType().Name)); foreach(var removed in RemovedIdentities.Keys) types.Add(removed.Split(':')[0]);
     var loaded=AllObjects(verify,types).Where(x=>x.ModifiedStatus!=ModifiedStatusEnum.Deleted).GroupBy(Identity).ToDictionary(x=>x.Key,x=>x.First());
     foreach(var removed in RemovedIdentities) {
      if(loaded.ContainsKey(removed.Key)) throw new InvalidOperationException("Removed definition still exists after reload: "+removed.Key);
      verified.Add(Dict("identity",removed.Key,"deleted",true,"passed",true,"previous",removed.Value));
     }
     foreach(var target in targets.Distinct()) {
      string identity=Identity(target); BaseMetadataObject actual;
      bool exists=loaded.TryGetValue(identity,out actual);
      if(RemovedIdentities.ContainsKey(identity)) {
       if(exists) throw new InvalidOperationException("Deleted definition still exists after reload: "+identity);
       verified.Add(Dict("identity",identity,"name",Name(target),"deleted",true,"passed",true));
       continue;
      }
      if(!exists) throw new InvalidOperationException("Definition missing after reload: "+identity);
      foreach(var key in properties[identity]) {
       var p=target.GetType().GetProperty(key); if(p==null || !p.CanRead) continue;
       if(Json.Serialize(Scalar(p.GetValue(target,null)))!=Json.Serialize(Scalar(p.GetValue(actual,null))))
        throw new InvalidOperationException("Saved property mismatch: "+identity+"."+key);
      }
      verified.Add(Record(actual));
     }
     var affected=new HashSet<string>();
     foreach(var target in targets) {
      if(target is CDODefinition) affected.Add(((CDODefinition)target).Name);
      if(target is CDOField) affected.Add(((CDOField)target).CDODefinition.Name);
      if(target is FieldDefinition) foreach(var field in set.AllCDOFields.Values.Where(x=>x.FieldDefId==((FieldDefinition)target).FieldDefID)) affected.Add(field.CDODefinition.Name);
      if(target is CDOMapDefinition || target is CDOFieldMapDefinition) {
       int mapID=target is CDOMapDefinition ? ((CDOMapDefinition)target).CDOMapID : ((CDOFieldMapDefinition)target).CDOMapID;
       var map=set.CDOMapDefinitions.Values.FirstOrDefault(x=>x.CDOMapID==mapID);
       if(map!=null) {affected.Add(map.SourceCDOName); affected.Add(map.TargetCDOName);}
      }
      if(target is CLFDefinition || target is CLFFunction || (target is CLFFunParm && ((CLFFunParm)target).ParentCLFFunction!=null)) {
       int clfID=target is CLFDefinition ? ((CLFDefinition)target).CLFID : (target is CLFFunction ? ((CLFFunction)target).CLFID : ((CLFFunParm)target).ParentCLFFunction.CLFID);
       foreach(var ev in set.CLFEventMaps.Values.Where(x=>x.CLFID==clfID)) {
        var cdo=set.CDODefinitions.Values.FirstOrDefault(x=>x.CDODefId==ev.CDODefID); if(cdo!=null) affected.Add(cdo.Name);
       }
      }
     }
     result=Dict("status","saved_to_test_copy","operations",records,"associated_operations",AssociatedOperations.ToArray(),"reloaded_definitions",verified,"reloaded_cdo_count",verify.CDODefinitions.Count,"reload_verified",true,"affected_cdos",affected.ToArray());
    } else throw new ArgumentException("Unknown bridge mode");
   }
   File.WriteAllText(resultFile,Json.Serialize(Dict("ok",true,"result",result)),new System.Text.UTF8Encoding(false));
  } catch(Exception e) { File.WriteAllText(resultFile,Json.Serialize(Dict("ok",false,"error",e.ToString())),new System.Text.UTF8Encoding(false)); }
 }
}

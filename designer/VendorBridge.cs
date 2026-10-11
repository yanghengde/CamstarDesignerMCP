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
  {"sql_type","SQLDataTypes"},{"db_type","DBTypes"},{"storage_category","DBCategories"}
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
  if(obj is CDOField) return new[]{"Name","FieldName","FieldDefId","IsListType","IsNonPersistent"};
  if(obj is FieldDefinition) return new[]{"FieldDefName","PrecisionValue","Scale","CPPDataTypeID"};
  if(obj is CLFFunction) return new[]{"FunctionID","Sequence","CLFID"};
  if(obj is CLFFunParm) return new[]{"ValueExpression"};
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
 static readonly HashSet<string> ReadOnlyKinds=new HashSet<string>(new[]{"workspace","event","feature","data_type","sql_type","db_type","storage_category","query_type","clf_type","label_category"});
 static readonly HashSet<string> Protected=new HashSet<string>(new[]{
  "Metadata","WorkspaceCode","WorkspaceOverride","LowerWorkspaceOverride","OverrideToDelete","ExistsInLowerWorkspace",
  "ModifiedStatus","InheritedID","InheritedField","InheritMask","SequenceNumber","Sequence","CDODefinition","ParentFunctionDefinition",
  "DBTable","ParentCLFDefinition","CDOName","FieldDefId","CPPDataTypeID","CPPDataTypeId","CDODefId","FieldID",
  "ParentQueryDef","ParentCLFFunction","ParentCLFEventMap","ParentCLFDefintion","CPPDataType","CDOType"
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
 static object Apply(MDBMetadataSet set,Dictionary<string,object> op) {
  string action=Text(op,"action"), kind=Text(op,"kind"), name=Text(op,"name"), owner=Text(op,"owner");
  if(action=="patch") {
   if(ReadOnlyKinds.Contains(kind)) throw new ArgumentException("This definition category is read-only");
   var obj=Find(set,kind,name,owner); var changes=Map(op["changes"]); var expected=Map(op["expected"]);
   foreach(var item in changes) {
    if(!expected.ContainsKey(item.Key)) throw new ArgumentException("Expected value required: "+item.Key);
    var p=obj.GetType().GetProperty(item.Key);
    if(p==null || !Editable(p)) throw new ArgumentException("Property cannot be edited: "+item.Key);
    if(new[]{"Name","FieldName","FieldDefName"}.Contains(item.Key) && !String.Equals(Name(obj),Convert.ToString(item.Value),StringComparison.OrdinalIgnoreCase))
     CheckName(set,kind,Convert.ToString(item.Value),owner);
    if(Json.Serialize(Scalar(p.GetValue(obj,null)))!=Json.Serialize(expected[item.Key])) throw new ArgumentException("Expected value conflict: "+item.Key);
   }
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
   if(cdo.WorkspaceCode!=set.WorkSpace) cdo.CreateWorkspaceOverride(set.WorkSpace,ModifiedStatusEnum.Modified);
   var type=(FieldDefinition)Find(set,"field_type",Text(op,"field_type"),"");
   var f=cdo.AddField(type,Flag(op,"is_list"),name,Text(op,"description"),0,0,Flag(op,"persistent"));
   f.IsNonPersistent=!Flag(op,"persistent");
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
   RememberRemoval(obj); obj.SetModifiedStatus(ModifiedStatusEnum.Deleted); return (obj);
  }
  if(action=="change_field_type") {
   var cdo=(CDODefinition)Find(set,"cdo",owner,"");
   var field=(CDOField)Find(set,"field",name,owner);
   if(field.FieldDefinition.FieldDefName!=Text(op,"expected_field_type")) throw new ArgumentException("Field type precondition failed");
   var type=(FieldDefinition)Find(set,"field_type",Text(op,"field_type"),"");
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
   if(mode=="export") {
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
    var set=Load(Text(req,"mdb"),folder,Text(req,"workspace"));
    string kind=Text(req,"kind"),owner=Text(req,"owner"),name=Text(req,"name");
    if(mode=="catalog") {
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
       var references=Values(method.Invoke(obj,new object[]{false}));
       result=Dict("where_used",references.Take(100).Select(Record).ToArray(),"total",references.Count,"truncated",references.Count>100);
      }
     } else {
      var record=Record(obj); if(obj is CDODefinition) {
       var fields=Values(((CDODefinition)obj).AllFields); record["fields"]=fields.Take(100).Select(Record).ToArray();
       record["total_fields"]=fields.Count; record["fields_truncated"]=fields.Count>100;
       record["events"]=Values(((CDODefinition)obj).Events).Select(Record).ToArray();
      }
      if(obj is CDOField) record["events"]=Values(((CDOField)obj).Events).Select(Record).ToArray();
      if(obj is CLFDefinition) record["functions"]=Values(((CLFDefinition)obj).CLFFunctions).Select(x=>{
       var r=Record(x); r["parameters"]=Values(((CLFFunction)x).Parameters).Select(Record).ToArray(); return r;
      }).ToArray();
      if(obj is CLFFunction) record["parameters"]=Values(((CLFFunction)obj).Parameters).Select(Record).ToArray();
      if(obj is QueryDef) { record["query_texts"]=Values(((QueryDef)obj).QueryTexts).Select(Record).ToArray(); record["parameters"]=Values(((QueryDef)obj).Parameters).Select(Record).ToArray(); }
      result=record;
     }
    } else if(mode=="apply") {
     Aliases.Clear(); RemovedIdentities.Clear();
     var targets=new List<BaseMetadataObject>(); var properties=new Dictionary<string,HashSet<string>>();
     foreach(var item in (object[])req["operations"]) {
      var op=Map(item); var target=(BaseMetadataObject)Apply(set,op); targets.Add(target);
      string identity=Identity(target);
      if(!properties.ContainsKey(identity)) properties[identity]=new HashSet<string>(CoreProperties(target));
      if(Text(op,"action")=="patch") foreach(var key in Map(op["changes"]).Keys) properties[identity].Add(key);
     }
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
     result=Dict("status","saved_to_test_copy","operations",records,"reloaded_definitions",verified,"reloaded_cdo_count",verify.CDODefinitions.Count,"reload_verified",true,"affected_cdos",affected.ToArray());
    } else throw new ArgumentException("Unknown bridge mode");
   }
   File.WriteAllText(resultFile,Json.Serialize(Dict("ok",true,"result",result)),new System.Text.UTF8Encoding(false));
  } catch(Exception e) { File.WriteAllText(resultFile,Json.Serialize(Dict("ok",false,"error",e.ToString())),new System.Text.UTF8Encoding(false)); }
 }
}

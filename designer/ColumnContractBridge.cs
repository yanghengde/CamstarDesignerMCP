// Read-only adapter to the installed Update DB generator. Never creates a Processor.
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Web.Script.Serialization;

public static class DesignerColumnContractBridge {
 public static void Run(string assemblyFile, string requestFile, string resultFile) {
  var json = new JavaScriptSerializer { MaxJsonLength = 20 * 1024 * 1024 };
  try {
   var request = json.Deserialize<Dictionary<string, object>>(File.ReadAllText(requestFile));
   var assembly = Assembly.LoadFrom(assemblyFile);
   var tableType = assembly.GetType("CIMS.DBUpdate.DataDictionary.Table", true);
   var columnType = assembly.GetType("CIMS.DBUpdate.DataDictionary.Column", true);
   var generate = columnType.GetMethod("GetSQLDefinitionClause");
   var databaseType = Enum.Parse(generate.GetParameters()[0].ParameterType, "SQLServer");
   var columns = new List<object>();
   foreach (var rawTable in (IEnumerable)request["tables"]) {
    var requested = (Dictionary<string, object>)rawTable;
    var tableName = Convert.ToString(requested["name"]);
    var table = Activator.CreateInstance(tableType, new object[] { tableName, "" });
    if (!(bool)tableType.GetMethod("LoadFromMDB").Invoke(table, new object[] { request["compiled_mdb"] }))
     throw new InvalidOperationException("Vendor table load failed: " + tableName);
    var available = ((IEnumerable)tableType.GetProperty("Columns").GetValue(table, null)).Cast<object>().ToArray();
    foreach (var rawName in (IEnumerable)requested["columns"]) {
     var name = Convert.ToString(rawName);
     var matches = available.Where(c => String.Equals(Convert.ToString(columnType.GetProperty("ColumnName").GetValue(c, null)), name, StringComparison.OrdinalIgnoreCase)).ToArray();
     if (matches.Length != 1) throw new InvalidOperationException("Compiled column missing or ambiguous: " + tableName + "." + name);
     var column = matches[0];
     var clause = Convert.ToString(generate.Invoke(column, new object[] { databaseType }));
     columns.Add(new {
      table = tableName, name = name, sql_definition = clause,
      nullable = (bool)columnType.GetProperty("IsNullable").GetValue(column, null),
      effective_precision = (int)columnType.GetProperty("Precision").GetValue(column, null),
      scale = (int)columnType.GetProperty("Scale").GetValue(column, null),
      usage = Convert.ToString(columnType.GetProperty("Usage").GetValue(column, null))
     });
    }
   }
   File.WriteAllText(resultFile, json.Serialize(new { ok = true, result = new {
    method = "CIMS.DBUpdate.DataDictionary.Table.LoadFromMDB/Column.GetSQLDefinitionClause",
    api_visibility = "internal_version_pinned", assembly = assembly.FullName,
    database_type = "SQLServer", database_connection_opened = false, columns = columns
   }}));
  } catch (Exception error) {
   File.WriteAllText(resultFile, json.Serialize(new { ok = false, error = error.ToString() }));
  }
 }
}

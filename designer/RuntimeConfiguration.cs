using System;
using System.Collections.Generic;
using System.Data;
using System.Data.Common;
using System.Data.OleDb;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using System.Text;

// DoUpdate(loadMetadata:true, loadConfiguration:false) still replaces these
// tables in the installed vendor version. Seed a private SiteInfo copy from
// the live target and explicitly load it, then verify the values survived.
public sealed class RuntimeConfigurationSnapshot {
 public static readonly string[] TableNames = {
  "DBDataSourceNames", "InsiteSiteInfo", "SiteInfoKeyList", "SystemInfo"
 };
 readonly DataSet data;

 public RuntimeConfigurationSnapshot(DataSet source) {
  data = source.Copy();
  foreach(string name in TableNames)
   if(!data.Tables.Contains(name)) throw new InvalidOperationException("Missing runtime configuration table: " + name);
  foreach(string name in new[]{"InsiteSiteInfo", "SystemInfo"})
   if(data.Tables[name].Rows.Count == 0)
    throw new InvalidOperationException("Runtime configuration is empty: " + name + "; restore it in Management Studio before publishing");
  foreach(string key in new[]{"LicenseServer", "LicenseFileType"}) {
   bool found = false;
   foreach(DataRow row in data.Tables["InsiteSiteInfo"].Rows)
    if(String.Equals(Convert.ToString(row["TName"]), key, StringComparison.OrdinalIgnoreCase) &&
       !String.IsNullOrWhiteSpace(Convert.ToString(row["TValue"]))) found = true;
   if(!found) throw new InvalidOperationException("Missing runtime license configuration: " + key);
  }
 }

 static string Quote(string value) { return "[" + value.Replace("]", "]]") + "]"; }

 public static RuntimeConfigurationSnapshot Capture(DbConnection connection, string schema) {
  var source = new DataSet();
  foreach(string name in TableNames) {
   using(var cmd = connection.CreateCommand()) {
    cmd.CommandText = "SELECT * FROM " + Quote(schema) + "." + Quote(name);
    var table = new DataTable(name);
    using(var reader = cmd.ExecuteReader()) table.Load(reader);
    source.Tables.Add(table);
   }
  }
  return new RuntimeConfigurationSnapshot(source);
 }

 static OleDbConnection OpenSiteInfo(string path, bool readOnly) {
  var builder = new OleDbConnectionStringBuilder();
  builder.Provider = "Microsoft.ACE.OLEDB.12.0";
  builder.DataSource = Path.GetFullPath(path);
  if(readOnly) builder["Mode"] = "Read";
  var connection = new OleDbConnection(builder.ConnectionString);
  connection.Open();
  return connection;
 }

 public void StageSiteInfo(string template, string destination) {
  File.Copy(template, destination, false);
  using(var connection = OpenSiteInfo(destination, false))
  using(var transaction = connection.BeginTransaction()) {
   foreach(string name in TableNames) {
    var expected = data.Tables[name];
    using(var adapter = new OleDbDataAdapter("SELECT * FROM " + Quote(name) + " WHERE 1=0", connection))
    using(var builder = new OleDbCommandBuilder(adapter)) {
     adapter.SelectCommand.Transaction = transaction;
     builder.QuotePrefix = "["; builder.QuoteSuffix = "]";
     var schema = new DataTable();
     adapter.Fill(schema);
     if(schema.Columns.Count != expected.Columns.Count)
      throw new InvalidOperationException("SiteInfo columns differ from the live target: " + name);
     foreach(DataColumn column in expected.Columns)
      if(!schema.Columns.Contains(column.ColumnName))
       throw new InvalidOperationException("SiteInfo column is missing: " + name + "." + column.ColumnName);
     using(var clear = connection.CreateCommand()) {
      clear.Transaction = transaction;
      clear.CommandText = "DELETE FROM " + Quote(name);
      clear.ExecuteNonQuery();
     }
     if(expected.Rows.Count == 0) continue;
     using(var insert = builder.GetInsertCommand()) {
      insert.Transaction = transaction;
      foreach(DataRow row in expected.Rows) {
       foreach(OleDbParameter parameter in insert.Parameters)
        parameter.Value = row[parameter.SourceColumn];
       insert.ExecuteNonQuery();
      }
     }
    }
   }
   transaction.Commit();
  }
  // Ensure MDB type conversions did not truncate or otherwise change values.
  using(var connection = OpenSiteInfo(destination, true)) {
   foreach(string name in TableNames) {
    using(var cmd = connection.CreateCommand()) {
     cmd.CommandText = "SELECT * FROM " + Quote(name);
     var actual = new DataTable(name);
     using(var reader = cmd.ExecuteReader()) actual.Load(reader);
     AssertTable(data.Tables[name], actual);
    }
   }
  }
 }

 static string Cell(object value) {
  if(value == null || value == DBNull.Value) return "null";
  if(value is byte[]) return "binary:" + Convert.ToBase64String((byte[])value);
  if(value is DateTime) return "date:" + ((DateTime)value).ToString("O", CultureInfo.InvariantCulture);
  return "value:" + Convert.ToString(value, CultureInfo.InvariantCulture);
 }

 public static string Fingerprint(DataTable table) {
  var columns = new List<string>();
  foreach(DataColumn column in table.Columns) columns.Add(column.ColumnName);
  columns.Sort(StringComparer.Ordinal);
  var rows = new List<string>();
  foreach(DataRow row in table.Rows) {
   var encoded = new StringBuilder();
   foreach(string column in columns) {
    string value = Cell(row[column]);
    encoded.Append(value.Length).Append(':').Append(value);
   }
   rows.Add(encoded.ToString());
  }
  rows.Sort(StringComparer.Ordinal);
  var result = new StringBuilder(String.Join("|", columns.ToArray()));
  foreach(string row in rows) result.Append('\n').Append(row.Length).Append(':').Append(row);
  using(var hash = SHA256.Create())
   return Convert.ToBase64String(hash.ComputeHash(Encoding.UTF8.GetBytes(result.ToString())));
 }

 static void AssertTable(DataTable expected, DataTable actual) {
  if(expected.Rows.Count != actual.Rows.Count || Fingerprint(expected) != Fingerprint(actual))
   throw new InvalidOperationException("Runtime configuration changed during publication: " + expected.TableName);
 }

 public void AssertUnchanged(DbConnection connection, string schema) {
  var current = Capture(connection, schema);
  foreach(string name in TableNames) AssertTable(data.Tables[name], current.data.Tables[name]);
 }

 public Dictionary<string,int> RowCounts() {
  var result = new Dictionary<string,int>();
  foreach(string name in TableNames) result.Add(name, data.Tables[name].Rows.Count);
  return result;
 }
}

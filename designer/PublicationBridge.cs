using System;
using System.Collections.Generic;
using System.IO;
using System.Web.Script.Serialization;
using Camstar.Data;
using CIMS.DBUpdate;

public static class DesignerPublicationBridge {
 public static void Run(string requestFile,string resultFile) {
  var json=new JavaScriptSerializer();
  try {
   var request=json.Deserialize<Dictionary<string,string>>(File.ReadAllText(requestFile));
   var db=Util.CreateDatabase("CamstarDB",false,"CamstarDesignerMCP");
   if(db==null) throw new InvalidOperationException("Vendor database factory unavailable");
   RuntimeConfigurationSnapshot configuration;
   using(var conn=db.CreateConnection()) {
    conn.Open(); using(var cmd=conn.CreateCommand()) {
     cmd.CommandText="SELECT DB_NAME()";
     if(Convert.ToString(cmd.ExecuteScalar())!=request["database"]) throw new InvalidOperationException("Vendor database target mismatch");
    }
    configuration=RuntimeConfigurationSnapshot.Capture(conn,request["schema"]);
   }
   string runtimeSiteInfo=Path.Combine(Path.GetDirectoryName(Path.GetFullPath(requestFile)),"runtime_siteinfo.mdb");
   configuration.StageSiteInfo(request["siteinfo_mdb"],runtimeSiteInfo);
   LogUtils.LogHandlers += (sender,args) => Console.WriteLine(args.Message);
   var processor=new Processor(request["compiled_mdb"],runtimeSiteInfo,"CamstarDB",request["schema"],request["server"],true,true);
   // Processor attaches its Windows event-log handler even when hosted outside
   // CIMS. Keep our subscribed audit handler and avoid machine-wide registration.
   LogUtils.LogHandlers -= EventLogHandler.LogMessage;
   LogUtils.SetLogLevel((LogUtils.LogSeverity)0);
   processor.AlterCommandTimeoutInSecs=300;
   // Explicitly load the live configuration snapshot. Passing false silently
   // emptied configuration tables with the installed Processor version.
   bool ok=processor.DoUpdate(true,true);
   if(!ok) throw new InvalidOperationException("Official Update DB failed: "+processor.ErrorMessage);
   using(var conn=db.CreateConnection()) {
    conn.Open();
    configuration.AssertUnchanged(conn,request["schema"]);
   }
   File.WriteAllText(resultFile,json.Serialize(new {ok=true,result=new {status="vendor_update_completed",configuration_updated=false,
    runtime_configuration_preserved=true,runtime_configuration_rows=configuration.RowCounts()}}));
  } catch(Exception error) {
   File.WriteAllText(resultFile,json.Serialize(new {ok=false,error=error.ToString()}));
  }
 }
}

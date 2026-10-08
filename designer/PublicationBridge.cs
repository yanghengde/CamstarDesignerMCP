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
   using(var conn=db.CreateConnection()) {
    conn.Open(); using(var cmd=conn.CreateCommand()) {
     cmd.CommandText="SELECT DB_NAME()";
     if(Convert.ToString(cmd.ExecuteScalar())!=request["database"]) throw new InvalidOperationException("Vendor database target mismatch");
    }
   }
   LogUtils.LogHandlers += (sender,args) => Console.WriteLine(args.Message);
   var processor=new Processor(request["compiled_mdb"],request["siteinfo_mdb"],"CamstarDB",request["schema"],request["server"],true,true);
   // Processor attaches its Windows event-log handler even when hosted outside
   // CIMS. Keep our subscribed audit handler and avoid machine-wide registration.
   LogUtils.LogHandlers -= EventLogHandler.LogMessage;
   LogUtils.SetLogLevel((LogUtils.LogSeverity)0);
   processor.AlterCommandTimeoutInSecs=300;
   // Update design metadata; leave server/user/site configuration intact.
   bool ok=processor.DoUpdate(true,false);
   if(!ok) throw new InvalidOperationException("Official Update DB failed: "+processor.ErrorMessage);
   File.WriteAllText(resultFile,json.Serialize(new {ok=true,result=new {status="vendor_update_completed",configuration_updated=false}}));
  } catch(Exception error) {
   File.WriteAllText(resultFile,json.Serialize(new {ok=false,error=error.ToString()}));
  }
 }
}

// Isolated .NET Framework adapter. Only generated ObjectStack service types are constructible.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Web.Script.Serialization;

public static class TestWcfClient {
    static Assembly client, foundation;
    static string directory;
    static int outputCount;
    static bool outputTruncated;
    static readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = 4194304, RecursionLimit = 64 };
    static readonly string stack = "Camstar.WCF.ObjectStack.";
    static readonly string services = "Camstar.WCF.Services.";
    static readonly HashSet<string> readMethods = new HashSet<string> { "Load", "GetEnvironment" };

    static PropertyInfo[] Properties(Type type) {
        var properties = new Dictionary<string, PropertyInfo>();
        for (var t = type; t != null && t != typeof(object); t = t.BaseType)
            foreach (var p in t.GetProperties(BindingFlags.Public | BindingFlags.Instance | BindingFlags.DeclaredOnly))
                if (p.GetIndexParameters().Length == 0 && !properties.ContainsKey(p.Name)) properties.Add(p.Name, p);
        return properties.Values.ToArray();
    }
    static PropertyInfo Prop(Type type, string name) { return Properties(type).FirstOrDefault(p => p.Name == name); }
    static Type Find(string name) {
        var type = client.GetType(name) ?? foundation.GetType(name);
        if (type == null) throw new InvalidOperationException("Client type is unavailable: " + name);
        return type;
    }
    static object Get(object value, string name) {
        var p = value == null ? null : Prop(value.GetType(), name);
        return p == null ? null : p.GetValue(value, null);
    }
    static bool Primitive(Type type) { return type.IsGenericType && type.GetGenericTypeDefinition().FullName == stack + "Primitive`1"; }
    static bool Scalar(Type type) { return type.IsPrimitive || type == typeof(string) || type == typeof(decimal) || type == typeof(DateTime) || type == typeof(Guid) || type.IsEnum; }
    static string Outcome(object status) {
        if (status != null && (bool)Get(status, "IsSuccess")) return "success";
        // The facade also converts communication/SOAP faults into ResultStatus.
        var exception = Get(status, "ExceptionData");
        return exception != null && Convert.ToString(Get(exception, "ExceptionLevel")) == "Server" ? "rejected" : "error";
    }
    static string Message(object status) {
        var message = Convert.ToString(Get(status, "Message"));
        if (String.IsNullOrEmpty(message)) message = Convert.ToString(Get(Get(status, "ExceptionData"), "Description"));
        return message;
    }
    static object Schema(Type type, int depth) {
        Type nullable = Nullable.GetUnderlyingType(type);
        if (nullable != null) type = nullable;
        var schema = new Dictionary<string, object> { {"type", type.FullName}, {"nullable", !type.IsValueType || nullable != null} };
        if (Primitive(type)) { schema["kind"] = "primitive"; schema["value_type"] = type.GetGenericArguments()[0].FullName; }
        else if (type.IsArray) { schema["kind"] = "array"; if (depth < 3) schema["item"] = Schema(type.GetElementType(), depth + 1); }
        else if (type.IsEnum) { schema["kind"] = "enum"; schema["values"] = Enum.GetNames(type); }
        else if (Scalar(type)) schema["kind"] = "scalar";
        else {
            schema["kind"] = "object";
            if (depth < 2) schema["fields"] = Properties(type).Where(p => p.CanWrite && p.Name != "IsEmpty").Select(p => new { name = p.Name, schema = Schema(p.PropertyType, depth + 1) }).ToArray();
        }
        return schema;
    }
    static MethodInfo[] Methods(Type service, Type data) {
        return service.GetMethods(BindingFlags.Public | BindingFlags.Instance).Where(m => {
            var p = m.GetParameters();
            return m.ReturnType.FullName == stack + "ResultStatus" && p.Length == 3 && p[0].ParameterType == data && p[1].ParameterType.FullName == services + data.Name + "_Request" && p[2].IsOut;
        }).OrderBy(m => m.Name).ToArray();
    }
    static object ConvertValue(object raw, Type type, int depth) {
        if (depth > 16) throw new InvalidOperationException("Input nesting is too deep");
        if (raw == null) { if (type.IsValueType && Nullable.GetUnderlyingType(type) == null) throw new InvalidOperationException("Null cannot be assigned to " + type.Name); return null; }
        var nullable = Nullable.GetUnderlyingType(type);
        if (nullable != null) return ConvertValue(raw, nullable, depth + 1);
        if (Primitive(type)) return Activator.CreateInstance(type, new object[] { ConvertValue(raw, type.GetGenericArguments()[0], depth + 1) });
        if (type.IsArray) {
            var values = raw as object[];
            if (values == null) throw new InvalidOperationException("Expected an array: " + type.Name);
            var array = Array.CreateInstance(type.GetElementType(), values.Length);
            for (int i = 0; i < values.Length; i++) array.SetValue(ConvertValue(values[i], type.GetElementType(), depth + 1), i);
            return array;
        }
        if (type.IsEnum) {
            if (!(raw is string) || !Enum.IsDefined(type, raw)) throw new InvalidOperationException("Unknown enum value for " + type.Name);
            return Enum.Parse(type, (string)raw);
        }
        if (type == typeof(string)) { if (!(raw is string)) throw new InvalidOperationException("Expected string"); return raw; }
        if (type == typeof(bool)) { if (!(raw is bool)) throw new InvalidOperationException("Expected boolean"); return raw; }
        if (type == typeof(int) || type == typeof(long) || type == typeof(short)) {
            if (!(raw is int) && !(raw is long)) throw new InvalidOperationException("Expected integer");
            return Convert.ChangeType(raw, type, CultureInfo.InvariantCulture);
        }
        if (type == typeof(double) || type == typeof(float) || type == typeof(decimal)) {
            if (!(raw is int) && !(raw is long) && !(raw is decimal) && !(raw is double)) throw new InvalidOperationException("Expected number");
            return Convert.ChangeType(raw, type, CultureInfo.InvariantCulture);
        }
        if (type == typeof(DateTime)) return DateTime.Parse(Convert.ToString(raw), CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind);
        if (type == typeof(Guid)) return Guid.Parse(Convert.ToString(raw));
        if (!type.FullName.StartsWith(stack) || type.Assembly != client && type.Assembly != foundation) throw new InvalidOperationException("Unsupported input type " + type.Name);
        var fields = raw as Dictionary<string, object>;
        if (fields == null) throw new InvalidOperationException("Expected object for " + type.Name);
        object typeName;
        if (fields.TryGetValue("$type", out typeName)) {
            var derived = Find(stack + Convert.ToString(typeName));
            if (!type.IsAssignableFrom(derived) || derived.Assembly != client) throw new InvalidOperationException("Incompatible derived contract");
            type = derived;
        }
        var result = Activator.CreateInstance(type);
        foreach (var field in fields) {
            if (field.Key == "$type") continue;
            var property = Prop(type, field.Key);
            if (property == null || !property.CanWrite) throw new InvalidOperationException("Unknown or readonly field: " + type.Name + "." + field.Key);
            property.SetValue(result, ConvertValue(field.Value, property.PropertyType, depth + 1), null);
        }
        return result;
    }
    static object Output(object value, int depth) {
        if (value == null) return null;
        if (++outputCount > 40000) { outputTruncated = true; return new { truncated = true }; }
        var type = value.GetType();
        if (type == typeof(DateTime)) return ((DateTime)value).ToString("o");
        if (Scalar(type)) return type.IsEnum || type == typeof(Guid) ? value.ToString() : value;
        if (Primitive(type)) return Get(value, "IsEmpty") is bool && (bool)Get(value, "IsEmpty") ? null : Output(Get(value, "Value"), depth + 1);
        if (depth > 16) { outputTruncated = true; return new Dictionary<string, object> { {"truncated", true} }; }
        var list = value as IEnumerable;
        if (list != null) {
            var values = list.Cast<object>().Take(2001).ToArray();
            if (values.Length > 2000) outputTruncated = true;
            return values.Take(2000).Select(v => Output(v, depth + 1)).ToArray();
        }
        var output = new Dictionary<string, object>();
        foreach (var property in Properties(type)) {
            if (!property.CanRead || property.Name == "Password" || property.Name == "SessionID" || property.Name == "Request" || property.Name == "Response" || property.Name == "Key" || property.Name == "IsEmpty") continue;
            var field = property.GetValue(value, null);
            if (field != null) output[property.Name] = Output(field, depth + 1);
        }
        return output;
    }
    static void RequestField(object info, string path) {
        var parts = path.Split('.');
        object current = info;
        for (int i = 0; i < parts.Length; i++) {
            var p = Prop(current.GetType(), parts[i]);
            if (p == null || !p.CanWrite) throw new InvalidOperationException("Unknown return field: " + path);
            var child = Activator.CreateInstance(p.PropertyType);
            var existing = p.GetValue(current, null);
            if (existing != null) child = existing;
            p.SetValue(current, child, null);
            if (i == parts.Length - 1) {
                var requestValue = Prop(child.GetType(), "RequestValue");
                if (requestValue == null) throw new InvalidOperationException("Return field needs a scalar path: " + path);
                requestValue.SetValue(child, true, null);
            }
            current = child;
        }
    }
    static void RequestSelection(object info, int limit) {
        var property = Prop(info.GetType(), "ObjectToChange");
        if (property == null) throw new InvalidOperationException("Service does not expose an ObjectToChange selection");
        var field = Activator.CreateInstance(property.PropertyType);
        Prop(field.GetType(), "RequestSelectionValues").SetValue(field, true, null);
        var selectionProperty = Prop(field.GetType(), "RequestSelectionValuesInfo");
        var selection = Activator.CreateInstance(selectionProperty.PropertyType);
        var optionsProperty = Prop(selection.GetType(), "Options");
        var options = Activator.CreateInstance(optionsProperty.PropertyType);
        Prop(options.GetType(), "RowSetSize").SetValue(options, limit, null);
        Prop(options.GetType(), "StartRow").SetValue(options, 1, null);
        optionsProperty.SetValue(selection, options, null);
        selectionProperty.SetValue(field, selection, null);
        property.SetValue(info, field, null);
    }
    static object Selection(object result, int limit) {
        var selection = Get(Get(Get(result, "Environment"), "ObjectToChange"), "SelectionValues");
        var headers = Get(selection, "Headers") as IEnumerable;
        var rows = Get(selection, "Rows") as IEnumerable;
        return new {
            headers = headers == null ? new string[0] : headers.Cast<object>().Select(h => Convert.ToString(Get(h, "Name"))).ToArray(),
            rows = rows == null ? new object[0] : rows.Cast<object>().Take(limit).Select(r => Output(Get(r, "Values"), 0)).ToArray(),
            limit = limit
        };
    }
    static object Execute(Dictionary<string, object> input) {
        var mode = Convert.ToString(input["mode"]);
        if (mode == "catalog") return new { services = client.GetExportedTypes().Where(t => t.Namespace + "." == services && t.Name.EndsWith("Service") && client.GetType(stack + t.Name.Substring(0, t.Name.Length - 7)) != null).Select(t => t.Name.Substring(0, t.Name.Length - 7)).OrderBy(n => n).ToArray() };
        if (mode == "schema") {
            string name = Convert.ToString(input["service"]);
            var data = Find(stack + name);
            var service = Find(services + name + "Service");
            var requestInfo = Prop(Find(services + name + "_Request"), "Info");
            var changesInfo = requestInfo == null ? null : Prop(requestInfo.PropertyType, "ObjectChanges");
            var returnFields = changesInfo == null ? new string[0] : Properties(changesInfo.PropertyType)
                .Where(p => p.CanWrite && Prop(p.PropertyType, "RequestValue") != null)
                .Select(p => "ObjectChanges." + p.Name).ToArray();
            return new { service = name, fields = Schema(data, 0), return_fields = returnFields, methods = Methods(service, data).Select(m => new { name = m.Name, writes = !readMethods.Contains(m.Name) }).ToArray() };
        }
        var profile = Activator.CreateInstance(Find(stack + "UserProfile"), new object[] { Convert.ToString(input["business_user"]), Convert.ToString(input["password"]) });
        if (input.ContainsKey("server_name") && !String.IsNullOrEmpty(Convert.ToString(input["server_name"]))) Prop(profile.GetType(), "ServerName").SetValue(profile, Convert.ToString(input["server_name"]), null);
        if (mode == "login") {
            var auth = Activator.CreateInstance(Find(services + "AuthenticationService"), new object[] { profile });
            var arguments = new object[] { null };
            var status = auth.GetType().GetMethod("Login").Invoke(auth, arguments);
            return new { outcome = Outcome(status), message = Message(status), login_verified = Outcome(status) == "success", exception = Output(Get(status, "ExceptionData"), 0), ace_status = Convert.ToString(Get(status, "ACEStatus")), ace_message = Convert.ToString(Get(status, "ACEMessage")) };
        }
        if (mode != "run" && mode != "validate") throw new InvalidOperationException("Unknown workbench mode");
        var serviceName = Convert.ToString(input["service"]);
        var dataType = Find(stack + serviceName);
        var serviceType = Find(services + serviceName + "Service");
        var methodName = Convert.ToString(input["method"]);
        int selectionLimit = input.ContainsKey("selection_limit") ? Convert.ToInt32(input["selection_limit"]) : 0;
        if (selectionLimit < 0 || selectionLimit > 5 || selectionLimit > 0 && (methodName != "GetEnvironment" || ((Dictionary<string, object>)input["data"]).Count != 0))
            throw new InvalidOperationException("Selection query requires a readonly GetEnvironment request with at most five rows");
        var method = Methods(serviceType, dataType).SingleOrDefault(m => m.Name == methodName);
        if (method == null) throw new InvalidOperationException("Unsupported service method or client signature");
        bool writes = !readMethods.Contains(methodName);
        if (writes && !(input.ContainsKey("allow_write") && (bool)input["allow_write"])) throw new InvalidOperationException("Write test has not been enabled");
        var dataObject = ConvertValue(input["data"], dataType, 0);
        var request = Activator.CreateInstance(Find(services + serviceName + "_Request"));
        var infoProperty = Prop(request.GetType(), "Info");
        var info = Activator.CreateInstance(infoProperty.PropertyType);
        infoProperty.SetValue(request, info, null);
        foreach (var raw in (object[])input["return_fields"]) RequestField(info, Convert.ToString(raw));
        if (selectionLimit > 0) RequestSelection(info, selectionLimit);
        if (mode == "validate") return new { validated = true, writes = writes };
        if ((bool)input["collect_trace"]) {
            var debug = Activator.CreateInstance(Find(stack + "DebugRequest"));
            Prop(debug.GetType(), "TraceToResponseXML").SetValue(debug, true, null);
            Prop(debug.GetType(), "IncludeTimestamps").SetValue(debug, true, null);
            Prop(profile.GetType(), "DebugRequest").SetValue(profile, debug, null);
        }
        var instance = Activator.CreateInstance(serviceType, new object[] { profile });
        var args = new object[] { dataObject, request, null };
        var resultStatus = method.Invoke(instance, args);
        return new { outcome = Outcome(resultStatus), message = Message(resultStatus), exception = Output(Get(resultStatus, "ExceptionData"), 0), value = Output(Get(args[2], "Value"), 0), selection = selectionLimit > 0 ? Selection(args[2], selectionLimit) : null, trace = Output(Get(Get(resultStatus, "DebugResponse"), "Trace"), 0), output_truncated = outputTruncated, writes = writes };
    }
    public static int Main() {
        try {
            directory = Environment.GetEnvironmentVariable("CAMSTAR_TEST_CLIENT_DIRECTORY");
            AppDomain.CurrentDomain.AssemblyResolve += (sender, args) => {
                var file = Path.Combine(directory, new AssemblyName(args.Name).Name + ".dll");
                return File.Exists(file) ? Assembly.LoadFrom(file) : null;
            };
            foundation = Assembly.LoadFrom(Path.Combine(directory, "Camstar.WCFClientBase.dll"));
            client = Assembly.LoadFrom(Path.Combine(directory, "Camstar.WCFClient.dll"));
            Console.InputEncoding = System.Text.Encoding.UTF8;
            Console.OutputEncoding = new System.Text.UTF8Encoding(false);
            var input = (Dictionary<string, object>)json.DeserializeObject(Console.In.ReadToEnd());
            Console.WriteLine(json.Serialize(new { ok = true, result = Execute(input) }));
            return 0;
        } catch (Exception error) {
            while (error.InnerException != null) error = error.InnerException;
            Console.WriteLine(json.Serialize(new { ok = false, error = error.GetType().Name + ": " + error.Message }));
            return 1;
        }
    }
}

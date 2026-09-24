//+------------------------------------------------------------------+
//|                                        ChartObjectMonitor.mq5    |
//|                       Trading Assistant - MT5 chart object watcher|
//|                                                                  |
//| Scans all objects on the current chart every tick. For objects    |
//| drawn in InpMonitorColor (OBJ_HLINE, OBJ_TREND, OBJ_RECTANGLE) it |
//| detects when price CROSSES or TOUCHES the level, then POSTs a     |
//| JSON payload to the Trading Assistant Flask webhook.              |
//|                                                                  |
//| IMPORTANT: You must whitelist InpWebhookURL in                     |
//|   Tools -> Options -> Expert Advisors -> Allow WebRequest for URL  |
//| or WebRequest() will fail with error 4060.                        |
//+------------------------------------------------------------------+
#property copyright "Trading Assistant"
#property link      "https://github.com/tex-node/chartasst"
#property version   "1.00"

//--- Inputs ----------------------------------------------------------
input string InpWebhookURL   = "http://127.0.0.1:5000/webhook/mt5"; // Webhook URL
input string InpAPIKey       = "";            // X-API-Key shared secret
input color  InpMonitorColor = clrRed;        // Only monitor objects of this color
input bool   InpAlertCross   = true;          // Alert on price crossing a level
input bool   InpAlertTouch   = true;          // Alert on price touching a level
input int    InpCooldownSec  = 30;            // Per-object cooldown between alerts (sec)
input int    InpTouchPoints  = 10;            // Touch tolerance in points
input int    InpTimeoutMs    = 5000;          // WebRequest timeout (ms)

//--- Per-object tracking state ---------------------------------------
string   g_names[];       // object name
int      g_side[];        // -1 = below, 0 = inside/unknown, +1 = above
datetime g_lastAlert[];   // last alert time (for cooldown)

//+------------------------------------------------------------------+
//| Expert initialisation                                            |
//+------------------------------------------------------------------+
int OnInit()
{
   Print("======================================================");
   Print("ChartObjectMonitor initialised.");
   Print("  Webhook URL   : ", InpWebhookURL);
   Print("  Monitor color : ", InpMonitorColor, " (only objects of this color)");
   Print("  Alert cross   : ", InpAlertCross);
   Print("  Alert touch   : ", InpAlertTouch);
   Print("  Cooldown (sec): ", InpCooldownSec);
   Print("  Touch points  : ", InpTouchPoints);
   Print("  Symbol/TF     : ", _Symbol, " / ", EnumToString((ENUM_TIMEFRAMES)_Period));
   Print("  API key set   : ", (StringLen(InpAPIKey) > 0 ? "yes" : "NO - set InpAPIKey!"));
   Print("------------------------------------------------------");
   Print("REMINDER: whitelist the URL in Tools -> Options ->");
   Print("Expert Advisors -> Allow WebRequest for listed URL.");
   Print("======================================================");

   if(StringLen(InpAPIKey) == 0)
      Print("WARNING: InpAPIKey is empty. The server will reject requests with HTTP 401.");

   ArrayResize(g_names, 0);
   ArrayResize(g_side, 0);
   ArrayResize(g_lastAlert, 0);
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialisation                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   Print("ChartObjectMonitor stopping. Reason code: ", reason);
}

//+------------------------------------------------------------------+
//| Main tick handler - scan every object on the chart               |
//+------------------------------------------------------------------+
void OnTick()
{
   double price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(price <= 0.0)
      return;

   int total = ObjectsTotal(0, -1, -1);
   for(int i = total - 1; i >= 0; i--)
   {
      string name = ObjectName(0, i);
      if(StringLen(name) == 0)
         continue;

      // Only consider objects drawn in the monitored color.
      color objColor = (color)ObjectGetInteger(0, name, OBJPROP_COLOR);
      if(objColor != InpMonitorColor)
         continue;

      ENUM_OBJECT type = (ENUM_OBJECT)ObjectGetInteger(0, name, OBJPROP_TYPE);
      if(type != OBJ_HLINE && type != OBJ_TREND && type != OBJ_RECTANGLE)
         continue;

      EvaluateObject(name, type, price);
   }
}

//+------------------------------------------------------------------+
//| Look up (or create) the tracking slot for an object name         |
//+------------------------------------------------------------------+
int FindSlot(const string name)
{
   int n = ArraySize(g_names);
   for(int i = 0; i < n; i++)
      if(g_names[i] == name)
         return i;

   int idx = n;
   ArrayResize(g_names, idx + 1);
   ArrayResize(g_side, idx + 1);
   ArrayResize(g_lastAlert, idx + 1);
   g_names[idx]     = name;
   g_side[idx]      = 0;
   g_lastAlert[idx] = 0;
   return idx;
}

//+------------------------------------------------------------------+
//| Compute the price level(s) of an object at the current bar time  |
//| Returns false if the object cannot be resolved.                  |
//+------------------------------------------------------------------+
bool GetObjectLevels(const string name, const ENUM_OBJECT type,
                     double &upper, double &lower)
{
   if(type == OBJ_HLINE)
   {
      double lvl = ObjectGetDouble(0, name, OBJPROP_PRICE, 0);
      upper = lvl;
      lower = lvl;
      return true;
   }

   if(type == OBJ_TREND)
   {
      datetime t0 = (datetime)ObjectGetInteger(0, name, OBJPROP_TIME, 0);
      datetime t1 = (datetime)ObjectGetInteger(0, name, OBJPROP_TIME, 1);
      double   p0 = ObjectGetDouble(0, name, OBJPROP_PRICE, 0);
      double   p1 = ObjectGetDouble(0, name, OBJPROP_PRICE, 1);
      datetime now = TimeCurrent();
      double lvl;
      if(t1 == t0)
         lvl = p1;
      else
         lvl = p0 + (p1 - p0) * ((double)(now - t0) / (double)(t1 - t0));
      upper = lvl;
      lower = lvl;
      return true;
   }

   if(type == OBJ_RECTANGLE)
   {
      double p0 = ObjectGetDouble(0, name, OBJPROP_PRICE, 0);
      double p1 = ObjectGetDouble(0, name, OBJPROP_PRICE, 1);
      upper = MathMax(p0, p1);
      lower = MathMin(p0, p1);
      return true;
   }

   return false;
}

//+------------------------------------------------------------------+
//| Convert the object type to the string the server expects         |
//+------------------------------------------------------------------+
string ObjectTypeString(const ENUM_OBJECT type)
{
   if(type == OBJ_HLINE)     return "horizontal_line";
   if(type == OBJ_TREND)     return "trendline";
   if(type == OBJ_RECTANGLE) return "rectangle";
   return "unknown";
}

//+------------------------------------------------------------------+
//| Evaluate a single object for cross / touch events                |
//+------------------------------------------------------------------+
void EvaluateObject(const string name, const ENUM_OBJECT type, const double price)
{
   double upper, lower;
   if(!GetObjectLevels(name, type, upper, lower))
      return;

   int slot = FindSlot(name);
   double tolerance = InpTouchPoints * _Point;

   // ----- Determine current side relative to the object. -----
   // For a rectangle, "inside" is sign 0; a cross is a move from
   // inside/equal to clearly outside (breakout).
   int newSide = 0;
   if(price > upper)
      newSide = 1;
   else if(price < lower)
      newSide = -1;

   int prevSide = g_side[slot];

   string event  = "";
   string dir    = "";
   double refLvl = 0.0;

   // ----- Cross detection -----
   if(InpAlertCross)
   {
      bool crossedUp = (prevSide <= 0 && newSide == 1);
      bool crossedDn = (prevSide >= 0 && newSide == -1);
      if(crossedUp)
      {
         event  = "cross";
         dir    = "above";
         refLvl = upper;
      }
      else if(crossedDn)
      {
         event  = "cross";
         dir    = "below";
         refLvl = lower;
      }
   }

   // ----- Touch detection (only if no cross fired, to avoid duplicates) -----
   if(event == "")
   {
      if(InpAlertTouch)
      {
         if(type == OBJ_RECTANGLE)
         {
            if(MathAbs(price - upper) <= tolerance)
            {
               event = "touch"; dir = "above"; refLvl = upper;
            }
            else if(MathAbs(price - lower) <= tolerance)
            {
               event = "touch"; dir = "below"; refLvl = lower;
            }
         }
         else if(MathAbs(price - upper) <= tolerance)
         {
            event = "touch";
            dir    = (price >= upper ? "above" : "below");
            refLvl = upper;
         }
      }
   }

   // Always update the tracked side, even when no alert fires.
   g_side[slot] = newSide;

   if(event == "")
      return;

   // ----- Cooldown -----
   datetime now = TimeCurrent();
   if(InpCooldownSec > 0 && (now - g_lastAlert[slot]) < InpCooldownSec)
      return;
   g_lastAlert[slot] = now;

   SendAlert(event, name, type, refLvl, dir);
}

//+------------------------------------------------------------------+
//| Escape a string for safe embedding in JSON                       |
//+------------------------------------------------------------------+
string JsonEscape(const string s)
{
   string out = "";
   int len = StringLen(s);
   for(int i = 0; i < len; i++)
   {
      ushort c = StringGetCharacter(s, i);
      if(c == '"' || c == '\\')
         out += "\\" + ShortToString(c);
      else if(c == '\n')
         out += "\\n";
      else if(c == '\r')
         out += "\\r";
      else if(c == '\t')
         out += "\\t";
      else
         out += ShortToString(c);
   }
   return out;
}

//+------------------------------------------------------------------+
//| Build and POST the JSON payload to the webhook                   |
//+------------------------------------------------------------------+
void SendAlert(const string event, const string name, const ENUM_OBJECT type,
               const double price, const string direction)
{
   string json = StringFormat(
      "{\"event\":\"%s\",\"object_name\":\"%s\",\"object_type\":\"%s\","
      "\"price\":%.8f,\"direction\":\"%s\",\"symbol\":\"%s\","
      "\"timeframe\":\"%s\"}",
      event,
      JsonEscape(name),
      ObjectTypeString(type),
      price,
      direction,
      JsonEscape(_Symbol),
      EnumToString((ENUM_TIMEFRAMES)_Period));

   Print("Sending alert: ", json);

   // String -> UTF-8 char array (drop the trailing null terminator).
   char post[];
   StringToCharArray(json, post, 0, WHOLE_ARRAY, CP_UTF8);
   int postLen = ArraySize(post);
   if(postLen > 0 && post[postLen - 1] == 0)
      ArrayResize(post, postLen - 1);

   string headers = "Content-Type: application/json\r\nX-API-Key: " + InpAPIKey + "\r\n";
   char   result[];
   string resultHeaders;

   ResetLastError();
   int code = WebRequest("POST", InpWebhookURL, headers, InpTimeoutMs,
                         post, result, resultHeaders);

   if(code == -1)
   {
      int err = GetLastError();
      Print("ERROR: WebRequest failed. LastError=", err,
            ". If 4060, add '", InpWebhookURL, "' to Tools -> Options -> ",
            "Expert Advisors -> Allow WebRequest for listed URL.");
      return;
   }

   string body = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
   if(code >= 200 && code < 300)
      Print("Webhook delivered (HTTP ", code, "): ", body);
   else
      Print("WARNING: Webhook returned HTTP ", code, " body=", body,
            " (401 = wrong InpAPIKey, 400 = bad payload).");
}
//+------------------------------------------------------------------+

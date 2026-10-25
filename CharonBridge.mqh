//+------------------------------------------------------------------+
//|                                                  CharonBridge.mqh |
//|  Optional file-based bridge between the EA and a local Python     |
//|  dashboard.                                                       |
//|                                                                  |
//|  DIRECTION OF TRUST                                               |
//|    EA  -> Python : JSON snapshot, written once per timer tick.    |
//|                    Python only ever READS this.                   |
//|    Python -> EA  : flat JSON command, validated before use.       |
//|                    This is the untrusted direction, so the        |
//|                    parser is strict and the verb list is fixed.   |
//|                                                                  |
//|  WHAT A COMMAND MAY NEVER DO                                      |
//|    There is no verb that opens, closes, sizes or modifies an      |
//|    order. CharonTrade.mqh remains the only module that calls      |
//|    OrderSend(), so the bridge cannot become a second order path.  |
//|    The permitted verbs only nudge state the human could already   |
//|    change from the keyboard: arm / disarm the MANUAL pyramid      |
//|    trigger, and select the trailing mode.                         |
//|                                                                  |
//|  TRANSPORT                                                        |
//|    %APPDATA%\MetaQuotes\Terminal\Common\Files\Charon\             |
//|      state_<SYMBOL>.json        EA writes, Python reads           |
//|      commands\*.json            Python writes, EA consumes        |
//|      commands\processed\*.json  audit trail of consumed commands  |
//|                                                                  |
//|  FILE_COMMON is used because it is shared by every terminal on    |
//|  the machine and needs no WebRequest URL allow-listing.           |
//|  The EA remains fully functional when the bridge is switched off: |
//|  nothing here is required for trading, risk or stops.             |
//+------------------------------------------------------------------+
#property copyright "Charon"
#property version   "1.04"

#ifndef __CHARON_BRIDGE__
#define __CHARON_BRIDGE__

#include "CharonDefines.mqh"
#include "CharonMath.mqh"

//--- schema revision understood by both sides of the bridge
//---   2 = v1.1.0 attended execution: order verbs carry block geometry and
//---       the snapshot carries an `order` verdict block. A snapshot or
//---       command written at a different revision is refused outright,
//---       which is what keeps a half-upgraded install from acting on a
//---       ticket it only partially understands.
#define CHARON_BRIDGE_SCHEMA      3
#define CHARON_BRIDGE_ROOT        "Charon"
#define CHARON_BRIDGE_CMD_DIR     "Charon\\commands"    //--- legacy, read by pre-1.3.0 EAs
#define CHARON_BRIDGE_DONE_DIR    "Charon\\commands\\processed"

//--- a command older or newer than this many seconds is refused, which
//--- makes a replayed or clock-skewed file a no-op instead of a surprise
#define CHARON_BRIDGE_CMD_MAX_AGE 60

//--- legs published per snapshot; the panel never needs more than this
#define CHARON_BRIDGE_MAX_LEGS    16

//--- permitted command verbs.
//---  The first four are state-only. The last three are attended manual
//---  execution: they carry an operator's *intent* and are still adjudicated
//---  by CCharonRisk and the block geometry inside the EA. Nothing here lets
//---  the caller choose a volume, a stop distance or an R multiple.
#define CHARON_BRIDGE_V_ARM       "arm"
#define CHARON_BRIDGE_V_DISARM    "disarm"
#define CHARON_BRIDGE_V_TRAILMODE "trail_mode"
#define CHARON_BRIDGE_V_PING      "ping"
#define CHARON_BRIDGE_V_ORDER_OPEN   "order_open"
#define CHARON_BRIDGE_V_ORDER_MODIFY "order_modify"
#define CHARON_BRIDGE_V_ORDER_CANCEL "order_cancel"

//--- highest RR rung a ticket may reference. The ladder publishes three
//--- levels (InpRrLadderA/B/C), so rung 0 means "no rung" and 1..3 index
//--- them. Must match MAX_RUNG in bridge/charon_bridge/schema.py.
#define CHARON_BRIDGE_MAX_RUNG    3

//--- pyramid stages a single ticket may define. Tier 1 is the primary leg,
//--- so three stages reach CHARON_MAX_TIERS (4) exactly.
//--- Must match MAX_STAGES in bridge/charon_bridge/schema.py.
#define CHARON_BRIDGE_MAX_STAGES  3

//--- how a ticket's pyramid stage decides it may fire. Sent as text so the
//--- JSON stays readable; must match TRIGGER_KINDS in schema.py.
#define CHARON_BRIDGE_TRIG_RR     "rr"
#define CHARON_BRIDGE_TRIG_PRICE  "price"

//--- longest journal tag copied into a broker comment. The full note lives
//--- in the bridge's journal sidecar, not on the chart: a comment is size
//--- limited and this EA only ever renders, never reconstructs, the note.
#define CHARON_BRIDGE_TAG_MAX     24

//+------------------------------------------------------------------+
//| One published leg                                                |
//|  Prices and the ladder arrive pre-formatted from the engine so    |
//|  the dashboard renders exactly what the chart renders.            |
//+------------------------------------------------------------------+
struct SCharonBridgeLeg
  {
   ulong             ticket;
   long              type;            // POSITION_TYPE_BUY / SELL
   int               tier;            // 1 = primary, 2..4 = scale-ins
   double            entry;
   double            lot;
   double            currentSL;
   double            riskPrice;       // 1R in price terms
   double            hwm;
   double            progressRR;      // R multiple reached
   bool              trailActive;
   bool              beDone;
   bool              halfRiskDone;
   string            ladder;          // EA-rendered rung text
  };

//+------------------------------------------------------------------+
//| One pyramid stage as a ticket stated it                            |
//|  triggerKind says how trigger is read: an RR multiple of the       |
//|  primary leg's 1R, or an absolute price. Either way lotsMult        |
//|  multiplies the primary's SOLVED volume -- it is never a volume,    |
//|  so a stage cannot smuggle a lot value past the risk model.         |
//+------------------------------------------------------------------+
struct SCharonBridgeStage
  {
   int               kind;            // CHARON_STAGE_*
   double            trigger;         // R multiple, or a price
   double            lotsMult;
  };

//+------------------------------------------------------------------+
//| One working pending order                                        |
//|  A pending order is not a position, so it never appears in legs;  |
//|  without this the dashboard cannot show -- or cancel -- a LIMIT    |
//|  ticket that has been accepted but not yet filled.                 |
//+------------------------------------------------------------------+
struct SCharonBridgePending
  {
   ulong             ticket;
   long              type;            // POSITION_TYPE_BUY (a BUY limit) / SELL
   long              orderType;       // ORDER_TYPE_* as the terminal reports it
   double            price;           // the working price
   double            sl;
   double            lot;
   datetime          placedAt;
  };

//+------------------------------------------------------------------+
//| A validated command extracted from the inbox                     |
//+------------------------------------------------------------------+
struct SCharonBridgeCommand
  {
   bool              valid;
   string            id;
   string            verb;
   string            arg;
   long              issued;
   string            reject;          // non-empty when the file was refused

   //--- order ticket payload. Only meaningful for the order_* verbs; the
   //--- parser leaves them zeroed for the state-only verbs, and
   //--- ApplyBridgeCommand re-validates every one of them before use.
   string            side;            // "BUY" / "SELL"
   string            orderType;       // "MARKET" / "LIMIT"
   double            entry;           // 0 => fill at market
   double            blockTop;
   double            blockBottom;
   double            riskPct;         // 0 when riskMoney is the stated figure
   double            riskMoney;       // 0 => risk is expressed as a percentage
   double            basketCeilingPct;// 0 => no ticket-level basket ceiling
   int               trailTrigger;    // CHARON_TRAIL_*, applied on fill
   double            trailStartRR;    // 0 => use InpTrailStartRR
   double            trailStartPoints;// 0 => use InpTrailStartPoints
   int               rung;            // RR rung the ticket is stated against
   ulong             ticket;          // target of order_modify / order_cancel
   string            journalTag;      // short ASCII tag for the broker comment
   int               stageCount;
   SCharonBridgeStage stages[CHARON_BRIDGE_MAX_STAGES];
  };


//+------------------------------------------------------------------+
//| Everything the dashboard may display                             |
//|  Assembled by the engine from data it already holds; the bridge   |
//|  never recomputes a risk figure or an RR rung.                    |
//+------------------------------------------------------------------+
struct SCharonBridgeSnapshot
  {
   bool              ready;
   string            symbol;
   datetime          serverTime;

   //--- chart-side settings the panel mirrors
   bool              dashboardOn;
   bool              persistOn;

   //--- basket
   bool              basketValid;
   bool              basketMixed;
   long              basketDir;
   int               basketLegs;
   double            basketLot;
   double            basketAvgEntry;
   double            basketOpenPL;
   double            basketRiskMoney;
   double            basketRiskPct;
   double            basketBest;
   string            basketLadder;    // EA-rendered rung text

   //--- risk model in force
   string            slText;
   string            tpText;
   string            sizingText;
   double            maxRiskPct;

   //--- trailing / pyramid
   int               trailMode;
   string            trailText;
   bool              pyramidEnabled;
   int               pyramidMaxLegs;
   double            pyramidMaxRiskPct;
   int               pyramidTrigger;  // 0 = AUTO, 1 = MANUAL
   int               pyramidArmKey;
   bool              pyramidArmed;
   long              pyramidSecsLeft; // -1 = no expiry
   int               nextTier;
   double            progressRR;      // basket progress toward nextTier

   //--- order verdict: what the EA decided about the last ticket
   string            orderStatus;    // idle / pending / accepted / rejected
   string            orderReason;
   string            orderSide;
   double            orderLots;
   double            orderEntry;
   double            orderStop;
   double            orderRiskMoney;
   double            orderRiskPct;
   int               orderRung;
   ulong             orderTicket;
   //--- the short ASCII tag the winning ticket carried, so the dashboard can
   //--- show which note is attached without the EA ever holding the note.
   string            orderJournalTag;

   //--- account
   string            currency;
   double            balance;
   double            equity;
   int               digits;

   //--- legs (flat array; the writer streams it, no nesting needed)
   int               legCount;
   int               pendingCount;
  };

//+------------------------------------------------------------------+
//| CCharonBridge                                                    |
//+------------------------------------------------------------------+
class CCharonBridge
  {
private:
   string            m_symbol;
   ulong             m_seq;
   bool              m_lastOk;
   string            m_lastNote;
   string            m_stateFile;
   string            m_cmdDir;      //--- Charon\commands\<SYMBOL>
   string            m_doneDir;     //--- ...\processed

   bool              WriteStateFile(const string payload);
   bool              ReadCommandFile(const string name, SCharonBridgeCommand &cmd);
   bool              Retire(const string name, const SCharonBridgeCommand &cmd);
   string            NextPending(void);

public:
                     CCharonBridge(void);
   void              Init(const string symbol);

   bool              Enabled(void) const { return (m_symbol != ""); }
   string            Symbol(void) const { return m_symbol; }
   string            CommandDir(void) const { return m_cmdDir; }
   string            Note(void) const { return m_lastNote; }
   ulong             Sequence(void) const { return m_seq; }

   //--- EA -> Python. legs may be NULL when legCount is 0. MQL5 will not take
   //--- a reference array with a default value, so the leg-only form is an
   //--- explicit overload rather than a two-argument call.
   bool              Publish(const SCharonBridgeSnapshot &snap,
                             const SCharonBridgeLeg &legs[], const int legCount);
   bool              Publish(const SCharonBridgeSnapshot &snap,
                             const SCharonBridgeLeg &legs[], const int legCount,
                             const SCharonBridgePending &pendings[],
                             const int pendingCount);

   //--- Python -> EA. Returns at most one command per call so a burst of
   //--- files can never stall the trade pipeline.
   bool              Poll(SCharonBridgeCommand &cmd);

   //--- static helpers, exposed so the self-test can cover them
   static string     JsonEscape(const string text);
   static string     SafeName(const string text);
   static bool       ReadJsonString(const string json, const string key, string &out);
   static bool       ReadJsonNumber(const string json, const string key, double &out);
   static bool       VerbAllowed(const string verb);
   static bool       OrderVerb(const string verb);
   static bool       TrailModeValid(const int mode);
 //--- v1.4.0 ticket helpers. Static and public so CharonSelfTest can cover
 //--- them without a broker.
 static bool       TagIsAscii(const string text);
 static bool       ReadStages(const string json, int &count,
                              SCharonBridgeStage &stages[], string &err);
 static bool       StagesAdvance(const SCharonBridgeStage &stages[], const int count,
                                 const bool isLong, const double top, const double bottom);
  };

//+------------------------------------------------------------------+
//| Make a symbol safe to embed in a file or folder name             |
//|                                                                  |
//|  Mirrors paths.sanitize_symbol() in the Python bridge **exactly**. |
//|  The two processes address the same folders, so a disagreement    |
//|  here does not produce an error -- it produces two different      |
//|  folders, and commands that are written to one and read from the  |
//|  other. bridge/tests/test_symbol_names.py pins the two together    |
//|  against a shared fixture; change one and that test fails.        |
//|                                                                  |
//|  Rule: keep [A-Za-z0-9._-], replace every other character with    |
//|  '_', then trim leading and trailing '.', '_' and '-'. An empty   |
//|  result becomes "UNKNOWN" so the path is never a bare separator.  |
//+------------------------------------------------------------------+
string CCharonBridge::SafeName(const string text)
  {
   string cleaned = "";
   int length = StringLen(text);
   for(int i = 0; i < length; i++)
     {
      ushort ch = StringGetCharacter(text, i);
      bool keep = ((ch >= '0' && ch <= '9') ||
                   (ch >= 'A' && ch <= 'Z') ||
                   (ch >= 'a' && ch <= 'z') ||
                   ch == '.' || ch == '_' || ch == '-');
      cleaned += (keep ? ShortToString(ch) : "_");
     }

   //--- trim from both ends, the way Python's strip("._-") does
   int left = 0, right = StringLen(cleaned) - 1;
   while(left <= right)
     {
      ushort ch = StringGetCharacter(cleaned, left);
      if(ch == '.' || ch == '_' || ch == '-') left++;
      else break;
     }
   while(right >= left)
     {
      ushort ch = StringGetCharacter(cleaned, right);
      if(ch == '.' || ch == '_' || ch == '-') right--;
      else break;
     }

   if(right < left)
      return "UNKNOWN";
   return StringSubstr(cleaned, left, right - left + 1);
  }

//+------------------------------------------------------------------+
CCharonBridge::CCharonBridge(void)
  {
   m_symbol    = "";
   m_seq       = 0;
   m_lastOk    = false;
   m_lastNote  = "";
   m_stateFile = "";
   m_cmdDir    = "";
   m_doneDir   = "";
  }

//+------------------------------------------------------------------+
void CCharonBridge::Init(const string symbol)
  {
   m_symbol    = symbol;
   m_seq       = 0;
   m_lastOk    = false;
   m_lastNote  = "";

   //--- v1.3.0: every chart owns its own inbox. Before this, two charts on
   //--- one terminal drained the same folder and each read the other's
   //--- commands: the symbol field stopped them *applying* it, but they had
   //--- already retired it into processed/ -- so the rightful chart never
   //--- saw it again. One folder per symbol removes the race entirely.
   //---
   //--- The bridge detects this folder to decide where to write, so it must
   //--- be created here and nowhere else. If Python made it, the probe would
   //--- always succeed and commands for an older EA would be routed into a
   //--- folder nothing reads.
   //---
   //--- An empty symbol means the bridge is disabled (CharonEngine passes ""
   //--- for InpBridgeEnable=false). That path must stay inert: creating
   //--- commands\UNKNOWN on every chart that opted out would be a new side
   //--- effect on a setting that promises none.
   if(symbol == "")
     {
      m_cmdDir    = "";
      m_doneDir   = "";
      m_stateFile = "";
      return;
     }

   string safe = SafeName(symbol);
   m_cmdDir    = "Charon\\commands\\" + safe;
   m_doneDir   = m_cmdDir + "\\processed";
   m_stateFile = "Charon\\state_" + safe + ".json";

   FolderCreate(m_cmdDir, FILE_COMMON);
   FolderCreate(m_doneDir, FILE_COMMON);
  }

//+------------------------------------------------------------------+
//| JSON string escaping                                             |
//|                                                                  |
//|  Every string that reaches the dashboard passes through here, so  |
//|  a broker comment or symbol name can never break the document.    |
//|                                                                  |
//|  Anything outside printable ASCII becomes a \uXXXX escape. The EA  |
//|  writes the file as FILE_ANSI while Python reads it as UTF-8, and  |
//|  those two agree only on ASCII -- so escaping every high byte is   |
//|  what keeps a non-ASCII symbol or currency from being mangled on   |
//|  the way across.                                                  |
//+------------------------------------------------------------------+
string CCharonBridge::JsonEscape(const string text)
  {
   string out = "";
   int len = StringLen(text);
   for(int i = 0; i < len; i++)
     {
      ushort c = StringGetCharacter(text, i);
      if(c == 34)              out += "\\\"";
      else if(c == 92)         out += "\\\\";
      else if(c == 10)         out += "\\n";
      else if(c == 13)         out += "\\r";
      else if(c == 9)          out += "\\t";
      else if(c < 32 || c > 126) out += StringFormat("\\u%04x", (int)c);
      else                     out += ShortToString(c);
     }
   return out;
  }

//+------------------------------------------------------------------+
//| Read a quoted string field from a flat JSON object               |
//|                                                                  |
//|  Deliberately minimal: the command files are produced by our own  |
//|  Python bridge and are flat by contract, so a full parser would   |
//|  be attack surface without adding reach. Anything unexpected is   |
//|  reported as "not found" and the caller rejects the command.      |
//+------------------------------------------------------------------+
bool CCharonBridge::ReadJsonString(const string json, const string key, string &out)
  {
   out = "";
   string needle = "\"" + key + "\"";
   int at = StringFind(json, needle);
   if(at < 0)
      return false;

   int len = StringLen(json);
   at += StringLen(needle);

   //--- skip whitespace up to the opening quote of the value
   while(at < len)
     {
      ushort c = StringGetCharacter(json, at);
      if(c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == ':')
        {
         at++;
         continue;
        }
      break;
     }
   if(at >= len || StringGetCharacter(json, at) != '"')
      return false;

   at++;
   string acc = "";
   while(at < len)
     {
      ushort c = StringGetCharacter(json, at);
      if(c == '\\')
        {
         at++;
         if(at >= len)
            return false;
         ushort e = StringGetCharacter(json, at);
         if(e == 'n')       acc += "\n";
         else if(e == 't')  acc += "\t";
         else if(e == 'r')  acc += "\r";
         else if(e == '"')  acc += "\"";
         else if(e == '\\') acc += "\\";
         else if(e == 'b')  acc += ShortToString(8);     // JSON backspace
         else if(e == 'f')  acc += ShortToString(12);    // JSON form feed
         else if(e == 'u')
           {
            //--- a JSON unicode escape: four hex digits after the 'u'.
            //--- Without this branch the digits were copied verbatim, so a
            //--- note containing an accented character arrived mangled
            //--- ("u00e9" instead of the character) -- damage that looked
            //--- like the operator's typo rather than a parser bug.
            //--- All four digits must parse, or the whole string is
            //--- refused: guessing a partial escape would invent text.
            if(at + 4 >= len)
               return false;
            uint code = 0;
            bool ok = true;
            for(int d = 1; d <= 4; d++)
              {
               ushort h = StringGetCharacter(json, at + d);
               uint nibble = 0;
               if(h >= '0' && h <= '9')      nibble = (uint)(h - '0');
               else if(h >= 'a' && h <= 'f') nibble = (uint)(h - 'a' + 10);
               else if(h >= 'A' && h <= 'F') nibble = (uint)(h - 'A' + 10);
               else { ok = false; break; }
               code = (code << 4) | nibble;
              }
            if(!ok)
               return false;
            acc += ShortToString((ushort)(code & 0xFFFF));
            at += 4;
           }
         else               acc += ShortToString(e);

         at++;
         continue;
        }
      if(c == '"')
        {
         out = acc;
         return true;
        }
      acc += ShortToString(c);
      at++;
     }
   return false;
  }

//+------------------------------------------------------------------+
//| Read a numeric field from a flat JSON object                     |
//+------------------------------------------------------------------+
bool CCharonBridge::ReadJsonNumber(const string json, const string key, double &out)
  {
   out = 0.0;
   string needle = "\"" + key + "\"";
   int at = StringFind(json, needle);
   if(at < 0)
      return false;

   int len = StringLen(json);
   at += StringLen(needle);
   while(at < len)
     {
      ushort c = StringGetCharacter(json, at);
      if(c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == ':')
        {
         at++;
         continue;
        }
      break;
     }
   if(at >= len)
      return false;

   //--- a numeric literal runs until the next structural character
   string acc = "";
   while(at < len)
     {
      ushort c = StringGetCharacter(json, at);
      if(c == ',' || c == '}' || c == ' ' || c == '\r' || c == '\n' || c == '\t')
         break;
      acc += ShortToString(c);
      at++;
     }
   if(StringLen(acc) == 0)
      return false;
   out = StringToDouble(acc);
   return true;
  }

//+------------------------------------------------------------------+
//| The complete set of verbs the bridge will ever act on.           |
//|  Adding a verb here is a security decision, not a feature flag.   |
//|  The order_* verbs are attended execution: they submit an intent, |
//|  and CCharonRisk still decides whether, how large and where the   |
//|  stop goes. None of them accepts a caller-chosen volume.          |
//+------------------------------------------------------------------+
bool CCharonBridge::VerbAllowed(const string verb)
  {
   return (verb == CHARON_BRIDGE_V_ARM ||
           verb == CHARON_BRIDGE_V_DISARM ||
           verb == CHARON_BRIDGE_V_TRAILMODE ||
           verb == CHARON_BRIDGE_V_PING ||
           verb == CHARON_BRIDGE_V_ORDER_OPEN ||
           verb == CHARON_BRIDGE_V_ORDER_MODIFY ||
           verb == CHARON_BRIDGE_V_ORDER_CANCEL);
  }

//+------------------------------------------------------------------+
//| Order-bearing verbs need a full ticket; state verbs do not       |
//+------------------------------------------------------------------+
bool CCharonBridge::OrderVerb(const string verb)
  {
   return (verb == CHARON_BRIDGE_V_ORDER_OPEN ||
           verb == CHARON_BRIDGE_V_ORDER_MODIFY ||
           verb == CHARON_BRIDGE_V_ORDER_CANCEL);
  }

//+------------------------------------------------------------------+
//| Trail modes are the five values the engine knows how to honour   |
//+------------------------------------------------------------------+
bool CCharonBridge::TrailModeValid(const int mode)
  {
   return (mode >= (int)CHARON_TRAIL_OFF && mode <= (int)CHARON_TRAIL_RR_LOCK);
  }

//+------------------------------------------------------------------+
//| Read the optional "stages" array from a ticket                    |
//|                                                                  |
//|  The project's JSON is flat, so this walks the array textually.    |
//|  Each element is an object with kind / trigger / lots_mult. An     |
//|  absent key leaves count at 0, which means "use the chart inputs"  |
//|  and is exactly the pre-1.4.0 behaviour.                          |
//+------------------------------------------------------------------+
bool CCharonBridge::ReadStages(const string json, int &count,
                               SCharonBridgeStage &stages[], string &err)
  {
   count = 0;
   err   = "";

   string needle = "\"stages\"";
   int at = StringFind(json, needle);
   if(at < 0)
      return true;                     //--- no stages: not an error

   int len = StringLen(json);
   at += StringLen(needle);
   while(at < len)
     {
      ushort c = StringGetCharacter(json, at);
      if(c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == ':')
        {
         at++;
         continue;
        }
      break;
     }
   if(at >= len || StringGetCharacter(json, at) != '[')
     {
      err = "stages is not an array";
      return false;
     }
   at++;

   int guard = 0;
   while(at < len && guard < 4096)
     {
      guard++;
      ushort c = StringGetCharacter(json, at);
      if(c == ']')
         return true;                  //--- end of the array
      if(c == ',' || c == ' ' || c == '\t' || c == '\r' || c == '\n')
        {
         at++;
         continue;
        }
      if(c != '{')
        {
         err = "a pyramid stage is not an object";
         return false;
        }

      //--- isolate one element so the ordinary readers can parse it
      int depth = 0;
      int start = at;
      int end   = -1;
      for(int i = at; i < len; i++)
        {
         ushort d = StringGetCharacter(json, i);
         if(d == '{')
            depth++;
         else if(d == '}')
           {
            depth--;
            if(depth == 0)
              {
               end = i;
               break;
              }
           }
        }
      if(end < 0)
        {
         err = "unterminated pyramid stage";
         return false;
        }

      string body = StringSubstr(json, start, end - start + 1);
      at = end + 1;

      if(count >= CHARON_BRIDGE_MAX_STAGES)
        {
         err = StringFormat("a ticket may carry at most %d pyramid stages",
                            CHARON_BRIDGE_MAX_STAGES);
         return false;
        }

      string kind = "";
      if(ReadJsonString(body, "kind", kind))
        {
         if(kind != CHARON_BRIDGE_TRIG_RR && kind != CHARON_BRIDGE_TRIG_PRICE)
           {
            err = StringFormat("stage kind '%s' is not supported", kind);
            return false;
           }
        }
      else
         kind = CHARON_BRIDGE_TRIG_RR;

      double trigger = 0.0;
      if(!ReadJsonNumber(body, "trigger", trigger) ||
         !MathIsValidNumber(trigger) || trigger <= 0.0)
        {
         err = "a pyramid stage has no positive trigger";
         return false;
        }

      double mult = 1.0;
      if(ReadJsonNumber(body, "lots_mult", mult))
        {
         if(!MathIsValidNumber(mult) || mult <= 0.0)
           {
            err = "a pyramid stage has a non-positive volume multiplier";
            return false;
           }
        }
      else
         mult = 1.0;

      stages[count].kind     = (kind == CHARON_BRIDGE_TRIG_PRICE
                                ? (int)CHARON_STAGE_PRICE : (int)CHARON_STAGE_RR);
      stages[count].trigger  = trigger;
      stages[count].lotsMult = mult;
      count++;
     }
   return true;
  }

//+------------------------------------------------------------------+
//| Do the stages advance in the direction the trade is going?        |
//|                                                                  |
//|  The engine only ever adds exposure while the basket is winning,   |
//|  so a stage on the losing side of the entry could never fire. It   |
//|  is refused rather than quietly dropped, because a three-stage     |
//|  plan that silently becomes a one-stage plan is a lie about the    |
//|  risk the operator agreed to.                                     |
//+------------------------------------------------------------------+
bool CCharonBridge::StagesAdvance(const SCharonBridgeStage &stages[], const int count,
                                  const bool isLong, const double top, const double bottom)
  {
   double lastRR    = (isLong ? -DBL_MAX : DBL_MAX);
   double lastPrice = (isLong ? -DBL_MAX : DBL_MAX);

   for(int i = 0; i < count; i++)
     {
      if(stages[i].kind == (int)CHARON_STAGE_PRICE)
        {
         //--- must sit beyond the block on the profit side
         if(isLong && stages[i].trigger <= top)
            return false;
         if(!isLong && stages[i].trigger >= bottom)
            return false;
         if(isLong && stages[i].trigger <= lastPrice)
            return false;
         if(!isLong && stages[i].trigger >= lastPrice)
            return false;
         lastPrice = stages[i].trigger;
        }
      else
        {
         if(isLong && stages[i].trigger <= lastRR)
            return false;
         if(!isLong && stages[i].trigger >= lastRR)
            return false;
         lastRR = stages[i].trigger;
        }
     }
   return true;
  }

//+------------------------------------------------------------------+
//| True when every character is printable ASCII                    |
//|  A broker comment is bytes, not text: anything outside this set   |
//|  is either mangled or truncated by the terminal, so the tag is    |
//|  refused rather than sent to be silently corrupted.               |
//+------------------------------------------------------------------+
bool CCharonBridge::TagIsAscii(const string text)
  {
   int n = StringLen(text);
   for(int i = 0; i < n; i++)
     {
      ushort c = StringGetCharacter(text, i);
      if(c < 32 || c > 126)
         return false;
     }
   return true;
  }

//+------------------------------------------------------------------+
//| Write the snapshot atomically                                    |
//|                                                                  |
//|  The dashboard must never read a half-written document, so the    |
//|  payload goes to <file>.tmp and is then renamed over the live     |
//|  file. A reader either sees the previous snapshot or the new one, |
//|  never a fragment.                                                |
//+------------------------------------------------------------------+
bool CCharonBridge::WriteStateFile(const string payload)
  {
   FolderCreate(CHARON_BRIDGE_ROOT, FILE_COMMON);
   FolderCreate(m_cmdDir, FILE_COMMON);
   FolderCreate(m_doneDir, FILE_COMMON);

   string tmp = m_stateFile + ".tmp";
   ResetLastError();
   int h = FileOpen(tmp, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE)
     {
      m_lastNote = StringFormat("state open failed (%d)", GetLastError());
      return false;
     }
   FileWriteString(h, payload);
   FileClose(h);

   ResetLastError();
   if(!FileMove(tmp, FILE_COMMON, m_stateFile, FILE_COMMON | FILE_REWRITE))
     {
      int err = GetLastError();
      FileDelete(tmp, FILE_COMMON);
      m_lastNote = StringFormat("state move failed (%d)", err);
      return false;
     }

   m_lastNote = "";
   return true;
  }

//+------------------------------------------------------------------+
//| Build and write the JSON snapshot                                |
//|                                                                  |
//|  Field names are the contract with bridge/charon_bridge/schema.py. |
//|  Two rules keep the two sides honest:                            |
//|    * every number is emitted via DoubleToString with an explicit   |
//|      precision, so Python never has to guess a tick size;         |
//|    * the RR ladder is copied as a STRING straight from the engine  |
//|      so a rung price can never disagree with the chart.           |
//+------------------------------------------------------------------+
//+------------------------------------------------------------------+
//| Leg-only publish: no working orders to report                    |
//+------------------------------------------------------------------+
bool CCharonBridge::Publish(const SCharonBridgeSnapshot &snap,
                            const SCharonBridgeLeg &legs[], const int legCount)
  {
   //--- MQL5 has no NULL for a reference array, so this forwards an empty
   //--- one. Writing it out is clearer than overloading on a default, which
   //--- the compiler rejects outright.
   SCharonBridgePending none[];
   return Publish(snap, legs, legCount, none, 0);
  }

//+------------------------------------------------------------------+
bool CCharonBridge::Publish(const SCharonBridgeSnapshot &snap,
                            const SCharonBridgeLeg &legs[], const int legCount,
                            const SCharonBridgePending &pendings[],
                            const int pendingCount)
  {
   int shown = legCount;
   if(shown < 0)
      shown = 0;
   if(shown > CHARON_BRIDGE_MAX_LEGS)
      shown = CHARON_BRIDGE_MAX_LEGS;

   int shownPending = pendingCount;
   if(shownPending < 0)
      shownPending = 0;
   if(shownPending > CHARON_BRIDGE_MAX_LEGS)
      shownPending = CHARON_BRIDGE_MAX_LEGS;

   m_seq++;

   string j = "";
   j += "{";
   j += StringFormat("\"schema\":%d,", CHARON_BRIDGE_SCHEMA);
   j += StringFormat("\"seq\":%I64u,", m_seq);
   j += StringFormat("\"ea_version\":\"%s\",", JsonEscape(CHARON_VERSION));
   j += StringFormat("\"symbol\":\"%s\",", JsonEscape(snap.symbol));
   j += StringFormat("\"server_time\":%I64d,", (long)snap.serverTime);
   j += StringFormat("\"ready\":%s,", (snap.ready ? "true" : "false"));

   //--- chart settings
   j += StringFormat("\"dashboard\":%s,", (snap.dashboardOn ? "true" : "false"));
   j += StringFormat("\"persist\":%s,", (snap.persistOn ? "true" : "false"));

   //--- account
   j += StringFormat("\"digits\":%d,", snap.digits);
   j += StringFormat("\"currency\":\"%s\",", JsonEscape(snap.currency));
   j += StringFormat("\"balance\":%s,", DoubleToString(snap.balance, 2));
   j += StringFormat("\"equity\":%s,", DoubleToString(snap.equity, 2));

   //--- basket
   j += "\"basket\":{";
   j += StringFormat("\"valid\":%s,", (snap.basketValid ? "true" : "false"));
   j += StringFormat("\"mixed\":%s,", (snap.basketMixed ? "true" : "false"));
   j += StringFormat("\"dir\":%I64d,", snap.basketDir);
   j += StringFormat("\"legs\":%d,", snap.basketLegs);
   j += StringFormat("\"lot\":%s,", DoubleToString(snap.basketLot, 2));
   j += StringFormat("\"avg_entry\":%s,", DoubleToString(snap.basketAvgEntry, snap.digits));
   j += StringFormat("\"open_pl\":%s,", DoubleToString(snap.basketOpenPL, 2));
   j += StringFormat("\"risk_money\":%s,", DoubleToString(snap.basketRiskMoney, 2));
   j += StringFormat("\"risk_pct\":%s,", DoubleToString(snap.basketRiskPct, 4));
   j += StringFormat("\"best\":%s,", DoubleToString(snap.basketBest, snap.digits));
   j += StringFormat("\"ladder\":\"%s\"", JsonEscape(snap.basketLadder));
   j += "},";

   //--- risk model in force
   j += "\"model\":{";
   j += StringFormat("\"sl\":\"%s\",", JsonEscape(snap.slText));
   j += StringFormat("\"tp\":\"%s\",", JsonEscape(snap.tpText));
   j += StringFormat("\"sizing\":\"%s\",", JsonEscape(snap.sizingText));
   j += StringFormat("\"max_risk_pct\":%s", DoubleToString(snap.maxRiskPct, 4));
   j += "},";

   //--- trailing
   j += "\"trail\":{";
   j += StringFormat("\"mode\":%d,", snap.trailMode);
   j += StringFormat("\"mode_name\":\"%s\",", JsonEscape(snap.trailText));
   j += "\"mutable\":true";
   j += "},";

   //--- pyramid
   j += "\"pyramid\":{";
   j += StringFormat("\"enabled\":%s,", (snap.pyramidEnabled ? "true" : "false"));
   j += StringFormat("\"max_legs\":%d,", snap.pyramidMaxLegs);
   j += StringFormat("\"max_risk_pct\":%s,", DoubleToString(snap.pyramidMaxRiskPct, 4));
   j += StringFormat("\"trigger\":\"%s\",", (snap.pyramidTrigger == 1 ? "MANUAL" : "AUTO"));
   j += StringFormat("\"arm_key\":%d,", snap.pyramidArmKey);
   j += StringFormat("\"armed\":%s,", (snap.pyramidArmed ? "true" : "false"));
   j += StringFormat("\"secs_left\":%I64d,", snap.pyramidSecsLeft);
   j += StringFormat("\"next_tier\":%d,", snap.nextTier);
   j += StringFormat("\"progress_rr\":%s", DoubleToString(snap.progressRR, 4));
   j += "},";

   //--- order verdict: what the EA decided, in the EA's own numbers.
   //---  Every value here is copied from the last adjudication -- the
   //---  dashboard renders this block verbatim and never recomputes a lot,
   //---  a stop or an R multiple.
   j += "\"order\":{";
   j += StringFormat("\"status\":\"%s\",", JsonEscape(snap.orderStatus));
   j += StringFormat("\"reason\":\"%s\",", JsonEscape(snap.orderReason));
   j += StringFormat("\"side\":\"%s\",", JsonEscape(snap.orderSide));
   j += StringFormat("\"lots\":%s,", DoubleToString(snap.orderLots, 2));
   j += StringFormat("\"entry\":%s,", DoubleToString(snap.orderEntry, snap.digits));
   j += StringFormat("\"stop\":%s,", DoubleToString(snap.orderStop, snap.digits));
   j += StringFormat("\"risk_money\":%s,", DoubleToString(snap.orderRiskMoney, 2));
   j += StringFormat("\"risk_pct\":%s,", DoubleToString(snap.orderRiskPct, 4));
   j += StringFormat("\"rung\":%d,", snap.orderRung);
   j += StringFormat("\"ticket\":%I64u,", snap.orderTicket);
   j += StringFormat("\"journal_tag\":\"%s\")", JsonEscape(snap.orderJournalTag));
   j += "},";

   //--- legs
   j += "\"legs\":[";
   for(int i = 0; i < shown; i++)
     {
      if(i > 0)
         j += ",";
      j += "{";
      j += StringFormat("\"ticket\":%I64u,", legs[i].ticket);
      j += StringFormat("\"type\":%I64d,", legs[i].type);
      j += StringFormat("\"side\":\"%s\",",
                        (legs[i].type == (long)POSITION_TYPE_BUY ? "BUY" : "SELL"));
      j += StringFormat("\"tier\":%d,", legs[i].tier);
      j += StringFormat("\"entry\":%s,", DoubleToString(legs[i].entry, snap.digits));
      j += StringFormat("\"lot\":%s,", DoubleToString(legs[i].lot, 2));
      j += StringFormat("\"sl\":%s,", DoubleToString(legs[i].currentSL, snap.digits));
      j += StringFormat("\"risk_price\":%s,", DoubleToString(legs[i].riskPrice, snap.digits));
      j += StringFormat("\"hwm\":%s,", DoubleToString(legs[i].hwm, snap.digits));
      j += StringFormat("\"progress_rr\":%s,", DoubleToString(legs[i].progressRR, 4));
      j += StringFormat("\"trail_active\":%s,", (legs[i].trailActive ? "true" : "false"));
      j += StringFormat("\"be_done\":%s,", (legs[i].beDone ? "true" : "false"));
      j += StringFormat("\"half_risk_done\":%s,", (legs[i].halfRiskDone ? "true" : "false"));
      j += StringFormat("\"ladder\":\"%s\"", JsonEscape(legs[i].ladder));
      j += "}";
     }
   j += "],";

   //--- working orders. A pre-1.4.0 bridge ignores this key entirely, which
   //--- is why the field is additive rather than a schema-breaking change.
   j += "\"pending\":[";
   for(int i = 0; i < shownPending; i++)
     {
      if(i > 0)
         j += ",";
      j += "{";
      j += StringFormat("\"ticket\":%I64u,", pendings[i].ticket);
      j += StringFormat("\"side\":\"%s\",",
                        (pendings[i].type == (long)POSITION_TYPE_BUY ? "BUY" : "SELL"));
      j += StringFormat("\"order_type\":\"%s\",",
                        (pendings[i].orderType == (long)ORDER_TYPE_BUY_LIMIT
                         || pendings[i].orderType == (long)ORDER_TYPE_SELL_LIMIT
                         ? "LIMIT" : "STOP"));
      j += StringFormat("\"price\":%s,", DoubleToString(pendings[i].price, snap.digits));
      j += StringFormat("\"sl\":%s,", DoubleToString(pendings[i].sl, snap.digits));
      j += StringFormat("\"lot\":%s,", DoubleToString(pendings[i].lot, 2));
      j += StringFormat("\"placed_at\":%I64d", (long)pendings[i].placedAt);
      j += "}";
     }
   j += "]";
   j += "}";

   m_lastOk = WriteStateFile(j);
   return m_lastOk;
  }

//+------------------------------------------------------------------+
//| Oldest pending command file name, or "" when the inbox is empty  |
//|  Files are named <epoch>-<rand>.json so a plain sort is already a |
//|  FIFO. Ordering matters: an `arm` then a `disarm` must not be     |
//|  applied backwards.                                              |
//+------------------------------------------------------------------+
string CCharonBridge::NextPending(void)
  {
   ResetLastError();
   string name = "";
   long handle = FileFindFirst(m_cmdDir + "\\*.json", name, FILE_COMMON);
   if(handle == INVALID_HANDLE)
      return "";

   string best = "";
   while(name != "")
     {
      if(StringFind(name, ".json") >= 0 && (best == "" || StringCompare(name, best, true) < 0))
         best = name;
      //--- FileFindNext overwrites 'name'; an empty name ends the walk
      name = "";
      if(!FileFindNext(handle, name))
         break;
     }
   FileFindClose(handle);
   return best;
  }

//+------------------------------------------------------------------+
//| Parse and validate one command file                              |
//|                                                                  |
//|  Every refusal reason is recorded on the command so the caller    |
//|  can retire the file with an audit note instead of silently       |
//|  dropping it. A malformed file is never acted upon.               |
//+------------------------------------------------------------------+
bool CCharonBridge::ReadCommandFile(const string name, SCharonBridgeCommand &cmd)
  {
   cmd.valid  = false;
   cmd.id     = "";
   cmd.verb   = "";
   cmd.arg    = "";
   cmd.issued = 0;
   cmd.reject = "";
   cmd.side          = "";
   cmd.orderType     = "";
   cmd.entry         = 0.0;
   cmd.blockTop      = 0.0;
   cmd.blockBottom   = 0.0;
   cmd.riskPct       = 0.0;
   cmd.trailTrigger  = (int)CHARON_TRAIL_OFF;
   cmd.rung          = 0;
   cmd.ticket        = 0;
   cmd.riskMoney     = 0.0;
   cmd.basketCeilingPct = 0.0;
   cmd.trailStartRR  = 0.0;
   cmd.trailStartPoints = 0.0;
   cmd.journalTag    = "";
   cmd.stageCount    = 0;
   for(int s = 0; s < CHARON_BRIDGE_MAX_STAGES; s++)
     {
      cmd.stages[s].kind     = (int)CHARON_STAGE_RR;
      cmd.stages[s].trigger  = 0.0;
      cmd.stages[s].lotsMult = 0.0;
     }

   string path = m_cmdDir + "\\" + name;
   ResetLastError();
   int h = FileOpen(path, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h == INVALID_HANDLE)
     {
      cmd.reject = StringFormat("open failed (%d)", GetLastError());
      return false;
     }

   //--- a command is a handful of bytes; refuse anything unreasonable
   int size = (int)FileSize(h);
   if(size <= 0)
     {
      FileClose(h);
      cmd.reject = "empty file";
      return false;
     }
   if(size > 4096)
     {
      FileClose(h);
      cmd.reject = StringFormat("oversized (%d bytes)", size);
      return false;
     }

   string body = "";
   while(!FileIsEnding(h))
      body += FileReadString(h);
   FileClose(h);

   string id = "";
   if(!ReadJsonString(body, "id", id))
     {
      cmd.reject = "missing id";
      return false;
     }
   cmd.id = id;

   string verb = "";
   if(!ReadJsonString(body, "verb", verb))
     {
      cmd.reject = "missing verb";
      return false;
     }
   if(!VerbAllowed(verb))
     {
      cmd.reject = StringFormat("verb '%s' is not permitted", verb);
      return false;
     }
   cmd.verb = verb;

   //--- the bridge targets exactly one symbol: its own
   string sym = "";
   if(ReadJsonString(body, "symbol", sym))
     {
      if(sym != "" && sym != m_symbol)
        {
         cmd.reject = StringFormat("symbol '%s' is not '%s'", sym, m_symbol);
         return false;
        }
     }

   //--- schema must match, so a newer Python cannot smuggle new semantics
   double schema = 0.0;
   if(ReadJsonNumber(body, "schema", schema))
     {
      if((int)schema != CHARON_BRIDGE_SCHEMA)
        {
         cmd.reject = StringFormat("schema %.0f is not %d", schema, CHARON_BRIDGE_SCHEMA);
         return false;
        }
     }

   //--- anti-replay: a stale or future-dated command is refused outright
   double issued = 0.0;
   if(!ReadJsonNumber(body, "issued", issued))
     {
      cmd.reject = "missing issued timestamp";
      return false;
     }
   cmd.issued = (long)issued;
   long now = (long)TimeLocal();
   long age = now - cmd.issued;
   if(age > CHARON_BRIDGE_CMD_MAX_AGE)
     {
      cmd.reject = StringFormat("stale command (%ds old)", (int)age);
      return false;
     }
   if(age < -CHARON_BRIDGE_CMD_MAX_AGE)
     {
      cmd.reject = StringFormat("future-dated command (%ds ahead)", (int)(-age));
      return false;
     }

   //--- per-verb argument validation, before anything is applied
   if(verb == CHARON_BRIDGE_V_TRAILMODE)
     {
      double mode = -1.0;
      if(!ReadJsonNumber(body, "mode", mode))
        {
         cmd.reject = "trail_mode without a numeric mode";
         return false;
        }
      if(!TrailModeValid((int)mode))
        {
         cmd.reject = StringFormat("trail mode %.0f is out of range", mode);
         return false;
        }
      cmd.arg = StringFormat("%d", (int)mode);
     }
   else
      cmd.arg = "";

   //--- order tickets: every field is re-validated here, because a ticket
   //--- that reaches ApplyBridgeCommand unchecked would let the inbox
   //--- decide risk. A missing or nonsensical field is a refusal, never a
   //--- silent default.
   if(OrderVerb(verb))
     {
      string side = "";
      if(!ReadJsonString(body, "side", side) || (side != "BUY" && side != "SELL"))
        {
         cmd.reject = "order ticket without a valid side";
         return false;
        }

      string otype = "";
      if(ReadJsonString(body, "order_type", otype))
        {
         if(otype != "MARKET" && otype != "LIMIT")
           {
            cmd.reject = StringFormat("order type '%s' is not supported", otype);
            return false;
           }
        }
      else
         otype = "MARKET";

      double top = 0.0, bottom = 0.0;
      if(!ReadJsonNumber(body, "block_top", top) ||
         !ReadJsonNumber(body, "block_bottom", bottom))
        {
         cmd.reject = "order ticket without block geometry";
         return false;
        }
      if(!MathIsValidNumber(top) || !MathIsValidNumber(bottom) ||
         top <= 0.0 || bottom <= 0.0 || top == bottom)
        {
         cmd.reject = "block geometry is degenerate";
         return false;
        }
      //--- the block's orientation must agree with the requested direction:
      //--- an inverted block would put the stop on the wrong side of the
      //--- structure the trader claims to be trading against.
      if(side == "BUY" && top <= bottom)
        {
         cmd.reject = "a BUY block needs block_top above block_bottom";
         return false;
        }
      if(side == "SELL" && bottom <= top)
        {
         cmd.reject = "a SELL block needs block_bottom above block_top";
         return false;
        }

      double entry = 0.0;
      if(ReadJsonNumber(body, "entry", entry))
        {
         if(!MathIsValidNumber(entry) || entry <= 0.0)
           {
            cmd.reject = "entry price is not a positive number";
            return false;
           }
         if(otype == "LIMIT")
           {
            double lo = (top < bottom ? top : bottom);
            double hi = (top < bottom ? bottom : top);
            if(!(entry > lo && entry < hi))
              {
               cmd.reject = "a LIMIT entry must sit inside the block";
               return false;
              }
           }
        }
      else
        {
         if(otype == "LIMIT")
           {
            cmd.reject = "a LIMIT order needs an entry price";
            return false;
           }
         entry = 0.0;
        }

      //--- risk is stated exactly once. Both figures present is two answers
      //--- to one question, so it is refused here as well as in Python --
      //--- the EA must not depend on the client having validated anything.
      double riskPct = 0.0;
      bool   havePct = ReadJsonNumber(body, "risk_pct", riskPct);
      double riskMoney = 0.0;
      bool   haveMoney = ReadJsonNumber(body, "risk_money", riskMoney);

      if(havePct && haveMoney)
        {
         cmd.reject = "order ticket states risk_pct and risk_money; send one";
         return false;
        }
      if(!havePct && !haveMoney)
        {
         cmd.reject = "order ticket without risk_pct or risk_money";
         return false;
        }
      if(havePct)
        {
         if(!MathIsValidNumber(riskPct) || riskPct <= 0.0 || riskPct > InpMaxRiskPct)
           {
            cmd.reject = StringFormat("risk %.4f%% is outside (0, %.4f]",
                                      riskPct, InpMaxRiskPct);
            return false;
           }
         riskMoney = 0.0;
        }
      else
        {
         //--- the account is the EA's to know, so only the sign can be
         //--- checked here; the money figure is clamped to
         //--- MaxAllowedRiskMoney() inside ApplyOrderCommand.
         if(!MathIsValidNumber(riskMoney) || riskMoney <= 0.0)
           {
            cmd.reject = StringFormat("risk money %.2f is not positive", riskMoney);
            return false;
           }
         riskPct = 0.0;
        }

      //--- optional whole-basket ceiling. 0 means "not stated", which leaves
      //--- InpPyramidMaxRiskPct as the only ceiling -- exactly the
      //--- pre-1.4.0 behaviour.
      double ceiling = 0.0;
      if(ReadJsonNumber(body, "basket_risk_ceiling_pct", ceiling))
        {
         if(!MathIsValidNumber(ceiling) || ceiling < 0.0 || ceiling > 100.0)
           {
            cmd.reject = StringFormat("basket ceiling %.4f%% is outside [0, 100]", ceiling);
            return false;
           }
         if(ceiling > 0.0 && riskPct > 0.0 && riskPct > ceiling)
           {
            cmd.reject = "risk_pct already exceeds the basket ceiling";
            return false;
           }
        }
      else
         ceiling = 0.0;

      double trailTrigger = (double)CHARON_TRAIL_OFF;
      if(ReadJsonNumber(body, "trail_trigger", trailTrigger))
        {
         if(!TrailModeValid((int)trailTrigger))
           {
            cmd.reject = StringFormat("trail trigger %.0f is out of range", trailTrigger);
            return false;
           }
        }
      else
         trailTrigger = (double)CHARON_TRAIL_OFF;

      double rung = 0.0;
      if(ReadJsonNumber(body, "rung", rung))
        {
         if(rung < 0.0 || rung > (double)CHARON_BRIDGE_MAX_RUNG)
           {
            cmd.reject = StringFormat("rung %.0f is out of range", rung);
            return false;
           }
        }
      else
         rung = 0.0;

      double ticket = 0.0;
      bool needsTicket = (verb == CHARON_BRIDGE_V_ORDER_MODIFY ||
                          verb == CHARON_BRIDGE_V_ORDER_CANCEL);
      if(needsTicket)
        {
         if(!ReadJsonNumber(body, "ticket", ticket) || ticket <= 0.0)
           {
            cmd.reject = "order ticket has no positive ticket number";
            return false;
           }
        }

      //--- optional per-ticket trail activation. 0 (or absent) leaves the
      //--- chart input as the threshold, so an untouched ticket trails
      //--- exactly as it did before v1.4.0.
      double startRR = 0.0;
      if(ReadJsonNumber(body, "trail_start_rr", startRR))
        {
         if(!MathIsValidNumber(startRR) || startRR < 0.0)
           {
            cmd.reject = StringFormat("trail start RR %.4f is not positive", startRR);
            return false;
           }
        }
      else
         startRR = 0.0;

      double startPoints = 0.0;
      if(ReadJsonNumber(body, "trail_start_points", startPoints))
        {
         if(!MathIsValidNumber(startPoints) || startPoints < 0.0)
           {
            cmd.reject = StringFormat("trail start points %.1f is not positive", startPoints);
            return false;
           }
        }
      else
         startPoints = 0.0;

      //--- short ASCII tag for the broker comment. Everything else about the
      //--- note stays on the bridge side; the chart only ever renders this.
      string tag = "";
      if(ReadJsonString(body, "journal_tag", tag))
        {
         if(StringLen(tag) > CHARON_BRIDGE_TAG_MAX)
            tag = StringSubstr(tag, 0, CHARON_BRIDGE_TAG_MAX);
         if(!TagIsAscii(tag))
           {
            cmd.reject = "journal tag carries characters a broker comment cannot hold";
            return false;
           }
        }
      else
         tag = "";

      //--- pyramid stages. Parsed only when the key is present, so a ticket
      //--- without them keeps the chart-wide InpPyramidT2RR/... ladder
      //--- exactly as it is today.
      SCharonBridgeStage stages[];
      ArrayResize(stages, CHARON_BRIDGE_MAX_STAGES);
      int stageCount = 0;
      string stageErr = "";
      if(!ReadStages(body, stageCount, stages, stageErr))
        {
         cmd.reject = stageErr;
         return false;
        }
      if(stageCount > 0 && !StagesAdvance(stages, stageCount, (side == "BUY"), top, bottom))
        {
         cmd.reject = "pyramid stages do not advance in the trade's direction";
         return false;
        }

      cmd.side         = side;
      cmd.orderType    = otype;
      cmd.entry        = entry;
      cmd.blockTop     = top;
      cmd.blockBottom  = bottom;
      cmd.riskPct      = riskPct;
      cmd.riskMoney    = riskMoney;
      cmd.basketCeilingPct = ceiling;
      cmd.trailTrigger = (int)trailTrigger;
      cmd.trailStartRR = startRR;
      cmd.trailStartPoints = startPoints;
      cmd.journalTag   = tag;
      cmd.stageCount   = 0;
      for(int s = 0; s < stageCount && s < CHARON_BRIDGE_MAX_STAGES; s++)
        {
         cmd.stages[s].kind     = stages[s].kind;
         cmd.stages[s].trigger  = stages[s].trigger;
         cmd.stages[s].lotsMult = stages[s].lotsMult;
         cmd.stageCount         = s + 1;
        }
      cmd.rung         = (int)rung;
      cmd.ticket       = (ulong)ticket;
     }

   cmd.valid = true;
   return true;
  }

//+------------------------------------------------------------------+
//| Retire a consumed command into processed/ as an audit trail      |
//|                                                                  |
//|  Consume-once is enforced by the rename, not by a flag, so a      |
//|  crash between "read" and "apply" cannot replay the command on    |
//|  the next tick. The original file is kept, never deleted.         |
//+------------------------------------------------------------------+
bool CCharonBridge::Retire(const string name, const SCharonBridgeCommand &cmd)
  {
   string from = m_cmdDir + "\\" + name;
   //--- prefix the archived file with the verdict so the trail is readable
   string tag  = (cmd.valid ? "ok" : "rejected");
   string dest = m_doneDir + "\\" + tag + "_" + name;

   ResetLastError();
   if(FileMove(from, FILE_COMMON, dest, FILE_COMMON | FILE_REWRITE))
      return true;

   //--- a leftover .json would be re-read on the next tick, so a failed
   //--- retire must still get the file out of the inbox
   int err = GetLastError();
   if(FileDelete(from, FILE_COMMON))
     {
      m_lastNote = StringFormat("retire rename failed (%d), deleted instead", err);
      return true;
     }
   m_lastNote = StringFormat("retire failed for %s (%d)", name, err);
   return false;
  }

//+------------------------------------------------------------------+
//| Take at most one command per call                                |
//|                                                                  |
//|  The file is retired BEFORE it is returned, so the caller can     |
//|  never see the same command twice even if applying it throws.     |
//|  Invalid files are retired too: a poison file would otherwise     |
//|  head-of-line block the inbox forever.                            |
//+------------------------------------------------------------------+
bool CCharonBridge::Poll(SCharonBridgeCommand &cmd)
  {
   cmd.valid  = false;
   cmd.id     = "";
   cmd.verb   = "";
   cmd.arg    = "";
   cmd.issued = 0;
   cmd.reject = "";

   string name = NextPending();
   if(name == "")
      return false;

   ReadCommandFile(name, cmd);      // sets cmd.valid / cmd.reject
   Retire(name, cmd);

   if(!cmd.valid && InpVerbose && cmd.reject != "")
      PrintFormat("%s bridge: refused command %s - %s", CHARON_NAME, name, cmd.reject);

   return cmd.valid;
  }

//+------------------------------------------------------------------+
#endif // __CHARON_BRIDGE__
//+------------------------------------------------------------------+

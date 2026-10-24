//+------------------------------------------------------------------+
//|                                                CharonDefines.mqh |
//|                    Charon - semi-automated MQL5 trading copilot  |
//|                                                                  |
//|  SCOPE       : strict stop loss | trailing stop | RR targets |    |
//|                risk measurement + position sizing | pyramiding   |
//|  OUT OF SCOPE: entry signals, indicators, news filters, market   |
//|                structure scanners, anything that decides WHEN to |
//|                open the first position.                          |
//|                                                                  |
//|  The human opens every primary position. Charon adopts it, arms  |
//|  a strict stop, sizes and validates the risk, projects the RR    |
//|  target and optionally trails / pyramids.                        |
//+------------------------------------------------------------------+
#property copyright "Charon"
#property version   "1.00"
#property link      "https://github.com/vickygujjar17/Charon-.git"

#ifndef __CHARON_DEFINES__
#define __CHARON_DEFINES__

//+------------------------------------------------------------------+
//| Version - single source of truth                                 |
//| _tools/bump_version.py rewrites this literal and every other     |
//| version string in the repository in one pass.                    |
//+------------------------------------------------------------------+
#define CHARON_VERSION        "1.0.1"
#define CHARON_NAME           "Charon"
#define CHARON_REPO           "https://github.com/vickygujjar17/Charon-.git"

//+------------------------------------------------------------------+
//| Hard limits (fixed-size arrays keep everything allocation-free)  |
//+------------------------------------------------------------------+
#define CHARON_MAX_STATES     256     // max simultaneously tracked positions
#define CHARON_MAX_TIERS      4       // tier 1 (primary) + 3 scale-ins
#define CHARON_GV_ROOT        "CHARON" // global-variable prefix for persistence
#define CHARON_COMMENT        "Charon"
#define CHARON_SAVE_INTERVAL_SEC 1      // min seconds between persistence writes

//+------------------------------------------------------------------+
//| Enumerations                                                     |
//+------------------------------------------------------------------+

//--- how the strict stop distance is measured
enum ENUM_CHARON_SL_UNIT
  {
   CHARON_SL_POINTS    = 0,   // Points
   CHARON_SL_ATR       = 1,   // ATR multiple
   CHARON_SL_PRICE_PCT = 2    // Percent of entry price
  };

//--- how the take profit (RR target) is measured
enum ENUM_CHARON_TP_UNIT
  {
   CHARON_TP_NONE   = 0,      // No target
   CHARON_TP_RR     = 1,      // R multiple of the strict stop
   CHARON_TP_POINTS = 2       // Fixed points
  };

//--- trailing stop engine mode
enum ENUM_CHARON_TRAIL_MODE
  {
   CHARON_TRAIL_OFF         = 0,  // Disabled
   CHARON_TRAIL_POINTS      = 1,  // Fixed distance in points from the extreme
   CHARON_TRAIL_ATR         = 2,  // ATR multiple from the extreme
   CHARON_TRAIL_HWM_PERCENT = 3,  // Give back N% of the gain from the extreme
   CHARON_TRAIL_RR_LOCK     = 4   // Lock (current R - giveback R)
  };

//--- position sizing model
enum ENUM_CHARON_LOT_MODE
  {
   CHARON_LOT_RISK_PCT    = 0,  // Risk percent of account
   CHARON_LOT_FIXED_MONEY = 1,  // Fixed money risk per trade
   CHARON_LOT_FIXED_LOTS  = 2   // Fixed lots (no risk sizing)
  };

//--- what to do when an adopted position carries no usable stop loss
enum ENUM_CHARON_SL_ACTION
  {
   CHARON_SL_ATTACH   = 0,  // Attach a strict stop
   CHARON_SL_CLOSE    = 1,  // Flatten the position
   CHARON_SL_LOG_ONLY = 2   // Report only (position left unprotected)
  };

//--- half-risk tightening stage (applied before breakeven)
enum ENUM_CHARON_HALF_RISK
  {
   CHARON_HALF_OFF      = 0,  // Disabled
   CHARON_HALF_HALVE    = 1,  // Move the stop to 0.5R at the trigger
   CHARON_HALF_LOCK_EN  = 2   // Move the stop to entry (breakeven) at the trigger
  };

//+------------------------------------------------------------------+
//| Structures                                                       |
//+------------------------------------------------------------------+

//--- per-ticket management state (no strings: kept in a fixed array)
struct SCharonPosState
  {
   ulong             ticket;          // position ticket
   long              type;            // POSITION_TYPE_BUY / POSITION_TYPE_SELL
   double            entry;           // fill price (refreshed every tick)
   double            lot;             // current volume (refreshed every tick)
   double            initialSL;       // strict stop armed at adoption
   double            currentSL;       // last stop successfully applied
   double            riskPrice;       // |entry - initialSL| = 1R in price terms
   double            hwm;             // best price reached (high for longs)
   datetime          entryTime;       // position open time
   int               tier;            // 1 = primary, 2..4 = scale-ins
   bool              trailActive;     // trailing has been armed
   bool              halfRiskDone;    // half-risk latch
   bool              beDone;          // breakeven latch
   bool              tpApplied;       // RR target written
   int               stopRejections;  // consecutive broker rejects
   int               stopApplies;     // successful stop modifications
   datetime          savedAt;         // last persistence write (throttle)
  };

//--- aggregated view of every managed position on the symbol
struct SCharonBasket
  {
   bool              valid;
   bool              mixed;           // legs disagree on direction
   long              dir;             // POSITION_TYPE_BUY/SELL (valid when !mixed)
   int               legs;
   double            lot;             // total volume
   double            avgEntry;        // volume weighted average entry
   double            openPL;          // floating profit + swap
   double            riskMoney;       // open risk at the live stops
   double            riskPct;         // riskMoney as % of equity
   double            bestPrice;       // extreme reached by the basket
  };

//--- tier definition (scale-in ladder)
struct SCharonTierSpec
  {
   int               index;           // 2..4
   double            activationRR;    // R multiple required to fire
   double            lotMultiplier;   // volume multiplier vs tier 1
  };

//+------------------------------------------------------------------+
//| Input parameters                                                 |
//+------------------------------------------------------------------+
input group "===== [0] GENERAL ====="
input long            InpMagicFilter        = 0;          // Manage only this magic (0 = every manual trade)
input int             InpMagic              = 20261024;   // Magic used by Charon's own scale-in legs
input bool            InpVerbose            = true;       // Verbose console logging
input bool            InpShowDashboard      = true;       // Draw the on-chart status panel
input bool            InpPersistState       = true;       // Persist per-ticket state across restarts

input group "===== [1] RISK MODEL ====="
input ENUM_CHARON_LOT_MODE InpLotMode       = CHARON_LOT_RISK_PCT; // Sizing model
input double          InpRiskPct            = 0.50;       // Risk per trade (% of account)
input double          InpRiskMoney          = 50.0;       // Fixed money risk per trade
input double          InpFixedLots          = 0.10;       // Fixed lots (FIXED_LOTS mode)
input bool            InpUseEquity          = true;       // Size off equity (false = balance)
input double          InpMaxRiskPct         = 2.00;       // Hard abort if actual risk exceeds this %
input bool            InpAllowMinLot        = false;      // Allow broker min lot when risk rounds to 0
input double          InpCommissionPerLot   = 0.0;        // Round-turn commission per lot (account ccy)

input group "===== [2] STRICT STOP LOSS ====="
input ENUM_CHARON_SL_UNIT InpSlUnit         = CHARON_SL_ATR; // Initial stop unit
input double          InpSlPoints           = 300.0;      // Stop distance (points)
input double          InpSlAtrMult          = 1.50;       // Stop distance (ATR multiple)
input double          InpSlPricePct         = 0.50;       // Stop distance (% of entry price)
input int             InpAtrPeriod          = 14;         // ATR period
input double          InpStrictStopBufferPts = 2.0;       // Extra points beyond the broker minimum
input ENUM_CHARON_SL_ACTION InpMissingSlAction = CHARON_SL_ATTACH; // Action when a stop is missing
input ENUM_CHARON_HALF_RISK InpHalfRiskMode = CHARON_HALF_OFF;     // Half-risk tightening stage
input double          InpHalfRiskRR         = 0.50;       // Half-risk trigger (R)
input double          InpBreakevenRR        = 1.00;       // Breakeven trigger (R)

input group "===== [3] RR TARGETS ====="
input ENUM_CHARON_TP_UNIT InpTpUnit         = CHARON_TP_NONE; // Take profit unit
input double          InpTpRR               = 2.00;       // Reward:risk multiple
input double          InpTpPoints           = 600.0;      // Fixed take profit (points)
input bool            InpApplyTpOnAdopt     = true;       // Set the RR target when a trade is adopted

input group "===== [4] TRAILING STOP ====="
input ENUM_CHARON_TRAIL_MODE InpTrailMode   = CHARON_TRAIL_OFF; // Trailing mode
input double          InpTrailStartRR       = 1.00;       // Activation (R)
input double          InpTrailStartPoints   = 0.0;        // Activation (points gained, 0 = off)
input double          InpTrailPoints        = 150.0;      // Trail distance (points)
input double          InpTrailAtrMult       = 1.50;       // Trail distance (ATR multiple)
input double          InpTrailGivebackPct   = 25.0;       // HWM mode: max % of gain given back
input double          InpTrailGivebackRR    = 0.50;       // RR lock mode: R given back
input double          InpTrailStepPoints    = 5.0;        // Minimum improvement before a modify

input group "===== [5] PYRAMIDING ====="
input bool            InpPyramidEnable      = false;      // Enable scale-ins
input int             InpPyramidMaxLegs     = 3;          // Max legs incl. primary (2..4)
input double          InpPyramidT2RR        = 1.00;       // Tier 2 trigger (R)
input double          InpPyramidT3RR        = 2.00;       // Tier 3 trigger (R)
input double          InpPyramidT4RR        = 3.00;       // Tier 4 trigger (R)
input double          InpPyramidT2Mult      = 0.50;       // Tier 2 lot multiplier
input double          InpPyramidT3Mult      = 0.50;       // Tier 3 lot multiplier
input double          InpPyramidT4Mult      = 0.50;       // Tier 4 lot multiplier
input double          InpPyramidMaxRiskPct  = 1.50;       // Reject a tier above this basket risk %
input bool            InpPyramidCollectiveBE = true;      // Move the whole basket to collective breakeven
input int             InpPyramidCooldownSec = 30;         // Minimum seconds between scale-ins

input group "===== [6] EXECUTION ====="
input int             InpSlippagePoints     = 20;         // Max slippage (points)
input int             InpOrderRetries       = 3;          // OrderSend attempts
input int             InpRetryDelayMs       = 300;        // Delay between attempts (ms)
input double          InpMaxSpreadPoints    = 0.0;        // Block new legs above this spread (0 = off)

//+------------------------------------------------------------------+
#endif // __CHARON_DEFINES__
//+------------------------------------------------------------------+

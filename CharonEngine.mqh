//+------------------------------------------------------------------+
//|                                                 CharonEngine.mqh |
//|  Orchestration: adoption, per-tick management, basket view,       |
//|  pyramid escalation and the on-chart dashboard.                   |
//|                                                                  |
//|  There is exactly one public entry point - OnTickWork() - so the  |
//|  order of operations is auditable in a single place.              |
//+------------------------------------------------------------------+
#property copyright "Charon"
#property version   "1.02"

#ifndef __CHARON_ENGINE__
#define __CHARON_ENGINE__

#include "CharonDefines.mqh"
#include "CharonMath.mqh"
#include "CharonRisk.mqh"
#include "CharonTrade.mqh"
#include "CharonStops.mqh"
#include "CharonTrail.mqh"
#include "CharonPyramid.mqh"
#include "CharonState.mqh"
#include "CharonBridge.mqh"

//--- one adoption pass can never scan more than this
#define CHARON_SCAN_LIMIT 512

//+------------------------------------------------------------------+
//| CCharonEngine                                                    |
//+------------------------------------------------------------------+
class CCharonEngine
  {
private:
   CCharonRisk       m_risk;
   CCharonStops      m_stops;
   CCharonTrail      m_trail;
   CCharonPyramid    m_pyramid;
   CCharonState      m_state;
   CCharonBridge     m_bridge;
   SCharonExecOpts   m_opts;
   SCharonBasket     m_basket;
   string            m_symbol;
   bool              m_ready;
   bool              m_busy;
   datetime          m_lastScan;

   //--- pipeline stages
   bool              ScanAndAdopt(void);
   void              ManageRow(const int index);
   void              ManageLadders(const int index);
   void              BuildBasket(void);
   void              MaybePyramid(void);
   void              MoveBasketToBreakeven(void);
   void              DrawDashboard(void);

   //--- optional file bridge (never required for trading)
   void              ServiceBridge(void);
   bool              ApplyBridgeCommand(const SCharonBridgeCommand &cmd);
   bool              ApplyOrderCommand(const SCharonBridgeCommand &cmd);
   void              SetOrderVerdict(const string status, const string reason,
                                     const string side, const double lots,
                                     const double entry, const double stop,
                                     const double riskMoney, const double riskPct,
                                     const int rung, const ulong ticket);
   double            OrderTarget(const double entry, const double distance,
                                 const bool isLong);
   bool              HasOwnPending(void);
   void              PublishBridge(void);

   //--- verdict of the last attended ticket, published verbatim
   string            m_orderStatus;
   string            m_orderReason;
   string            m_orderSide;
   double            m_orderLots;
   double            m_orderEntry;
   double            m_orderStop;
   double            m_orderRiskMoney;
   double            m_orderRiskPct;
   int               m_orderRung;
   ulong             m_orderTicket;

   //--- helpers
   bool              WantsTicket(const ulong magic) const;
   void              AdoptTicket(const ulong ticket);
   double            BasketRiskMoney(const SCharonBasket &basket) const;
   double            BasketRefRisk(void) const;
   string            RrLadderText(const double entry, const double riskPrice,
                                  const bool isLong);

public:
                     CCharonEngine(void);
                    ~CCharonEngine(void);

   bool              Init(void);
   void              Deinit(void);
   void              OnTickWork(void);
   void              OnTimerWork(void);

   //--- CHARTEVENT_KEYDOWN: arm the MANUAL pyramid trigger
   void              OnKey(const long key);

   bool              Basket(SCharonBasket &out) const { out = m_basket; return m_basket.valid; }
  };
//+------------------------------------------------------------------+
//| Implementation                                                   |
//+------------------------------------------------------------------+

//+------------------------------------------------------------------+
CCharonEngine::CCharonEngine(void)
  {
   m_symbol   = "";
   m_ready    = false;
   m_busy     = false;
   m_lastScan = 0;
   m_orderStatus    = "idle";
   m_orderReason    = "";
   m_orderSide      = "";
   m_orderLots      = 0.0;
   m_orderEntry     = 0.0;
   m_orderStop      = 0.0;
   m_orderRiskMoney = 0.0;
   m_orderRiskPct   = 0.0;
   m_orderRung      = 0;
   m_orderTicket    = 0;
   m_opts.symbol   = "";
   m_opts.slippage = 0;
   m_opts.retries  = 1;
   m_opts.delayMs  = 0;
   m_basket.valid     = false;
   m_basket.mixed     = false;
   m_basket.dir       = 0;
   m_basket.legs      = 0;
   m_basket.lot       = 0.0;
   m_basket.avgEntry  = 0.0;
   m_basket.openPL    = 0.0;
   m_basket.riskMoney = 0.0;
   m_basket.riskPct   = 0.0;
   m_basket.bestPrice = 0.0;
  }

//+------------------------------------------------------------------+
CCharonEngine::~CCharonEngine(void)
  {
   m_risk.Deinit();
  }

//+------------------------------------------------------------------+
//| Validate the input set before any money is touched               |
//+------------------------------------------------------------------+
bool CCharonEngine::Init(void)
  {
   m_symbol = _Symbol;

   //--- hard input validation: refuse to run with a nonsensical setup
   if(InpRiskPct <= 0.0 && InpLotMode == CHARON_LOT_RISK_PCT)
     {
      PrintFormat("%s: InpRiskPct must be > 0 in RISK_PCT mode", CHARON_NAME);
      return false;
     }
   if(InpRiskMoney <= 0.0 && InpLotMode == CHARON_LOT_FIXED_MONEY)
     {
      PrintFormat("%s: InpRiskMoney must be > 0 in FIXED_MONEY mode", CHARON_NAME);
      return false;
     }
   if(InpFixedLots <= 0.0 && InpLotMode == CHARON_LOT_FIXED_LOTS)
     {
      PrintFormat("%s: InpFixedLots must be > 0 in FIXED_LOTS mode", CHARON_NAME);
      return false;
     }
   if(InpMaxRiskPct <= 0.0)
     {
      PrintFormat("%s: InpMaxRiskPct must be > 0", CHARON_NAME);
      return false;
     }
   if(InpSlUnit == CHARON_SL_ATR && InpSlAtrMult <= 0.0)
     {
      PrintFormat("%s: InpSlAtrMult must be > 0 in ATR mode", CHARON_NAME);
      return false;
     }
   if(InpSlUnit == CHARON_SL_POINTS && InpSlPoints <= 0.0)
     {
      PrintFormat("%s: InpSlPoints must be > 0 in POINTS mode", CHARON_NAME);
      return false;
     }
   if(InpSlUnit == CHARON_SL_PRICE_PCT && InpSlPricePct <= 0.0)
     {
      PrintFormat("%s: InpSlPricePct must be > 0 in PRICE_PCT mode", CHARON_NAME);
      return false;
     }
   if(InpLotMode == CHARON_LOT_RISK_PCT && InpRiskPct > InpMaxRiskPct)
     {
      PrintFormat("%s: InpRiskPct (%.2f) exceeds InpMaxRiskPct (%.2f) - aborting",
                  CHARON_NAME, InpRiskPct, InpMaxRiskPct);
      return false;
     }

   if(!m_risk.Init(m_symbol, PERIOD_CURRENT, InpAtrPeriod))
     {
      PrintFormat("%s: risk module failed to initialise on %s", CHARON_NAME, m_symbol);
      return false;
     }

   //--- execution options are fixed for the session
   m_opts.symbol   = m_symbol;
   m_opts.slippage = (InpSlippagePoints < 0 ? 0 : InpSlippagePoints);
   m_opts.retries  = (InpOrderRetries < 1 ? 1 : InpOrderRetries);
   m_opts.delayMs  = (InpRetryDelayMs < 0 ? 0 : InpRetryDelayMs);

   m_stops.Init(&m_risk, m_opts);
   m_trail.Init(&m_risk);
   m_pyramid.Init(&m_risk, m_opts);
   m_state.Clear();
   m_bridge.Init(InpBridgeEnable ? m_symbol : "");

   m_ready = true;

   PrintFormat("%s %s ready on %s | point %.5f | min lot %.2f step %.2f | ATR(%d) %s",
               CHARON_NAME, CHARON_VERSION, m_symbol, m_risk.Point(), m_risk.VolMin(),
               m_risk.VolStep(), InpAtrPeriod,
               (m_risk.AtrReady() ? "ready" : "warming up"));
   PrintFormat("%s scope: strict SL | trailing (%s) | RR targets | risk sizing | pyramid %s",
               CHARON_NAME, EnumToString(m_trail.Mode()),
               (InpPyramidEnable ? "enabled" : "disabled"));
   return true;
  }

//+------------------------------------------------------------------+
//| Clean shutdown: release the indicator and the chart panel        |
//+------------------------------------------------------------------+
void CCharonEngine::Deinit(void)
  {
   //--- tell the dashboard the EA is going away, so it can show "offline"
   //--- instead of rendering the last snapshot as if the EA were still live
   if(m_bridge.Enabled())
     {
      SCharonBridgeSnapshot snap;
      ZeroMemory(snap);
      snap.ready      = false;
      snap.symbol     = m_symbol;
      snap.serverTime = TimeCurrent();
      snap.digits     = (m_risk.Digits() > 0 ? m_risk.Digits() : 5);
      snap.currency   = AccountInfoString(ACCOUNT_CURRENCY);
      snap.balance    = AccountInfoDouble(ACCOUNT_BALANCE);
      snap.equity     = AccountInfoDouble(ACCOUNT_EQUITY);
      snap.trailMode  = (int)m_trail.Mode();
      snap.trailText  = EnumToString(m_trail.Mode());
      SCharonBridgeLeg none[];
      m_bridge.Publish(snap, none, 0);
     }

   //--- flush every row before releasing the ATR handle
   m_state.FlushAll();
   m_risk.Deinit();
   Comment("");
   m_ready = false;
  }

//+------------------------------------------------------------------+
//| Should Charon manage this position?                              |
//|  InpMagicFilter == 0 -> every position on the symbol (manual)    |
//|  otherwise only that magic -- PLUS always its own scale-in legs, |
//|  which carry InpMagic. Without that exception a restart would    |
//|  orphan legs Charon itself opened, leaving them unprotected.     |
//+------------------------------------------------------------------+
bool CCharonEngine::WantsTicket(const ulong magic) const
  {
   if(InpMagicFilter == 0)
      return true;
   if((long)magic == InpMagicFilter)
      return true;
   return ((long)magic == (long)InpMagic);
  }

//+------------------------------------------------------------------+
//| Adopt one ticket: arm the strict stop, project the target, seed  |
//| the state row. Called for positions Charon has not seen yet.     |
//+------------------------------------------------------------------+
void CCharonEngine::AdoptTicket(const ulong ticket)
  {
   if(!PositionSelectByTicket(ticket))
      return;

   long   ptype = PositionGetInteger(POSITION_TYPE);
   double entry = PositionGetDouble(POSITION_PRICE_OPEN);
   double lot   = PositionGetDouble(POSITION_VOLUME);
   double liveSl = PositionGetDouble(POSITION_SL);
   datetime otime = (datetime)PositionGetInteger(POSITION_TIME);
   bool   isLong  = ChIsLong(ptype);

   if(entry <= 0.0 || lot <= 0.0)
      return;

   //--- Charon's own pyramid legs carry InpMagic and a "Tn" comment tag
   string comment = PositionGetString(POSITION_COMMENT);
   int tier = 1;
   if(StringFind(comment, " T2") >= 0)
      tier = 2;
   else if(StringFind(comment, " T3") >= 0)
      tier = 3;
   else if(StringFind(comment, " T4") >= 0)
      tier = 4;

   //--- 1) try to restore persisted state (restart safety)
   SCharonPosState restored;
   bool haveState = m_state.Load(ticket, tier, restored);

   //--- 2) determine the strict stop: persisted R > live stop > computed
   double armedSl = 0.0;
   double riskPrice = 0.0;
   double initialSl = 0.0;

   if(haveState && restored.initialSL > 0.0 && restored.riskPrice > 0.0)
     {
      //--- the ORIGINAL stop defines 1R and must survive a restart even if the
      //--- live stop has since been trailed far into profit
      initialSl = restored.initialSL;
      riskPrice = restored.riskPrice;
     }
   else if(MathIsValidNumber(liveSl) && liveSl > 0.0 &&
           (isLong ? (liveSl < entry) : (liveSl > entry)))
     {
      initialSl = liveSl;                      // respect the human's stop as 1R
      riskPrice = MathAbs(entry - liveSl);
     }
   else
     {
      string note = "";
      ENUM_CHARON_STOP_RESULT res = m_stops.EnforceStrictStop(ticket, entry, isLong,
                                                              armedSl, note);
      if(res == CH_STOP_CLOSED)
        {
         m_state.Erase(ticket);
         return;
        }
      if(res == CH_STOP_FAILED && InpVerbose)
         PrintFormat("%s #%I64u adoption: %s", CHARON_NAME, ticket, note);
      initialSl = (armedSl > 0.0 ? armedSl : 0.0);
      if(initialSl > 0.0)
         riskPrice = MathAbs(entry - initialSl);
     }

   //--- 3) risk sanity: an existing stop may exceed the hard ceiling
   if(riskPrice > 0.0)
     {
      double money = m_risk.MoneyAtDistance(riskPrice, lot);
      double cap   = m_risk.MaxAllowedRiskMoney();
      if(cap > 0.0 && money > cap + CHARON_EPS)
        {
         PrintFormat("%s #%I64u carries %.2f risk vs cap %.2f - stop exceeds InpMaxRiskPct, "
                     "position adopted but flagged", CHARON_NAME, ticket, money, cap);
        }
     }

   //--- 4) seed / refresh the row
   int idx = m_state.Add(ticket, ptype, entry, lot, initialSl, riskPrice, otime, tier);
   if(idx < 0)
     {
      PrintFormat("%s: state table full (%d) - #%I64u not tracked",
                  CHARON_NAME, CHARON_MAX_STATES, ticket);
      return;
     }

   if(haveState)
     {
      SCharonPosState row;
      if(m_state.Get(idx, row))
        {
         row.hwm          = MathMax(restored.hwm, row.hwm);
         row.trailActive  = restored.trailActive;
         row.halfRiskDone = restored.halfRiskDone;
         row.beDone       = restored.beDone;
         row.tpApplied    = restored.tpApplied;
         row.stopApplies  = restored.stopApplies;
         row.currentSL    = (restored.currentSL > 0.0 ? restored.currentSL : row.currentSL);
         m_state.Set(idx, row);
        }
     }

   //--- 5) project the RR target once
   if(InpTpUnit != CHARON_TP_NONE && InpApplyTpOnAdopt && riskPrice > 0.0)
     {
      bool configured = false;
      double tp = m_stops.TargetPrice(entry, riskPrice, isLong, configured);
      double liveTp = PositionGetDouble(POSITION_TP);
      bool hasTp = (MathIsValidNumber(liveTp) && liveTp > 0.0);
      if(configured && !hasTp)
        {
         string err = "";
         if(m_stops.ApplyTarget(ticket, initialSl, tp, err))
           {
            PrintFormat("%s #%I64u target %.5f (%.2fR) applied", CHARON_NAME, ticket, tp, InpTpRR);
            SCharonPosState row;
            if(m_state.Get(idx, row))
              {
               row.tpApplied = true;
               m_state.Set(idx, row);
              }
           }
         else if(InpVerbose)
            PrintFormat("%s #%I64u target not applied: %s", CHARON_NAME, ticket, err);
        }
      else if(configured && hasTp)
        {
         SCharonPosState row;
         if(m_state.Get(idx, row))
           {
            row.tpApplied = true;
            m_state.Set(idx, row);
           }
        }
     }

   SCharonPosState row;
   if(m_state.Get(idx, row))
     {
      m_state.Save(idx);
      PrintFormat("%s adopted #%I64u %s %.2f lots @ %.5f | stop %.5f | R %.5f (%.2f %s) | tier %d",
                  CHARON_NAME, ticket, (isLong ? "BUY" : "SELL"), lot, entry,
                  initialSl, riskPrice,
                  m_risk.MoneyAtDistance(riskPrice, lot), AccountInfoString(ACCOUNT_CURRENCY),
                  row.tier);
     }
  }

//+------------------------------------------------------------------+
//| Scan open positions and adopt the ones that belong to Charon     |
//|  Cost is bounded: at most CHARON_SCAN_LIMIT positions per tick.   |
//+------------------------------------------------------------------+
bool CCharonEngine::ScanAndAdopt(void)
  {
   int total = PositionsTotal();
   if(total <= 0)
      return false;

   int scanned = 0;
   for(int i = total - 1; i >= 0 && scanned < CHARON_SCAN_LIMIT; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != m_symbol)
         continue;
      if(!WantsTicket((ulong)PositionGetInteger(POSITION_MAGIC)))
         continue;

      scanned++;
      if(m_state.Find(ticket) < 0)
         AdoptTicket(ticket);
     }
   return (scanned > 0);
  }

//+------------------------------------------------------------------+
//| Per-tick management of one tracked position                      |
//|                                                                  |
//|  Order is deliberate and never reordered:                        |
//|   1. refresh the row from the broker                             |
//|   2. update the high-water mark                                  |
//|   3. ENFORCE THE STRICT STOP (highest priority, may close)       |
//|   4. half-risk / breakeven ladder                                |
//|   5. trailing stop                                               |
//|  Step 5 can never loosen the stop set by step 3: every trailing  |
//|  candidate must pass the improvement gate.                        |
//+------------------------------------------------------------------+
void CCharonEngine::ManageRow(const int index)
  {
   SCharonPosState row;
   if(!m_state.Get(index, row))
      return;

   if(!PositionSelectByTicket(row.ticket))
     {
      m_state.Erase(row.ticket);
      m_state.RemoveAt(index);
      return;
     }

   //--- 1) refresh mutable broker fields
   long   ptype  = PositionGetInteger(POSITION_TYPE);
   double entry  = PositionGetDouble(POSITION_PRICE_OPEN);
   double lot    = PositionGetDouble(POSITION_VOLUME);
   double liveSl = PositionGetDouble(POSITION_SL);
   double liveTp = PositionGetDouble(POSITION_TP);
   bool   isLong = ChIsLong(ptype);

   row.type  = ptype;
   row.entry = entry;
   row.lot   = lot;
   if(MathIsValidNumber(liveSl) && liveSl > 0.0)
      row.currentSL = liveSl;

   //--- 2) high-water mark from the current price
   double price = (isLong ? m_risk.Bid() : m_risk.Ask());
   row.hwm = ChExtreme(row.hwm, price, isLong);
   if(row.riskPrice <= 0.0 && row.initialSL > 0.0)
      row.riskPrice = MathAbs(entry - row.initialSL);

   //--- 3) strict stop enforcement (may flatten the position)
   double armedSl = 0.0;
   string note = "";
   ENUM_CHARON_STOP_RESULT res = m_stops.EnforceStrictStop(row.ticket, entry, isLong,
                                                           armedSl, note);
   if(res == CH_STOP_CLOSED)
     {
      m_state.Erase(row.ticket);
      m_state.RemoveAt(index);
      return;
     }
   if(armedSl > 0.0)
     {
      row.currentSL = armedSl;
      if(row.initialSL <= 0.0 && res == CH_STOP_ATTACHED)
        {
         //--- a stop attached late becomes the R reference
         row.initialSL = armedSl;
         row.riskPrice = MathAbs(entry - armedSl);
        }
      row.stopRejections = 0;
     }
   else if(res == CH_STOP_FAILED)
     {
      row.stopRejections++;
      if(InpVerbose && row.stopRejections <= 3)
         PrintFormat("%s #%I64u stop enforcement: %s", CHARON_NAME, row.ticket, note);
     }

   m_state.Set(index, row);
   ManageLadders(index);
  }

//+------------------------------------------------------------------+
//| Half-risk / breakeven ladder, then the trailing engine.          |
//| Split out from ManageRow so each step stays readable and so the  |
//| strict-stop enforcement above can never be bypassed.             |
//+------------------------------------------------------------------+
void CCharonEngine::ManageLadders(const int index)
  {
   SCharonPosState row;
   if(!m_state.Get(index, row))
      return;
   if(row.riskPrice <= 0.0 || row.currentSL <= 0.0)
      return;

   bool isLong = ChIsLong(row.type);
   double price = (isLong ? m_risk.Bid() : m_risk.Ask());
   if(price <= 0.0)
      return;

   double liveTp = 0.0;
   if(PositionSelectByTicket(row.ticket))
      liveTp = PositionGetDouble(POSITION_TP);

   double progress = ChProgressRR(row.entry, row.riskPrice, price, isLong);

   //--- 4a) R ladder: 0.5R half-risk, then breakeven at 1R
   double ladderStop = 0.0;
   string ladderLabel = "";
   if(m_stops.LadderTarget(row.entry, row.riskPrice, isLong, progress, ladderStop, ladderLabel))
     {
      if(ChStopImproves(row.currentSL, ladderStop, isLong, 0.0))
        {
         string err = "";
         if(m_stops.ApplyStop(row.ticket, ladderStop, liveTp, err))
           {
            row.currentSL = ladderStop;
            row.stopApplies++;
            if(StringFind(ladderLabel, "breakeven") >= 0)
               row.beDone = true;
            if(StringFind(ladderLabel, "half-risk") >= 0)
               row.halfRiskDone = true;
            if(InpVerbose)
               PrintFormat("%s #%I64u stop -> %.5f (%s)", CHARON_NAME, row.ticket,
                           ladderStop, ladderLabel);
            m_state.Set(index, row);
            m_state.Save(index, true);   // a stop change is always persisted
           }
         else if(InpVerbose)
            PrintFormat("%s #%I64u ladder rejected: %s", CHARON_NAME, row.ticket, err);
        }
     }

   //--- 4b) trailing engine (strictly improving moves only)
   if(m_trail.Mode() == CHARON_TRAIL_OFF)
      return;

   double trailStop = 0.0;
   string trailLabel = "";
   if(!m_trail.NextStop(row.entry, row.riskPrice, row.hwm, isLong, row.currentSL,
                        trailStop, trailLabel))
      return;

   string terr = "";
   if(m_stops.ApplyStop(row.ticket, trailStop, liveTp, terr))
     {
      row.currentSL   = trailStop;
      row.trailActive = true;
      row.stopApplies++;
      if(InpVerbose)
         PrintFormat("%s #%I64u trail -> %.5f (%s)", CHARON_NAME, row.ticket,
                     trailStop, trailLabel);
      m_state.Set(index, row);
      m_state.Save(index, true);      // a stop change is always persisted
     }
   else if(InpVerbose)
      PrintFormat("%s #%I64u trail rejected: %s", CHARON_NAME, row.ticket, terr);
  }

//+------------------------------------------------------------------+
//| Aggregate every tracked position into a basket view              |
//+------------------------------------------------------------------+
void CCharonEngine::BuildBasket(void)
  {
   m_basket.valid     = false;
   m_basket.mixed     = false;
   m_basket.dir       = 0;
   m_basket.legs      = 0;
   m_basket.lot       = 0.0;
   m_basket.avgEntry  = 0.0;
   m_basket.openPL    = 0.0;
   m_basket.riskMoney = 0.0;
   m_basket.riskPct   = 0.0;
   m_basket.bestPrice = 0.0;

   double weighted = 0.0;
   double equity   = AccountInfoDouble(ACCOUNT_EQUITY);
   long   dir      = -1;              // -1 = not yet set, -2 = conflicting

   for(int i = 0; i < m_state.Count(); i++)
     {
      SCharonPosState row;
      if(!m_state.Get(i, row))
         continue;
      if(!PositionSelectByTicket(row.ticket))
         continue;

      long ptype = PositionGetInteger(POSITION_TYPE);
      double lot = PositionGetDouble(POSITION_VOLUME);
      double entry = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl  = PositionGetDouble(POSITION_SL);
      double pl  = PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);

      if(lot <= 0.0 || entry <= 0.0)
         continue;

      //--- a basket is one-sided by definition; track conflicts explicitly
      //--- (POSITION_TYPE_BUY is 0, so a sentinel of 0 would be ambiguous)
      if(dir == -1)
         dir = ptype;
      else if(dir != -2 && dir != ptype)
         dir = -2;

      m_basket.legs++;
      m_basket.lot += lot;
      weighted     += entry * lot;
      m_basket.openPL += pl;

      //--- risk at the live stop, counted only when the stop is adverse
      if(sl > 0.0)
        {
         bool isLong = ChIsLong(ptype);
         double adverse = (isLong ? (entry - sl) : (sl - entry));
         if(adverse > 0.0)
            m_basket.riskMoney += m_risk.MoneyAtDistance(adverse, lot);
        }

      //--- basket extreme: highest high for longs, lowest low for shorts
      bool isLongBest = ChIsLong(ptype);
      m_basket.bestPrice = ChExtreme(m_basket.bestPrice,
                                     (isLongBest ? m_risk.Bid() : m_risk.Ask()),
                                     isLongBest);
     }

   if(m_basket.legs <= 0)
      return;

   m_basket.valid    = true;
   m_basket.mixed    = (dir == -2);
   //--- POSITION_TYPE_BUY is 0, so an unset dir on an empty-ish basket is fine
   m_basket.dir      = (m_basket.mixed ? 0 : (dir < 0 ? 0 : dir));
   m_basket.avgEntry = (m_basket.lot > 0.0 ? weighted / m_basket.lot : 0.0);
   m_basket.riskPct  = (equity > 0.0 ? m_basket.riskMoney / equity * 100.0 : 0.0);
  }

//+------------------------------------------------------------------+
//| Escalate the pyramid ladder when the basket qualifies            |
//+------------------------------------------------------------------+
void CCharonEngine::MaybePyramid(void)
  {
   if(!InpPyramidEnable)
      return;
   if(!m_basket.valid)
      return;
   if(m_basket.mixed)
     {
      if(InpVerbose && m_basket.legs > 1)
         PrintFormat("%s pyramid skipped: basket has mixed directions", CHARON_NAME);
      return;
     }

   //--- R reference: the primary leg's original risk, not the average
   double refRiskPrice = BasketRefRisk();
   if(refRiskPrice <= 0.0)
      return;

   bool isLong = ChIsLong(m_basket.dir);

   //--- progress measured from the basket's weighted average entry
   double progress = ChProgressRR(m_basket.avgEntry, refRiskPrice,
                                  (isLong ? m_risk.Bid() : m_risk.Ask()), isLong);

   //--- next tier = highest tier already used + 1, so manual extra primaries
   //--- never masquerade as tier 2/3
   int highestTier = 1;
   for(int t = 0; t < m_state.Count(); t++)
     {
      SCharonPosState r;
      if(m_state.Get(t, r) && r.tier > highestTier)
         highestTier = r.tier;
     }
   int nextTier = highestTier + 1;

   SCharonTierSpec spec;
   double projected = m_basket.riskMoney;
   if(ChTierSpec(nextTier, spec))
     {
      bool sized = false;
      double probeLot = m_risk.LotForStop(MathMax(refRiskPrice, m_risk.StopDistanceWithBuffer()),
                                          sized, true, spec.lotMultiplier);
      if(sized && probeLot > 0.0)
         projected += m_risk.MoneyAtDistance(refRiskPrice, probeLot);
     }

   double base = m_risk.AccountBase();
   //--- direction as a signed integer: POSITION_TYPE_BUY is 0, so passing the
   //--- raw type would be indistinguishable from "no direction".
   int dirSign = (m_basket.dir == (long)POSITION_TYPE_BUY ? 1 : -1);
   ENUM_CHARON_TIER_DECISION decision = m_pyramid.Evaluate(m_basket.legs, nextTier, dirSign,
                                                           progress, projected, base);
   if(decision != CH_TIER_READY)
     {
      if(InpVerbose && decision != CH_TIER_NOTRIGGER && decision != CH_TIER_DISABLED
         && decision != CH_TIER_NOTARMED)
         PrintFormat("%s pyramid tier %d held: %s (progress %.2fR, basket risk %.2f%%)",
                     CHARON_NAME, nextTier, EnumToString(decision), progress, m_basket.riskPct);
      return;
     }

   //--- optional collective breakeven before adding exposure
   if(InpPyramidCollectiveBE)
      MoveBasketToBreakeven();

   ulong ticket = 0;
   double fill = 0.0;
   string note = "";
   ENUM_CHARON_TIER_DECISION sent = m_pyramid.OpenLeg(nextTier,
                                                      (ENUM_POSITION_TYPE)m_basket.dir,
                                                      refRiskPrice,
                                                      m_basket.riskMoney, base,
                                                      ticket, fill, note);
   if(sent == CH_TIER_SEND_OK && ticket > 0)
     {
      //--- a manual arm authorises exactly one tier: consume it
      m_pyramid.ClearArm();
      //--- res.order is the ORDER ticket, which is not guaranteed to equal the
      //--- position ticket, so re-scan rather than adopting by ticket. The leg
      //--- is already protected: its strict stop was attached in the same
      //--- OrderSend request, so there is no unprotected window here.
      ScanAndAdopt();
      return;
     }

   if(sent != CH_TIER_SEND_OK)
      PrintFormat("%s pyramid tier %d refused: %s", CHARON_NAME, nextTier, note);
  }

//+------------------------------------------------------------------+
//| Move every leg of the basket to (at least) its own entry price.  |
//| Used before a scale-in so the whole basket stops losing money on |
//| the original exposure. Never loosens an existing stop.           |
//+------------------------------------------------------------------+
void CCharonEngine::MoveBasketToBreakeven(void)
  {
   for(int i = 0; i < m_state.Count(); i++)
     {
      SCharonPosState row;
      if(!m_state.Get(i, row))
         continue;
      if(!PositionSelectByTicket(row.ticket))
         continue;

      long   ptype = PositionGetInteger(POSITION_TYPE);
      double entry = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl    = PositionGetDouble(POSITION_SL);
      double tp    = PositionGetDouble(POSITION_TP);
      bool   isLong = ChIsLong(ptype);

      if(entry <= 0.0)
         continue;

      //--- already at or beyond breakeven
      if(sl > 0.0 && (isLong ? (sl >= entry - CHARON_EPS) : (sl <= entry + CHARON_EPS)))
         continue;

      //--- breakeven must sit behind the market or the broker rejects it
      double be = m_risk.NormalizePrice(entry);
      double buffer = m_risk.StopDistanceWithBuffer();
      double bid = m_risk.Bid();
      double ask = m_risk.Ask();
      if(buffer <= 0.0 || bid <= 0.0 || ask <= 0.0)
         continue;
      if(isLong && be > bid - buffer)
         continue;                     // price has not cleared entry yet
      if(!isLong && be < ask + buffer)
         continue;

      string err = "";
      if(!m_stops.ApplyStop(row.ticket, be, tp, err))
        {
         if(InpVerbose)
            PrintFormat("%s #%I64u collective BE refused: %s", CHARON_NAME, row.ticket, err);
         continue;
        }

      row.currentSL = be;
      row.beDone    = true;
      row.stopApplies++;
      m_state.Set(i, row);
      m_state.Save(i);
      if(InpVerbose)
         PrintFormat("%s #%I64u collective breakeven -> %.5f", CHARON_NAME, row.ticket, be);
     }
  }

//+------------------------------------------------------------------+
//| R reference for the basket: the PRIMARY leg's original stop      |
//|                                                                  |
//|  Deliberately not the average, so a scale-in can never dilute the |
//|  R multiple the ladder and the tier triggers are measured in.     |
//|  Returns 0.0 when no primary leg carries a usable risk.           |
//+------------------------------------------------------------------+
double CCharonEngine::BasketRefRisk(void) const
  {
   for(int i = 0; i < m_state.Count(); i++)
     {
      SCharonPosState row;
      if(!m_state.Get(i, row))
         continue;
      if(row.tier == 1 && row.riskPrice > 0.0)
         return row.riskPrice;
     }
   return 0.0;
  }

//+------------------------------------------------------------------+
//| Format the configured R ladder for one entry/R pair              |
//|                                                                  |
//|  Prices come from CCharonStops::RPrice() so the panel shows the   |
//|  same normalised values a broker would accept, not raw floats.    |
//|  Returns "" when no rung is usable.                              |
//+------------------------------------------------------------------+
string CCharonEngine::RrLadderText(const double entry, const double riskPrice,
                                   const bool isLong)
  {
   double prices[3];
   int count = ChRrLadderPrices(entry, riskPrice, isLong,
                                InpRrLadderA, InpRrLadderB, InpRrLadderC, prices);
   if(count <= 0)
      return "";

   double levels[3];
   levels[0] = InpRrLadderA;
   levels[1] = InpRrLadderB;
   levels[2] = InpRrLadderC;

   //--- rebuild the level list in the same order ChRrLadderPrices used
   string text = "";
   int written = 0;
   for(int i = 0; i < 3 && written < count; i++)
     {
      if(!MathIsValidNumber(levels[i]) || levels[i] <= 0.0)
         continue;
      double shown = m_stops.RPrice(entry, riskPrice, levels[i], isLong);
      if(shown <= 0.0)
         continue;
      if(written > 0)
         text += " | ";
      text += StringFormat("%.2fR %s", levels[i], DoubleToString(shown, m_risk.Digits()));
      written++;
     }
   return text;
  }

//+------------------------------------------------------------------+
//| On-chart status panel (chart comment, no objects to clean up)    |
//+------------------------------------------------------------------+
void CCharonEngine::DrawDashboard(void)
  {
   if(!InpShowDashboard)
     {
      Comment("");
      return;
     }

   string slUnit = StringFormat("%.0f points", InpSlPoints);
   if(InpSlUnit == CHARON_SL_ATR)
      slUnit = StringFormat("ATR x%.2f", InpSlAtrMult);
   else if(InpSlUnit == CHARON_SL_PRICE_PCT)
      slUnit = StringFormat("%.2f%% of price", InpSlPricePct);

   string tpText = "no target";
   if(InpTpUnit == CHARON_TP_RR)
      tpText = StringFormat("%.2fR", InpTpRR);
   else if(InpTpUnit == CHARON_TP_POINTS)
      tpText = StringFormat("%.0f points", InpTpPoints);

   string sizing = StringFormat("%.2f lots", InpFixedLots);
   if(InpLotMode == CHARON_LOT_RISK_PCT)
      sizing = StringFormat("%.2f%% of %s", InpRiskPct, (InpUseEquity ? "equity" : "balance"));
   else if(InpLotMode == CHARON_LOT_FIXED_MONEY)
      sizing = StringFormat("%.2f %s", InpRiskMoney, AccountInfoString(ACCOUNT_CURRENCY));

   double atr = m_risk.Atr();

   string text = "";
   text += StringFormat("%s %s  |  %s  |  %s\n",
                        CHARON_NAME, CHARON_VERSION, m_symbol,
                        (m_ready ? "ACTIVE" : "NOT READY"));
   text += "------------------------------------------------------------\n";
   text += StringFormat("Strict SL   : %s  (broker min %.0f pts + %.1f buffer)\n",
                        slUnit, ChPointsFromPrice(m_risk.StopsLevel(), m_risk.Point()),
                        InpStrictStopBufferPts);
   text += StringFormat("RR target   : %s\n", tpText);
   text += StringFormat("Sizing      : %s  |  cap %.2f%%\n", sizing, InpMaxRiskPct);
   text += StringFormat("Trailing    : %s\n", EnumToString(m_trail.Mode()));
   string pyrTrig = (InpPyramidTrigger == CHARON_PYR_MANUAL ? "MANUAL" : "AUTO");
   if(InpPyramidTrigger == CHARON_PYR_MANUAL)
     {
      string armState = "disarmed";
      if(m_pyramid.IsArmed())
        {
         if(ChArmIsLive(true, m_pyramid.ArmedAt(), TimeCurrent(), InpPyramidArmTimeoutSec))
           {
            long left = (long)InpPyramidArmTimeoutSec
                        - (long)(TimeCurrent() - m_pyramid.ArmedAt());
            armState = (InpPyramidArmTimeoutSec > 0
                        ? StringFormat("ARMED %ds left", (int)left) : "ARMED");
           }
         else
            armState = "expired";
        }
      if(InpPyramidArmKey > 0)
         pyrTrig += StringFormat(" [%s = arm, %s]", ShortToString((ushort)InpPyramidArmKey), armState);
      else
         pyrTrig += StringFormat(" [arm key off, %s]", armState);
     }
   text += StringFormat("Pyramiding  : %s  (max %d legs, cap %.2f%%)\n",
                        (InpPyramidEnable ? "ON" : "OFF"),
                        ChClampI(InpPyramidMaxLegs, 1, CHARON_MAX_TIERS),
                        InpPyramidMaxRiskPct);
   text += StringFormat("Pyramid trig: %s\n", pyrTrig);
   text += StringFormat("ATR(%d)      : %s\n", InpAtrPeriod,
                        (atr > 0.0 ? DoubleToString(atr, m_risk.Digits()) : "warming up"));
   text += "------------------------------------------------------------\n";

   if(!m_basket.valid)
     {
      text += "No managed positions on this symbol.\n";
      text += "Open a trade manually - Charon adopts it and arms the stop.\n";
     }
   else
     {
      text += StringFormat("Basket      : %s  %d leg(s)  %.2f lots\n",
                           (m_basket.mixed ? "MIXED"
                            : (m_basket.dir == (long)POSITION_TYPE_BUY ? "LONG" : "SHORT")),
                           m_basket.legs, m_basket.lot);
      text += StringFormat("Avg entry   : %s\n",
                           DoubleToString(m_basket.avgEntry, m_risk.Digits()));
      //--- the R ladder is projected from the basket's average entry, so the
      //--- rung prices match what the aggregate position actually needs
      if(!m_basket.mixed)
        {
         string ladder = RrLadderText(m_basket.avgEntry, BasketRefRisk(),
                                      ChIsLong(m_basket.dir));
         if(ladder != "")
            text += StringFormat("RR ladder   : %s\n", ladder);
        }
      text += StringFormat("Open P/L    : %.2f %s\n", m_basket.openPL,
                           AccountInfoString(ACCOUNT_CURRENCY));
      text += StringFormat("Risk now    : %.2f %s (%.2f%% of equity)\n",
                           m_basket.riskMoney, AccountInfoString(ACCOUNT_CURRENCY),
                           m_basket.riskPct);
     }

   text += "------------------------------------------------------------\n";
   text += "TICKETS\n";
   if(m_state.Count() == 0)
      text += "  (none)\n";

   for(int i = 0; i < m_state.Count() && i < 10; i++)
     {
      SCharonPosState row;
      if(!m_state.Get(i, row))
         continue;
      if(!PositionSelectByTicket(row.ticket))
         continue;

      bool   isLong = ChIsLong(row.type);
      double price  = (isLong ? m_risk.Bid() : m_risk.Ask());
      double rr     = ChProgressRR(row.entry, row.riskPrice, price, isLong);
      double sl     = PositionGetDouble(POSITION_SL);
      text += StringFormat("  #%I64u %s T%d %.2f lots | R %.0f pts | %.2fR | SL %s\n",
                           row.ticket, (isLong ? "BUY " : "SELL"), row.tier, row.lot,
                           ChPointsFromPrice(row.riskPrice, m_risk.Point()), rr,
                           (sl > 0.0 ? DoubleToString(sl, m_risk.Digits()) : "NONE"));
      //--- each leg gets its own ladder, projected from its own entry and its
      //--- own R, so a tier's targets never inherit the primary's geometry
      if(row.riskPrice > 0.0 && row.entry > 0.0)
        {
         string legLadder = RrLadderText(row.entry, row.riskPrice, isLong);
         if(legLadder != "")
            text += StringFormat("       -> %s\n", legLadder);
        }
     }
   if(m_state.Count() > 10)
      text += StringFormat("  ... and %d more\n", m_state.Count() - 10);

   Comment(text);
  }

//+------------------------------------------------------------------+
//| Main per-tick pipeline                                           |
//+------------------------------------------------------------------+
void CCharonEngine::OnTickWork(void)
  {
   if(!m_ready || m_busy)
      return;
   if(!m_risk.Refresh())
      return;

   //--- OnTradeTransaction can fire while an OrderSend is in flight, so the
   //--- pipeline must be non-reentrant: a nested pass would act on half-written
   //--- state and could double-send a stop modify or a pyramid leg.
   m_busy = true;

   //--- 1) adopt anything new the human just opened
   ScanAndAdopt();

   //--- 2) manage every tracked position (backwards: rows get removed)
   for(int i = m_state.Count() - 1; i >= 0; i--)
      ManageRow(i);

   //--- 3) drop rows whose positions are gone
   m_state.Compact();

   //--- 4) refresh the basket view and consider a scale-in
   BuildBasket();
   MaybePyramid();

   //--- 5) redraw
   DrawDashboard();

   m_busy = false;
  }

//+------------------------------------------------------------------+
//| Coarse heartbeat: works without ticks (weekend gaps, thin        |
//| symbols, or a position opened while no ticks arrive)             |
//+------------------------------------------------------------------+
void CCharonEngine::OnTimerWork(void)
  {
   if(!m_ready || m_busy)
      return;
   if(!m_risk.Refresh())
      return;

   m_busy = true;
   ScanAndAdopt();
   for(int i = m_state.Count() - 1; i >= 0; i--)
      ManageRow(i);
   m_state.Compact();
   BuildBasket();

   //--- the bridge is serviced on the timer only, never inside the tick
   //--- pipeline: the dashboard is a convenience and must never sit between
   //--- a tick and a stop modification.
   ServiceBridge();

   DrawDashboard();
   m_busy = false;
  }

//+------------------------------------------------------------------+
//| Keyboard: arm the next pyramid tier when the trigger is MANUAL   |
//|                                                                  |
//|  OnChartEvent only delivers CHARTEVENT_KEYDOWN while the chart    |
//|  owns focus, which is an MQL5 constraint rather than a design     |
//|  choice. The dashboard shows the armed state so it is always      |
//|  visible whether the press landed.                                |
//+------------------------------------------------------------------+
void CCharonEngine::OnKey(const long key)
  {
   if(!m_ready)
      return;
   if(InpPyramidTrigger != CHARON_PYR_MANUAL)
      return;                          // AUTO never needs an arm
   if(InpPyramidArmKey <= 0)
      return;                          // arming disabled

   //--- lparam carries the virtual-key code
   if(key != (long)InpPyramidArmKey)
      return;

   m_pyramid.Arm();
   PrintFormat("%s pyramid trigger armed for the next tier (expires in %s)",
               CHARON_NAME,
               (InpPyramidArmTimeoutSec > 0
                ? StringFormat("%ds", InpPyramidArmTimeoutSec) : "never"));
   DrawDashboard();
  }

//+------------------------------------------------------------------+
//| Drain the command inbox, then publish a fresh snapshot           |
//|                                                                  |
//|  Order matters. Commands are applied first so the state the       |
//|  dashboard receives on this tick already reflects them; otherwise |
//|  the panel would show "ARMED" a full second after the click.      |
//|  Both halves are cheap: Poll() reads at most one small file,      |
//|  Publish() writes one small file.                                 |
//+------------------------------------------------------------------+
void CCharonEngine::ServiceBridge(void)
  {
   if(!m_bridge.Enabled())
      return;

   //--- a bounded drain so a flood of files can never delay a tick
   for(int guard = 0; guard < 8; guard++)
     {
      SCharonBridgeCommand cmd;
      if(!m_bridge.Poll(cmd))
         break;                      // inbox empty, or the file was refused
      ApplyBridgeCommand(cmd);
     }

   PublishBridge();
  }

//+------------------------------------------------------------------+
//| Translate one validated command into existing engine state        |
//|                                                                  |
//|  Every branch calls a method that already existed before the      |
//|  bridge did, so the bridge adds a remote input device and no new   |
//|  capability. The order_* verbs delegate to ApplyOrderCommand(),    |
//|  which is the one place a ticket can reach CharonTrade.mqh -- and  |
//|  only after the EA's own risk model has adjudicated it.           |
//+------------------------------------------------------------------+
bool CCharonEngine::ApplyBridgeCommand(const SCharonBridgeCommand &cmd)
  {
   if(!cmd.valid)
      return false;

   if(cmd.verb == CHARON_BRIDGE_V_ARM)
     {
      //--- identical rules to OnKey(): an arm only means anything when the
      //--- trigger is MANUAL. Reporting the refusal keeps the log honest
      //--- instead of silently pretending the arm took effect.
      if(InpPyramidTrigger != CHARON_PYR_MANUAL)
        {
         PrintFormat("%s bridge: arm ignored - pyramid trigger is AUTO", CHARON_NAME);
         return false;
        }
      if(!InpPyramidEnable)
        {
         PrintFormat("%s bridge: arm ignored - pyramiding is disabled", CHARON_NAME);
         return false;
        }
      if(!m_basket.valid)
        {
         PrintFormat("%s bridge: arm ignored - no basket on %s", CHARON_NAME, m_symbol);
         return false;
        }
      m_pyramid.Arm();
      PrintFormat("%s bridge: pyramid trigger armed via dashboard (expires in %s)",
                  CHARON_NAME,
                  (InpPyramidArmTimeoutSec > 0
                   ? StringFormat("%ds", InpPyramidArmTimeoutSec) : "never"));
      return true;
     }

   if(cmd.verb == CHARON_BRIDGE_V_DISARM)
     {
      m_pyramid.ClearArm();
      PrintFormat("%s bridge: pyramid trigger disarmed via dashboard", CHARON_NAME);
      return true;
     }

   if(cmd.verb == CHARON_BRIDGE_V_TRAILMODE)
     {
      int mode = (int)StringToInteger(cmd.arg);
      if(!CCharonBridge::TrailModeValid(mode))
         return false;
      m_trail.SetMode((ENUM_CHARON_TRAIL_MODE)mode);
      PrintFormat("%s bridge: trailing mode -> %s", CHARON_NAME,
                  EnumToString(m_trail.Mode()));
      return true;
     }

   if(cmd.verb == CHARON_BRIDGE_V_PING)
      return true;                    // liveness probe, no state change

   //--- attended execution. The ticket carries intent only: the side, the
   //--- block the stop is derived from, and the risk the operator is willing
   //--- to carry. Everything else -- whether it is allowed, how large it is,
   //--- where the stop sits -- is decided here, by the same CCharonRisk calls
   //--- the rest of the engine uses.
   if(CCharonBridge::OrderVerb(cmd.verb))
      return ApplyOrderCommand(cmd);

   return false;
  }
//+------------------------------------------------------------------+
//| Record the verdict of the last attended ticket                   |
//|  Every field is what the EA actually used or produced, so the     |
//|  dashboard never has to derive a lot, a stop or an R multiple.    |
//+------------------------------------------------------------------+
void CCharonEngine::SetOrderVerdict(const string status, const string reason,
                                    const string side, const double lots,
                                    const double entry, const double stop,
                                    const double riskMoney, const double riskPct,
                                    const int rung, const ulong ticket)
  {
   m_orderStatus    = status;
   m_orderReason    = reason;
   m_orderSide      = side;
   m_orderLots      = lots;
   m_orderEntry     = entry;
   m_orderStop      = stop;
   m_orderRiskMoney = riskMoney;
   m_orderRiskPct   = riskPct;
   m_orderRung      = rung;
   m_orderTicket    = ticket;
  }

//+------------------------------------------------------------------+
//| Take-profit target, derived exactly as adoption and pyramid do   |
//|  Returns 0.0 when the operator configured "no target", which is   |
//|  the default: Charon never invents a TP the settings did not ask  |
//|  for.                                                            |
//+------------------------------------------------------------------+
double CCharonEngine::OrderTarget(const double entry, const double distance,
                                  const bool isLong)
  {
   if(!MathIsValidNumber(entry) || entry <= 0.0)
      return 0.0;
   if(!MathIsValidNumber(distance) || distance <= 0.0)
      return 0.0;

   double tp = 0.0;
   if(InpTpUnit == CHARON_TP_RR && InpTpRR > 0.0)
      tp = ChPriceAtRR(entry, distance, InpTpRR, isLong);
   else if(InpTpUnit == CHARON_TP_POINTS && InpTpPoints > 0.0)
     {
      double tdist = ChPriceFromPoints(InpTpPoints, m_risk.Point());
      tp = (isLong ? entry + tdist : entry - tdist);
     }

   if(!MathIsValidNumber(tp) || tp <= 0.0)
      return 0.0;
   return m_risk.NormalizePrice(tp);
  }

//+------------------------------------------------------------------+
//| Is there already a pending order Charon should manage?           |
//|  One basket per symbol is the whole state model, so a second     |
//|  pending ticket would create an exposure the panel cannot show.  |
//+------------------------------------------------------------------+
bool CCharonEngine::HasOwnPending(void)
  {
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0)
         continue;
      if(!OrderSelect(ticket))
         continue;
      if(OrderGetString(ORDER_SYMBOL) != m_symbol)
         continue;
      if(WantsTicket((ulong)OrderGetInteger(ORDER_MAGIC)))
         return true;
     }
   return false;
  }

//+------------------------------------------------------------------+
//| Adjudicate and execute one attended order ticket                 |
//|                                                                  |
//|  The ordering of these checks is the design: every refusal is a   |
//|  reason the operator can read, and no check is a "we'll fix it    |
//|  later". A ticket either satisfies the same constraints the rest  |
//|  of the engine enforces, or it is rejected with a verdict that    |
//|  shows up in the dashboard.                                      |
//|                                                                  |
//|  Three invariants hold on every path through this function:       |
//|    * a position is never opened without a strict stop attached in |
//|      the same OrderSend request;                                 |
//|    * the volume is solved by CCharonRisk from the block height    |
//|      and the ticket's own budget -- never supplied by the caller; |
//|    * the risk is re-verified against InpMaxRiskPct after sizing,  |
//|      so a rounding edge or the min-lot floor cannot exceed it.    |
//+------------------------------------------------------------------+
bool CCharonEngine::ApplyOrderCommand(const SCharonBridgeCommand &cmd)
  {
   const bool   isLong = (cmd.side == "BUY");
   const string side   = cmd.side;

   //--- 1) cancel: remove a pending ticket, or flatten a position
   if(cmd.verb == CHARON_BRIDGE_V_ORDER_CANCEL)
     {
      string err = "";
      if(OrderSelect(cmd.ticket))
        {
         if(!ChExecCancelPending(m_opts, cmd.ticket, err))
           {
            SetOrderVerdict("rejected", "cancel refused: " + err, "", 0.0, 0.0, 0.0,
                            0.0, 0.0, 0, cmd.ticket);
            PrintFormat("%s bridge: cancel #%I64u failed - %s", CHARON_NAME, cmd.ticket, err);
            return false;
           }
         SetOrderVerdict("accepted", StringFormat("pending #%I64u cancelled", cmd.ticket),
                         "", 0.0, 0.0, 0.0, 0.0, 0.0, 0, cmd.ticket);
         PrintFormat("%s bridge: pending #%I64u cancelled via dashboard", CHARON_NAME, cmd.ticket);
         return true;
        }

      if(PositionSelectByTicket(cmd.ticket))
        {
         if(!ChExecClosePosition(m_opts, cmd.ticket, 0.0, err))
           {
            SetOrderVerdict("rejected", "close refused: " + err, "", 0.0, 0.0, 0.0,
                            0.0, 0.0, 0, cmd.ticket);
            PrintFormat("%s bridge: close #%I64u failed - %s", CHARON_NAME, cmd.ticket, err);
            return false;
           }
         m_state.Erase(cmd.ticket);
         SetOrderVerdict("accepted", StringFormat("position #%I64u closed", cmd.ticket),
                         "", 0.0, 0.0, 0.0, 0.0, 0.0, 0, cmd.ticket);
         PrintFormat("%s bridge: position #%I64u closed via dashboard", CHARON_NAME, cmd.ticket);
         return true;
        }

      SetOrderVerdict("rejected", "no pending order or position with that ticket",
                      "", 0.0, 0.0, 0.0, 0.0, 0.0, 0, cmd.ticket);
      return false;
     }

   //--- 2) the account must be usable before any risk arithmetic happens
   double base = m_risk.AccountBase();
   if(base <= 0.0)
     {
      SetOrderVerdict("rejected", "account base is unavailable", side,
                      0.0, 0.0, 0.0, 0.0, 0.0, cmd.rung, 0);
      return false;
     }

   //--- 3) block geometry -> stop distance. The block is the structure the
   //--- operator is trading against; the stop sits just beyond it, plus the
   //--- same buffer the strict-stop path uses.
   double buffer      = m_risk.StopDistanceWithBuffer();
   double blockHeight = MathAbs(cmd.blockTop - cmd.blockBottom);
   double distance    = blockHeight + buffer;
   if(!MathIsValidNumber(distance) || distance <= 0.0)
     {
      SetOrderVerdict("rejected", "block has no usable height", side,
                      0.0, 0.0, 0.0, 0.0, 0.0, cmd.rung, 0);
      return false;
     }
   if(distance < m_risk.MinStopDistance())
     {
      SetOrderVerdict("rejected",
                      StringFormat("block is tighter than the broker stop level (%.5f < %.5f)",
                                   distance, m_risk.MinStopDistance()),
                      side, 0.0, 0.0, 0.0, 0.0, 0.0, cmd.rung, 0);
      return false;
     }

   //--- 4) entry reference: the requested price, or the market side
   double entry = cmd.entry;
   if(cmd.orderType == "MARKET" || entry <= 0.0)
      entry = (isLong ? m_risk.Ask() : m_risk.Bid());
   entry = m_risk.NormalizePrice(entry);
   if(entry <= 0.0)
     {
      SetOrderVerdict("rejected", "no usable price for this symbol", side,
                      0.0, 0.0, 0.0, 0.0, 0.0, cmd.rung, 0);
      return false;
     }

   //--- the stop must sit on the far side of the block from the entry
   double stop = (isLong ? MathMin(cmd.blockBottom, entry) - buffer
                         : MathMax(cmd.blockTop, entry) + buffer);
   stop = m_risk.NormalizePrice(stop);
   if(stop <= 0.0 || (isLong ? stop >= entry : stop <= entry))
     {
      SetOrderVerdict("rejected", "block does not place a stop beyond the entry",
                      side, 0.0, 0.0, 0.0, 0.0, 0.0, cmd.rung, 0);
      return false;
     }

   //--- re-measure from the normalized prices: rounding can move the
   //--- distance by a fraction of a point, and the risk below must describe
   //--- the stop that will actually be sent
   distance = MathAbs(entry - stop);
   if(distance < m_risk.MinStopDistance())
     {
      SetOrderVerdict("rejected",
                      StringFormat("normalized stop is inside the broker minimum (%.5f)",
                                   distance),
                      side, 0.0, 0.0, 0.0, 0.0, 0.0, cmd.rung, 0);
      return false;
     }
   //--- 5) the ticket's own budget, clamped by the operator's hard ceiling.
   //--- A ticket may ask for less than InpMaxRiskPct but never for more.
   double wanted = cmd.riskPct;
   if(wanted > InpMaxRiskPct)
      wanted = InpMaxRiskPct;
   double budget = base * wanted / 100.0;
   if(budget <= 0.0)
     {
      SetOrderVerdict("rejected", "risk budget resolves to zero", side,
                      0.0, entry, stop, 0.0, 0.0, cmd.rung, 0);
      return false;
     }

   //--- 6) size it. LotForBudget enforces every LotForStop rule plus the
   //--- min-lot floor against the hard ceiling.
   bool   sized = false;
   double lot   = m_risk.LotForBudget(distance, budget, sized);
   if(!sized || lot <= 0.0)
     {
      SetOrderVerdict("rejected",
                      StringFormat("no volume fits %.2f risk over %.5f", budget, distance),
                      side, 0.0, entry, stop, 0.0, 0.0, cmd.rung, 0);
      return false;
     }

   //--- 7) verify the applied risk, not the rounded intent
   double riskMoney = m_risk.MoneyAtDistance(distance, lot);
   double cap       = m_risk.MaxAllowedRiskMoney();
   if(cap > 0.0 && riskMoney > cap + CHARON_EPS)
     {
      SetOrderVerdict("rejected",
                      StringFormat("solved risk %.2f exceeds the %.2f cap", riskMoney, cap),
                      side, lot, entry, stop, riskMoney, 0.0, cmd.rung, 0);
      return false;
     }

   const double riskPct = (riskMoney / base * 100.0);

   //--- 8) one basket per symbol, and never against the existing direction
   if(m_basket.valid && !m_basket.mixed && m_basket.legs > 0)
     {
      bool basketLong = ChIsLong(m_basket.dir);
      if(basketLong != isLong)
        {
         SetOrderVerdict("rejected",
                         StringFormat("basket on %s is already %s", m_symbol,
                                      (basketLong ? "LONG" : "SHORT")),
                         side, lot, entry, stop, riskMoney, riskPct, cmd.rung, 0);
         return false;
        }
     }

   //--- 9) order_modify: re-derive the stop on an existing position
   if(cmd.verb == CHARON_BRIDGE_V_ORDER_MODIFY)
     {
      if(!PositionSelectByTicket(cmd.ticket))
        {
         SetOrderVerdict("rejected", "position no longer exists", side, lot, entry, stop,
                         riskMoney, riskPct, cmd.rung, cmd.ticket);
         return false;
        }
      if(ChIsLong(PositionGetInteger(POSITION_TYPE)) != isLong)
        {
         SetOrderVerdict("rejected", "ticket direction does not match the ticket side",
                         side, lot, entry, stop, riskMoney, riskPct, cmd.rung, cmd.ticket);
         return false;
        }

      string err = "";
      if(!ChExecModifyStops(m_opts, cmd.ticket, stop, 0.0, err))
        {
         SetOrderVerdict("rejected", "modify refused: " + err, side, lot, entry, stop,
                         riskMoney, riskPct, cmd.rung, cmd.ticket);
         PrintFormat("%s bridge: modify #%I64u failed - %s", CHARON_NAME, cmd.ticket, err);
         return false;
        }

      //--- keep 1R anchored at the new stop so the ladder stays honest.
      //--- GetByTicket hands back a COPY, so the row has to be written back
      //--- through Set() before Save() -- Save() persists from the table,
      //--- not from this local.
      SCharonPosState row;
      int rowIndex = m_state.Find(cmd.ticket);
      if(rowIndex >= 0 && m_state.GetByTicket(cmd.ticket, row))
        {
         row.initialSL = stop;
         row.riskPrice = distance;
         if(m_state.Set(rowIndex, row))
            m_state.Save(rowIndex, true);
        }

      SetOrderVerdict("accepted", "stop re-derived from the new block", side, lot, entry,
                      stop, riskMoney, riskPct, cmd.rung, cmd.ticket);
      PrintFormat("%s bridge: #%I64u stop -> %.5f (%.2f risk, rung %d)", CHARON_NAME,
                  cmd.ticket, stop, riskMoney, cmd.rung);
      return true;
     }
   //--- 10) order_open. A LIMIT ticket becomes a pending order with its stop
   //--- attached; a MARKET ticket becomes a position with its stop attached.
   //--- Neither path can produce an unprotected fill.
   if(cmd.orderType == "LIMIT")
     {
      double ask = m_risk.Ask();
      double bid = m_risk.Bid();
      if(isLong && ask > 0.0 && entry >= ask)
        {
         SetOrderVerdict("rejected",
                         StringFormat("a BUY LIMIT must sit below the ask (%.5f)", ask),
                         side, lot, entry, stop, riskMoney, riskPct, cmd.rung, 0);
         return false;
        }
      if(!isLong && bid > 0.0 && entry <= bid)
        {
         SetOrderVerdict("rejected",
                         StringFormat("a SELL LIMIT must sit above the bid (%.5f)", bid),
                         side, lot, entry, stop, riskMoney, riskPct, cmd.rung, 0);
         return false;
        }
      if(HasOwnPending())
        {
         SetOrderVerdict("rejected", "a Charon pending order is already working on this symbol",
                         side, lot, entry, stop, riskMoney, riskPct, cmd.rung, 0);
         return false;
        }

      ulong  ticket  = 0;
      string err     = "";
      string comment = StringFormat("%s L%d", CHARON_COMMENT, cmd.rung);
      if(!ChExecOpenPending(m_opts, (isLong ? POSITION_TYPE_BUY : POSITION_TYPE_SELL),
                            lot, entry, stop, InpMagic, comment, ticket, err))
        {
         SetOrderVerdict("rejected", "pending send failed: " + err, side, lot, entry, stop,
                         riskMoney, riskPct, cmd.rung, 0);
         PrintFormat("%s bridge: pending order failed - %s", CHARON_NAME, err);
         return false;
        }

      if(cmd.trailTrigger != (int)CHARON_TRAIL_OFF)
         m_trail.SetMode((ENUM_CHARON_TRAIL_MODE)cmd.trailTrigger);

      SetOrderVerdict("pending",
                      StringFormat("limit working at %.5f, stop %.5f", entry, stop),
                      side, lot, entry, stop, riskMoney, riskPct, cmd.rung, ticket);
      PrintFormat("%s bridge: pending %s %.2f lots @ %.5f stop %.5f risk %.2f (#%I64u)",
                  CHARON_NAME, side, lot, entry, stop, riskMoney, ticket);
      return true;
     }

   //--- MARKET. Two different situations reach this point and they must not
   //--- be conflated:
   //---   * flat book -- the ticket is the primary, subject to its own budget;
   //---   * an existing same-direction basket -- adding here is a scale-in,
   //---     and the pyramid's collective ceiling applies instead. Letting a
   //---     dashboard ticket bypass InpPyramidMaxRiskPct would quietly make
   //---     the basket more dangerous than the chart would allow, so the
   //---     extra exposure is projected and refused if it breaches the cap.
   if(m_basket.valid && !m_basket.mixed && m_basket.legs > 0)
     {
      double projected = m_basket.riskMoney + riskMoney;
      double basketCap = base * InpPyramidMaxRiskPct / 100.0;
      if(basketCap > 0.0 && projected > basketCap + CHARON_EPS)
        {
         SetOrderVerdict("rejected",
                         StringFormat("adding %.2f would take basket risk to %.2f (cap %.2f)",
                                      riskMoney, projected, basketCap),
                         side, lot, entry, stop, riskMoney, riskPct, cmd.rung, 0);
         return false;
        }
     }

   double tp      = OrderTarget(entry, distance, isLong);
   ulong  opened  = 0;
   double fill    = 0.0;
   string note    = "";
   string comment = StringFormat("%s M%d", CHARON_COMMENT, cmd.rung);
   if(!ChExecOpenMarket(m_opts, (isLong ? POSITION_TYPE_BUY : POSITION_TYPE_SELL),
                        lot, stop, tp, InpMagic, comment, opened, fill, note))
     {
      SetOrderVerdict("rejected", "open failed: " + note, side, lot, entry, stop,
                      riskMoney, riskPct, cmd.rung, 0);
      PrintFormat("%s bridge: market open failed - %s", CHARON_NAME, note);
      return false;
     }

   if(cmd.trailTrigger != (int)CHARON_TRAIL_OFF)
      m_trail.SetMode((ENUM_CHARON_TRAIL_MODE)cmd.trailTrigger);

   //--- res.order is the ORDER ticket, not necessarily the position ticket,
   //--- so re-scan instead of adopting by number. The leg already carries its
   //--- stop, so there is no unprotected window here either way.
   ScanAndAdopt();

   double shownEntry = (fill > 0.0 ? fill : entry);
   SetOrderVerdict("accepted",
                   StringFormat("%s filled %.2f lots @ %.5f, stop %.5f",
                                side, lot, shownEntry, stop),
                   side, lot, shownEntry, stop, riskMoney, riskPct, cmd.rung, opened);
   PrintFormat("%s bridge: %s %.2f lots @ %.5f stop %.5f risk %.2f (%.2f%%) (#%I64u)",
               CHARON_NAME, side, lot, shownEntry, stop, riskMoney, riskPct, opened);
   return true;
  }








//+------------------------------------------------------------------+
//| Assemble and publish the snapshot the dashboard renders          |
//|                                                                  |
//|  This is a projection of state that already exists - no risk      |
//|  figure, RR rung or tier trigger is recomputed here. The ladder    |
//|  strings come straight from RrLadderText(), so the panel and the   |
//|  chart can never drift apart.                                      |
//+------------------------------------------------------------------+
void CCharonEngine::PublishBridge(void)
  {
   SCharonBridgeSnapshot snap;
   ZeroMemory(snap);

   snap.ready      = m_ready;
   snap.symbol     = m_symbol;
   snap.serverTime = TimeCurrent();
   snap.dashboardOn = InpShowDashboard;
   snap.persistOn   = InpPersistState;

   snap.digits   = m_risk.Digits();
   snap.currency = AccountInfoString(ACCOUNT_CURRENCY);
   snap.balance  = AccountInfoDouble(ACCOUNT_BALANCE);
   snap.equity   = AccountInfoDouble(ACCOUNT_EQUITY);

   //--- basket: copied, never re-derived
   snap.basketValid    = m_basket.valid;
   snap.basketMixed    = m_basket.mixed;
   snap.basketDir      = m_basket.dir;
   snap.basketLegs     = m_basket.legs;
   snap.basketLot      = m_basket.lot;
   snap.basketAvgEntry = m_basket.avgEntry;
   snap.basketOpenPL   = m_basket.openPL;
   snap.basketRiskMoney = m_basket.riskMoney;
   snap.basketRiskPct   = m_basket.riskPct;
   snap.basketBest      = m_basket.bestPrice;

   if(m_basket.valid && !m_basket.mixed)
      snap.basketLadder = RrLadderText(m_basket.avgEntry, BasketRefRisk(),
                                       ChIsLong(m_basket.dir));

   //--- risk model in force, worded exactly as the chart panel words it
   string slUnit = StringFormat("%.0f points", InpSlPoints);
   if(InpSlUnit == CHARON_SL_ATR)
      slUnit = StringFormat("ATR x%.2f", InpSlAtrMult);
   else if(InpSlUnit == CHARON_SL_PRICE_PCT)
      slUnit = StringFormat("%.2f%% of price", InpSlPricePct);
   snap.slText = slUnit;

   string tpText = "no target";
   if(InpTpUnit == CHARON_TP_RR)
      tpText = StringFormat("%.2fR", InpTpRR);
   else if(InpTpUnit == CHARON_TP_POINTS)
      tpText = StringFormat("%.0f points", InpTpPoints);
   snap.tpText = tpText;

   string sizing = StringFormat("%.2f lots", InpFixedLots);
   if(InpLotMode == CHARON_LOT_RISK_PCT)
      sizing = StringFormat("%.2f%% of %s", InpRiskPct,
                            (InpUseEquity ? "equity" : "balance"));
   else if(InpLotMode == CHARON_LOT_FIXED_MONEY)
      sizing = StringFormat("%.2f %s", InpRiskMoney,
                            AccountInfoString(ACCOUNT_CURRENCY));
   snap.sizingText = sizing;
   snap.maxRiskPct = InpMaxRiskPct;

   //--- trailing / pyramid
   snap.trailMode = (int)m_trail.Mode();
   snap.trailText = EnumToString(m_trail.Mode());
   snap.pyramidEnabled   = InpPyramidEnable;
   snap.pyramidMaxLegs   = ChClampI(InpPyramidMaxLegs, 1, CHARON_MAX_TIERS);
   snap.pyramidMaxRiskPct = InpPyramidMaxRiskPct;
   snap.pyramidTrigger   = (InpPyramidTrigger == CHARON_PYR_MANUAL ? 1 : 0);
   snap.pyramidArmKey    = InpPyramidArmKey;
   snap.pyramidArmed     = false;
   snap.pyramidSecsLeft  = -1;

   if(InpPyramidTrigger == CHARON_PYR_MANUAL && m_pyramid.IsArmed())
     {
      bool live = ChArmIsLive(true, m_pyramid.ArmedAt(), TimeCurrent(),
                              InpPyramidArmTimeoutSec);
      snap.pyramidArmed = live;
      if(live && InpPyramidArmTimeoutSec > 0)
        {
         long left = (long)InpPyramidArmTimeoutSec
                     - (long)(TimeCurrent() - m_pyramid.ArmedAt());
         snap.pyramidSecsLeft = (left > 0 ? left : 0);
        }
     }

   //--- next tier + progress, mirroring MaybePyramid()'s arithmetic
   int highestTier = 1;
   for(int t = 0; t < m_state.Count(); t++)
     {
      SCharonPosState r;
      if(m_state.Get(t, r) && r.tier > highestTier)
         highestTier = r.tier;
     }
   snap.nextTier = highestTier + 1;

   double refRisk = BasketRefRisk();
   if(m_basket.valid && !m_basket.mixed && refRisk > 0.0)
     {
      bool isLong = ChIsLong(m_basket.dir);
      snap.progressRR = ChProgressRR(m_basket.avgEntry, refRisk,
                                     (isLong ? m_risk.Bid() : m_risk.Ask()), isLong);
     }

   //--- order verdict: copied, never recomputed
   snap.orderStatus    = m_orderStatus;
   snap.orderReason    = m_orderReason;
   snap.orderSide      = m_orderSide;
   snap.orderLots      = m_orderLots;
   snap.orderEntry     = m_orderEntry;
   snap.orderStop      = m_orderStop;
   snap.orderRiskMoney = m_orderRiskMoney;
   snap.orderRiskPct   = m_orderRiskPct;
   snap.orderRung      = m_orderRung;
   snap.orderTicket    = m_orderTicket;

   //--- legs
   SCharonBridgeLeg legs[];
   ArrayResize(legs, CHARON_BRIDGE_MAX_LEGS);
   int n = 0;
   for(int i = 0; i < m_state.Count() && n < CHARON_BRIDGE_MAX_LEGS; i++)
     {
      SCharonPosState row;
      if(!m_state.Get(i, row))
         continue;
      if(!PositionSelectByTicket(row.ticket))
         continue;

      bool isLong = ChIsLong(row.type);
      double px   = (isLong ? m_risk.Bid() : m_risk.Ask());
      double liveSl = PositionGetDouble(POSITION_SL);

      legs[n].ticket      = row.ticket;
      legs[n].type        = row.type;
      legs[n].tier        = row.tier;
      legs[n].entry       = row.entry;
      legs[n].lot         = row.lot;
      legs[n].currentSL   = liveSl;
      legs[n].riskPrice   = row.riskPrice;
      legs[n].hwm         = row.hwm;
      legs[n].progressRR  = ChProgressRR(row.entry, row.riskPrice, px, isLong);
      legs[n].trailActive = row.trailActive;
      legs[n].beDone      = row.beDone;
      legs[n].halfRiskDone = row.halfRiskDone;
      legs[n].ladder      = "";
      //--- per-leg ladder, projected from that leg's own entry and own R so a
      //--- tier's targets never inherit the primary's geometry
      if(row.riskPrice > 0.0 && row.entry > 0.0)
         legs[n].ladder = RrLadderText(row.entry, row.riskPrice, isLong);
      n++;
     }
   snap.legCount = n;

   m_bridge.Publish(snap, legs, n);
  }

//+------------------------------------------------------------------+
#endif // __CHARON_ENGINE__

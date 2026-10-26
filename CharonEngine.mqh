//+------------------------------------------------------------------+
//|                                                 CharonEngine.mqh |
//|  Orchestration: adoption, per-tick management, basket view,       |
//|  pyramid escalation and the on-chart dashboard.                   |
//|                                                                  |
//|  There is exactly one public entry point - OnTickWork() - so the  |
//|  order of operations is auditable in a single place.              |
//+------------------------------------------------------------------+
#property copyright "Charon"
#property version   "1.07"

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
   int               CollectPending(SCharonBridgePending &out[], const int maxRows);
   void              ApplyTicketTrail(const SCharonBridgeCommand &cmd, const ulong positionTicket);
   void              ApplyTicketPlan(const SCharonBridgeCommand &cmd, const ulong positionTicket);
   bool              PlanFromTicket(const SCharonBridgeCommand &cmd, SCharonStagePlan &plan);
   void              RestoreTicketPlan(void);
   void              PublishBridge(void);

   //--- v1.5.0 watch queue
   void              WatchAdd(const SCharonBridgeCommand &cmd);
   bool              WatchCancel(const long id);
   void              EvaluateWatches(void);
   int               CollectWatches(SCharonBridgeWatch &out[], const int maxRows);
   bool              SaveWatches(void);
   bool              LoadWatches(void);

   //--- armed tickets waiting on a price. Held whole, so the fill runs the
   //--- identical order_open path with the identical validated intent.
   SCharonWatch      m_watch[CHARON_BRIDGE_MAX_WATCHES];
   int               m_watchCount;
   long              m_nextWatchId;

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
   string            m_orderJournalTag;

   //--- toggles on a CHANGE of the bridge publish verdict, because the timer
   //--- runs once a second: a failure printed 3,600 times an hour is a log
   //--- nobody reads, and a failure printed never is the bug this fixes.
   bool              m_bridgeFailing;

   //--- same transition discipline for the rates refresh: when it starts
   //--- failing the timer stops dead, which also stops the publish, so the
   //--- silence has to be explained rather than left to look like a healthy
   //--- EA that simply went quiet.
   bool              m_refreshFailing;

   //--- trail thresholds waiting on a pending order that has not filled yet.
   //--- A pending order has no position ticket, so the values are held
   //--- against the ORDER ticket and claimed by AdoptTicket when the fill
   //--- finally appears.
   ulong             m_pendingTrailTicket;
   double            m_pendingTrailRR;
   double            m_pendingTrailPoints;
   double            m_pendingTrailPrice;

   //--- pyramid stages waiting on the same pending fill. Held for the same
   //--- reason as the thresholds above: there is no position row yet.
   SCharonStagePlan  m_pendingPlan;
   bool              m_pendingPlanValid;

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
   m_bridgeFailing  = false;
   m_refreshFailing = false;
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
   m_orderJournalTag = "";
   m_pendingTrailTicket = 0;
   m_pendingTrailRR     = 0.0;
   m_pendingTrailPoints = 0.0;
   m_pendingTrailPrice  = 0.0;
   ZeroMemory(m_pendingPlan);
   m_pendingPlanValid = false;
   m_watchCount = 0;
   m_nextWatchId = 1;
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

   //--- adopt anything that already belongs to Charon before the first tick,
   //--- so a reload does not leave positions unmanaged for one tick. Watches
   //--- are restored here but deliberately NOT evaluated: firing a trade from
   //--- OnInit would act on whatever tick the terminal last cached, which on a
   //--- weekend reload can be hours stale. The first OnTickWork evaluates them
   //--- against a real tick, and a level reached while the terminal was down
   //--- still fires there.
   ScanAndAdopt();
   LoadWatches();
   //--- a reload must keep the ladder the operator authorised, so the plan
   //--- the primary row persisted is re-attached before the first tick.
   RestoreTicketPlan();

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
   //--- the armed queue outlives a chart reload by design: an entry the
   //--- operator set is not "chart state" they expect to lose when they
   //--- recompile. Save is unconditional here -- a throttle would drop a
   //--- watch armed seconds before the reload.
   SaveWatches();
   m_risk.Deinit();
   Comment("");
   m_ready = false;
  }

//+------------------------------------------------------------------+
//| The watch queue file                                             |
//|                                                                  |
//|  Tab-separated, one watch per line, rewritten whole on every      |
//|  change: the queue is at most CHARON_BRIDGE_MAX_WATCHES rows, so   |
//|  an incremental format would buy nothing and risk a torn tail.     |
//|  A corrupt line costs one watch, never the queue.                  |
//|  Written under MQL5\Files\ and not into Common\Files: the queue is |
//|  this chart's private state, not part of the Python contract, and  |
//|  the bridge never reads it.                                        |
//+------------------------------------------------------------------+
#define CHARON_WATCH_FILE "Charon\\watches.txt"

//+------------------------------------------------------------------+
//| Persist the armed queue                                          |
//|  Deliberately whole-file: at most CHARON_BRIDGE_MAX_WATCHES rows, |
//|  so rewriting is cheaper and simpler than any incremental scheme. |
//+------------------------------------------------------------------+
bool CCharonEngine::SaveWatches(void)
  {
   if(!InpPersistState)
      return false;

   //--- delete before writing. FileSeek(0) only rewinds: it does not truncate,
   //--- so rewriting a shorter queue would leave the old tail on disk and a
   //--- cancelled watch would come back on the next reload. There is no
   //--- truncate call in MQL5, so the file is recreated.
   if(FileIsExist(CHARON_WATCH_FILE))
      FileDelete(CHARON_WATCH_FILE);

   ResetLastError();
   int h = FileOpen(CHARON_WATCH_FILE,
                    FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_SHARE_READ);
   if(h == INVALID_HANDLE)
     {
      PrintFormat("%s watches: could not open %s (%d)", CHARON_NAME,
                  CHARON_WATCH_FILE, GetLastError());
      return false;
     }

   FileWrite(h, "# Charon watch queue v1");
   const int dg = (m_risk.Digits() > 0 ? m_risk.Digits() : 5);
   for(int i = 0; i < m_watchCount; i++)
     {
      //--- one tab-separated record per watch, field order fixed and mirrored
      //--- exactly by LoadWatches:
      //---   0 id        1 side       2 orderType   3 created   4 price
      //---   5 entry     6 blockTop   7 blockBottom 8 riskPct   9 riskMoney
      //---  10 ceiling  11 startRR   12 startPts   13 startPrc 14 rung
      //---  15 journalTag 16 stages
      //--- Prices use the symbol's own digits, so a reload reproduces the exact
      //--- doubles the operator sent rather than a rounded copy of them.
      const SCharonBridgeCommand c = m_watch[i].cmd;
      string row = StringFormat("%I64d\t%s\t%s\t%I64d\t",
                                m_watch[i].id, c.side, c.orderType,
                                (long)m_watch[i].createdAt);
      row += DoubleToString(m_watch[i].price, dg) + "\t";
      row += DoubleToString(c.entry, dg) + "\t";
      row += DoubleToString(c.blockTop, dg) + "\t";
      row += DoubleToString(c.blockBottom, dg) + "\t";
      row += DoubleToString(c.riskPct, 4) + "\t";
      row += DoubleToString(c.riskMoney, 2) + "\t";
      row += DoubleToString(c.basketCeilingPct, 4) + "\t";
      row += DoubleToString(c.trailStartRR, 4) + "\t";
      row += DoubleToString(c.trailStartPoints, 2) + "\t";
      row += DoubleToString(c.trailStartPrice, dg) + "\t";
      row += StringFormat("%d\t%s\t", c.rung, c.journalTag);
      //--- v1.7.0: the watch's pyramid ladder, or "" when it named none. The
      //--- trailing field is appended last so a pre-1.7.0 reader that stops at
      //--- field 15 still sees a complete record.
      SCharonStagePlan watchPlan;
      PlanFromTicket(c, watchPlan);
      row += ChFormatStagePlan(watchPlan);
      FileWrite(h, row);
     }
   FileClose(h);
   return true;
  }

//+------------------------------------------------------------------+
//| Restore the armed queue                                          |
//|  A line that cannot be parsed is skipped, not fatal: the operator  |
//|  loses one watch and keeps the rest, and the log says so.          |
//+------------------------------------------------------------------+
bool CCharonEngine::LoadWatches(void)
  {
   m_watchCount = 0;
   if(!InpPersistState)
      return false;

   if(!FileIsExist(CHARON_WATCH_FILE))
      return false;

   int h = FileOpen(CHARON_WATCH_FILE, FILE_READ | FILE_TXT | FILE_ANSI | FILE_SHARE_READ);
   if(h == INVALID_HANDLE)
      return false;

   while(!FileIsEnding(h) && m_watchCount < CHARON_BRIDGE_MAX_WATCHES)
     {
      string line = FileReadString(h);
      if(StringLen(line) < 8 || StringGetCharacter(line, 0) == '#')
         continue;

      string f[];
      if(StringSplit(line, '\t', f) < 16)
        {
         PrintFormat("%s watches: skipping a malformed record", CHARON_NAME);
         continue;
        }

      SCharonBridgeCommand c;
      ZeroMemory(c);
      c.valid       = true;
      c.verb        = CHARON_BRIDGE_V_ORDER_OPEN;   // a fired watch IS an open
      c.side        = f[1];
      c.orderType   = f[2];
      c.entry       = StringToDouble(f[5]);
      c.blockTop    = StringToDouble(f[6]);
      c.blockBottom = StringToDouble(f[7]);
      c.riskPct     = StringToDouble(f[8]);
      c.riskMoney   = StringToDouble(f[9]);
      c.basketCeilingPct = StringToDouble(f[10]);
      c.trailStartRR     = StringToDouble(f[11]);
      c.trailStartPoints = StringToDouble(f[12]);
      c.trailStartPrice  = StringToDouble(f[13]);
      c.rung        = (int)StringToInteger(f[14]);
      c.journalTag  = f[15];

      //--- v1.7.0: the pyramid ladder, when the record carries one. A queue
      //--- written by an older build has only 16 fields, and an empty 17th is
      //--- a watch that named no stages -- both restore as "no plan", which is
      //--- the old chart-wide behaviour.
      c.stageCount = 0;
      for(int s = 0; s < CHARON_BRIDGE_MAX_STAGES; s++)
        {
         c.stages[s].kind     = (int)CHARON_STAGE_RR;
         c.stages[s].trigger  = 0.0;
         c.stages[s].lotsMult = 0.0;
        }
      if(ArraySize(f) >= 17 && f[16] != "")
        {
         SCharonStagePlan plan;
         if(!ChParseStagePlan(f[16], plan) || plan.count <= 0)
           {
            //--- a corrupt ladder costs the watch: firing it with the chart
            //--- inputs would send exposure the operator never authorised.
            PrintFormat("%s watches: skipping a watch with an unreadable stage plan",
                        CHARON_NAME);
            continue;
           }
         for(int s = 0; s < plan.count; s++)
           {
            c.stages[s].kind     = plan.stage[s].kind;
            c.stages[s].trigger  = plan.stage[s].trigger;
            c.stages[s].lotsMult = plan.stage[s].lotsMult;
           }
         c.stageCount = plan.count;
        }

      //--- a row that would not have passed the parser is not worth keeping:
      //--- restoring it would submit a ticket the operator never validated.
      if(c.side != "BUY" && c.side != "SELL")
         continue;
      if(c.entry <= 0.0 || c.blockTop == c.blockBottom)
         continue;

      m_watch[m_watchCount].id        = (long)StringToInteger(f[0]);
      m_watch[m_watchCount].price     = StringToDouble(f[4]);
      m_watch[m_watchCount].createdAt = (datetime)(long)StringToInteger(f[3]);
      m_watch[m_watchCount].cmd       = c;
      if(m_watch[m_watchCount].id >= m_nextWatchId)
         m_nextWatchId = m_watch[m_watchCount].id + 1;
      m_watchCount++;
     }
   FileClose(h);

   if(m_watchCount > 0)
      PrintFormat("%s watches: restored %d armed entr%s",
                  CHARON_NAME, m_watchCount, (m_watchCount == 1 ? "y" : "ies"));
   return (m_watchCount > 0);
  }
//+------------------------------------------------------------------+
//| Arm a validated ticket until the market reaches its price        |
//|                                                                  |
//|  Nothing is submitted here and nothing is sized: the ticket is    |
//|  parked whole, and when the level is touched the EA runs the same |
//|  ApplyOrderCommand path an immediate order would have taken.      |
//|  That is the whole point -- a watch defers *when*, never *what*.  |
//+------------------------------------------------------------------+
void CCharonEngine::WatchAdd(const SCharonBridgeCommand &cmd)
  {
   if(m_watchCount >= CHARON_BRIDGE_MAX_WATCHES)
     {
      SetOrderVerdict("rejected",
                      StringFormat("watch queue is full (%d)", CHARON_BRIDGE_MAX_WATCHES),
                      cmd.side, 0.0, cmd.entry, 0.0,
                      cmd.riskMoney, cmd.riskPct, cmd.rung, 0);
      PrintFormat("%s bridge: watch refused - queue is full", CHARON_NAME);
      return;
     }

   m_watch[m_watchCount].id        = m_nextWatchId++;
   m_watch[m_watchCount].price     = cmd.entry;
   m_watch[m_watchCount].createdAt = TimeCurrent();
   m_watch[m_watchCount].cmd       = cmd;
   m_watchCount++;

   SaveWatches();

   SetOrderVerdict("watching",
                   StringFormat("watching %.5f for a %s entry", cmd.entry, cmd.side),
                   cmd.side, 0.0, cmd.entry, 0.0,
                   cmd.riskMoney, cmd.riskPct, cmd.rung, 0);
   PrintFormat("%s bridge: watching %.5f for %s (watch #%I64d)",
               CHARON_NAME, cmd.entry, cmd.side, m_nextWatchId - 1);
  }

//+------------------------------------------------------------------+
//| Drop an armed watch                                              |
//+------------------------------------------------------------------+
bool CCharonEngine::WatchCancel(const long id)
  {
   for(int i = 0; i < m_watchCount; i++)
     {
      if(m_watch[i].id != id)
         continue;

      for(int k = i; k < m_watchCount - 1; k++)
         m_watch[k] = m_watch[k + 1];
      m_watchCount--;

      SaveWatches();
      SetOrderVerdict("accepted", StringFormat("watch #%I64d cancelled", id),
                      "", 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0);
      PrintFormat("%s bridge: watch #%I64d cancelled", CHARON_NAME, id);
      return true;
     }

   SetOrderVerdict("rejected", StringFormat("no watch #%I64d to cancel", id),
                   "", 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0);
   return false;
  }

//+------------------------------------------------------------------+
//| Fire every watch whose level the market has reached              |
//|                                                                  |
//|  Runs *before* ManageRow in the tick pipeline: a watch that fires |
//|  opens a position, and that position should be managed by the     |
//|  same pass rather than waiting a tick for its first stop check.   |
//|                                                                  |
//|  The test is "touched or crossed", never equality. A level the     |
//|  market gaps over must still fire, and an exact-tick comparison    |
//|  would silently never trigger -- which is the worst possible      |
//|  failure for a feature whose entire job is to wait.               |
//+------------------------------------------------------------------+
void CCharonEngine::EvaluateWatches(void)
  {
   if(m_watchCount <= 0)
      return;

   //--- iterate backwards: firing removes rows
   for(int i = m_watchCount - 1; i >= 0; i--)
     {
      const bool   isLong = (m_watch[i].cmd.side == "BUY");
      //--- a BUY is filled at the ask and a SELL at the bid, so the price that
      //--- must reach the level is the one the fill would use, not the other
      //--- side of the spread.
      double quoted = (isLong ? m_risk.Ask() : m_risk.Bid());
      if(!MathIsValidNumber(quoted) || quoted <= 0.0)
         continue;

      const double target = m_watch[i].price;
      //--- "touched or crossed", never equality -- the same rule the trail
      //--- uses, via the same helper, so the two can never drift.
      if(!ChPriceAtOrBeyond(quoted, target, isLong))
         continue;

      //--- take a copy and remove the row BEFORE submitting: leaving it armed
      //--- would re-fire on the very next tick whatever the outcome.
      SCharonWatch firing = m_watch[i];
      for(int k = i; k < m_watchCount - 1; k++)
         m_watch[k] = m_watch[k + 1];
      m_watchCount--;
      SaveWatches();

      //--- identical path an immediate order takes. The ticket is already
      //--- validated; verb order_open is exactly what the parser would have
      //--- set had the command arrived as an order instead of a watch.
      SCharonBridgeCommand fire = firing.cmd;
      fire.verb  = CHARON_BRIDGE_V_ORDER_OPEN;
      fire.valid = true;

      PrintFormat("%s watch #%I64d fired: %s reached %.5f (quoted %.5f)",
                  CHARON_NAME, firing.id, fire.side, target, quoted);

      //--- ApplyOrderCommand writes its own verdict, including on refusal, so
      //--- nothing is added here: a rejected fire reports the real reason.
      ApplyOrderCommand(fire);
     }
  }

//+------------------------------------------------------------------+
//| Project the armed queue for the snapshot                         |
//+------------------------------------------------------------------+
int CCharonEngine::CollectWatches(SCharonBridgeWatch &out[], const int maxRows)
  {
   int n = 0;
   if(maxRows <= 0)
      return 0;

   for(int i = 0; i < m_watchCount && n < maxRows; i++)
     {
      out[n].id        = m_watch[i].id;
      out[n].side      = m_watch[i].cmd.side;
      out[n].price     = m_watch[i].price;
      out[n].orderType = m_watch[i].cmd.orderType;
      out[n].createdAt = m_watch[i].createdAt;
      out[n].status    = "armed";
      n++;
     }
   return n;
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

   //--- a pending order filled: hand it the thresholds the ticket asked for.
   //--- The order ticket and the position ticket differ on most brokers, so
   //--- this is matched by the fact that only one Charon pending order may be
   //--- working at a time (HasOwnPending refuses a second).
   if(m_pendingTrailTicket > 0 &&
      (m_pendingTrailRR > 0.0 || m_pendingTrailPoints > 0.0 || m_pendingTrailPrice > 0.0))
     {
      SCharonPosState pending;
      if(m_state.Get(idx, pending))
        {
         pending.trailStartRR     = m_pendingTrailRR;
         pending.trailStartPoints = m_pendingTrailPoints;
         pending.trailStartPrice  = m_pendingTrailPrice;
         if(m_state.Set(idx, pending))
            m_state.Save(idx, true);
         m_pendingTrailTicket = 0;
         m_pendingTrailRR     = 0.0;
         m_pendingTrailPoints = 0.0;
         m_pendingTrailPrice  = 0.0;
        }
     }

   //--- a pending order may also have carried pyramid stages. Same matching
   //--- rule as the trail thresholds: one Charon pending order at a time, so a
   //--- fill claims the plan and the plan travels with the primary leg.
   if(m_pendingPlanValid && m_pendingPlan.count > 0)
     {
      SCharonPosState pending;
      if(m_state.Get(idx, pending) && pending.tier == 1)
        {
         ChCopyPlan(pending.stages, m_pendingPlan);
         if(m_state.Set(idx, pending))
            m_state.Save(idx, true);
         m_pyramid.SetPlan(pending.stages);
         PrintFormat("%s #%I64u pyramid plan from a filled pending: %s", CHARON_NAME,
                     ticket, ChFormatStagePlan(pending.stages));
        }
      m_pendingPlanValid = false;
      ZeroMemory(m_pendingPlan);
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
      //--- a persisted threshold survives a restart; 0 in the restored row
      //--- means the pre-1.4.0 default, so the seeded value is left alone.
      if(restored.trailStartRR > 0.0)
         row.trailStartRR = restored.trailStartRR;
      if(restored.trailStartPoints > 0.0)
         row.trailStartPoints = restored.trailStartPoints;
      if(restored.trailStartPrice > 0.0)
         row.trailStartPrice = restored.trailStartPrice;
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

   //--- a reloaded chart has just recovered this ticket's plan from the state
   //--- file; re-attaching it here is what makes the ladder survive a
   //--- recompile rather than silently reverting to the chart inputs.
   RestoreTicketPlan();
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
                        trailStop, trailLabel, row.trailStartRR, row.trailStartPoints,
                        row.trailStartPrice))
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
   //--- the same resolution Evaluate() uses: the ticket's own stage when it
   //--- has one, otherwise the chart ladder. Projecting with a different
   //--- multiplier than the one that will actually be sent would understate
   //--- the basket risk the ceiling check is about to approve.
   if(m_pyramid.ResolveSpec(nextTier, spec))
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
   //--- the price the stage is judged against: the quote the next leg would
   //--- trade at, so a PRICE stage fires on the same number the fill sees.
   double stagePrice = (isLong ? m_risk.Bid() : m_risk.Ask());
   ENUM_CHARON_TIER_DECISION decision = m_pyramid.Evaluate(m_basket.legs, nextTier, dirSign,
                                                           progress, projected, base,
                                                           stagePrice);
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

   //--- the publish verdict, shown rather than assumed. Before this the panel
   //--- could not tell "the dashboard is switched off" from "the dashboard is
   //--- on and every write is failing", and the second state is precisely what
   //--- makes /api/status answer 503 with no clue anywhere on the terminal.
   string bridgeLine = "off (InpBridgeEnable=false)";
   if(m_bridge.Enabled())
     {
      bridgeLine = StringFormat("%s  |  seq %I64u",
                                (m_bridge.LastOk() ? "ok" : "FAILED"),
                                m_bridge.Sequence());
      if(!m_bridge.LastOk())
         bridgeLine += "  |  " + m_bridge.Note();
     }
   text += StringFormat("Bridge      : %s\n", bridgeLine);

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

   //--- 1b) fire any armed entry whose level this tick reached. Before
   //--- ManageRow on purpose: a fired watch opens a position, and that
   //--- position gets its first stop check in the same pass rather than on
   //--- the next one.
   EvaluateWatches();

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
//|                                                                  |
//|  The refresh gate below is deliberately narrow: RefreshRates()    |
//|  only moves the spread figure, and every consumer of it is        |
//|  downstream of the early return. A terminal whose symbols have    |
//|  gone unselected therefore stops the timer -- which also stops the |
//|  snapshot publish -- so that state is reported on entry and left   |
//|  reported, instead of looking like an EA that simply went quiet.   |
//+------------------------------------------------------------------+
void CCharonEngine::OnTimerWork(void)
  {
   if(!m_ready || m_busy)
      return;
   if(!m_risk.Refresh())
     {
      if(!m_refreshFailing)
        {
         m_refreshFailing = true;
         PrintFormat("%s: SymbolInfo refresh failed - the timer pass is "
                     "suspended until the terminal serves prices for %s "
                     "again. The bridge stops publishing while this lasts.",
                     CHARON_NAME, m_symbol);
        }
      return;
     }
   if(m_refreshFailing)
     {
      m_refreshFailing = false;
      PrintFormat("%s: SymbolInfo refresh recovered", CHARON_NAME);
     }

   m_busy = true;
   ScanAndAdopt();
   //--- the timer is the only pass that runs without ticks, so a watch whose
   //--- level was reached during a quiet spell still fires here.
   EvaluateWatches();
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

   //--- v1.5.0 watch queue. `watch_add` carries a full validated ticket, so it
   //--- is parked rather than submitted; `watch_cancel` names a watch id.
   if(cmd.verb == CHARON_BRIDGE_V_WATCH_ADD)
     {
      WatchAdd(cmd);
      return true;
     }
   if(cmd.verb == CHARON_BRIDGE_V_WATCH_CANCEL)
      return WatchCancel(cmd.watchId);

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
//| Attach a ticket's trail thresholds to the position it opened     |
//|                                                                  |
//|  Zero means "this ticket said nothing", so the row keeps the       |
//|  chart-wide InpTrailStartRR / InpTrailStartPoints. That is the     |
//|  whole compatibility story: a ticket written by any older client   |
//|  lands here with zeros and changes nothing.                        |
//+------------------------------------------------------------------+
void CCharonEngine::ApplyTicketTrail(const SCharonBridgeCommand &cmd,
                                     const ulong positionTicket)
  {
   if(positionTicket == 0)
      return;
   if(cmd.trailStartRR <= 0.0 && cmd.trailStartPoints <= 0.0 &&
      cmd.trailStartPrice <= 0.0)
      return;

   int idx = m_state.Find(positionTicket);
   if(idx < 0)
      return;

   SCharonPosState row;
   if(!m_state.Get(idx, row))
      return;

   if(cmd.trailStartRR > 0.0)
      row.trailStartRR = cmd.trailStartRR;
   if(cmd.trailStartPoints > 0.0)
      row.trailStartPoints = cmd.trailStartPoints;
   //--- v1.5.0: an absolute activation price. Validated against the fill
   //--- price here rather than in the parser, because the parser has no fill
   //--- to check against -- and a threshold on the wrong side of the entry
   //--- would arm the trail immediately in the wrong direction.
   if(cmd.trailStartPrice > 0.0)
     {
      bool isLongPos = ChIsLong(PositionGetInteger(POSITION_TYPE));
      double fill    = PositionGetDouble(POSITION_PRICE_OPEN);
      if(fill > 0.0 &&
         ((isLongPos && cmd.trailStartPrice <= fill) ||
          (!isLongPos && cmd.trailStartPrice >= fill)))
        {
         PrintFormat("%s #%I64u trail_start_price %.5f is not beyond the %.5f fill; ignored",
                     CHARON_NAME, positionTicket, cmd.trailStartPrice, fill);
        }
      else
         row.trailStartPrice = cmd.trailStartPrice;
     }

   if(m_state.Set(idx, row))
      m_state.Save(idx, true);

   if(InpVerbose)
      PrintFormat("%s #%I64u trail activation -> %.2fR / %.0f points",
                  CHARON_NAME, positionTicket, row.trailStartRR, row.trailStartPoints);
  }

//+------------------------------------------------------------------+
//| Build a plan from the stages a ticket carried                    |
//|                                                                  |
//|  The wire parser already rejected every malformed stage, so a     |
//|  stage that reaches here is individually valid. What is decided   |
//|  here is whether the plan as a whole is worth keeping: a plan is  |
//|  all-or-nothing, because a three-stage ticket that silently       |
//|  becomes one stage is a lie about the exposure the operator        |
//|  authorised.                                                      |
//+------------------------------------------------------------------+
bool CCharonEngine::PlanFromTicket(const SCharonBridgeCommand &cmd,
                                   SCharonStagePlan &plan)
  {
   ZeroMemory(plan);
   if(cmd.stageCount <= 0)
      return false;

   for(int i = 0; i < cmd.stageCount && i < CHARON_BRIDGE_MAX_STAGES; i++)
     {
      if(!ChAddStage(plan, cmd.stages[i].kind,
                     cmd.stages[i].trigger, cmd.stages[i].lotsMult))
        {
         ZeroMemory(plan);
         return false;
        }
     }
   return (plan.count > 0);
  }

//+------------------------------------------------------------------+
//| Attach a ticket's pyramid stages to the primary leg              |
//|                                                                  |
//|  The plan lives on the tier-1 row because that is the leg whose   |
//|  1R every RR trigger is measured against. A ticket that named no   |
//|  stages clears the plan outright: the chart-wide ladder governs,   |
//|  which is exactly the pre-1.7.0 behaviour.                        |
//|                                                                  |
//|  Two deliberate rules:                                            |
//|   * a scale-in leg (tier > 1) never owns a plan, so a ticket that  |
//|     arrives against one is ignored rather than overwriting the     |
//|     primary's;                                                    |
//|   * the plan is written whether or not it changed, because the     |
//|     persisted copy is the only thing a restart can recover from.   |
//+------------------------------------------------------------------+
void CCharonEngine::ApplyTicketPlan(const SCharonBridgeCommand &cmd,
                                    const ulong positionTicket)
  {
   if(positionTicket == 0)
      return;

   int idx = m_state.Find(positionTicket);
   if(idx < 0)
      return;

   SCharonPosState row;
   if(!m_state.Get(idx, row))
      return;
   if(row.tier != 1)
      return;

   SCharonStagePlan plan;
   bool have = PlanFromTicket(cmd, plan);

   if(have)
     {
      m_pyramid.SetPlan(plan);
      ChCopyPlan(row.stages, plan);
      PrintFormat("%s #%I64u pyramid plan: %s", CHARON_NAME, positionTicket,
                  ChFormatStagePlan(plan));
     }
   else
     {
      //--- nothing stated: drop any plan the row was carrying, so a reload
      //--- or a re-issued ticket cannot resurrect a stale ladder.
      m_pyramid.ClearPlan();
      ZeroMemory(row.stages);
     }

   if(m_state.Set(idx, row))
      m_state.Save(idx, true);
  }

//+------------------------------------------------------------------+
//| Re-attach the plan the primary row persisted                     |
//|                                                                  |
//|  Called after adoption and on init, so a reload keeps the ladder   |
//|  the operator authorised. The plan is taken from the tier-1 row    |
//|  only: it is the row BasketRefRisk() reads, and a scale-in row     |
//|  carrying a plan would mean the table is inconsistent.            |
//|                                                                  |
//|  Idempotent, and cheap when there is nothing to do: a plan already |
//|  in place is left alone rather than cleared and rebuilt, so        |
//|  calling this from every adoption pass cannot churn state or spam  |
//|  the log.                                                          |
//+------------------------------------------------------------------+
void CCharonEngine::RestoreTicketPlan(void)
  {
   for(int i = 0; i < m_state.Count(); i++)
     {
      SCharonPosState row;
      if(!m_state.Get(i, row))
         continue;
      if(row.tier != 1)
         continue;
      if(row.stages.count <= 0)
         continue;

      //--- already attached: nothing to redo
      if(m_pyramid.PlanText() == ChFormatStagePlan(row.stages))
         return;

      m_pyramid.SetPlan(row.stages);
      if(InpVerbose)
         PrintFormat("%s #%I64u pyramid plan restored: %s", CHARON_NAME,
                     row.ticket, ChFormatStagePlan(row.stages));
      return;
     }

   //--- no primary row carries a plan, so nothing should be driving tiers.
   //--- Clearing here is what stops a closed ticket's ladder from outliving it.
   m_pyramid.ClearPlan();
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
//| The working pending orders this chart is responsible for         |
//|                                                                  |
//|  WantsTicket() decides ownership, so a manually placed order on    |
//|  the same symbol is not reported as Charon's -- exactly the        |
//|  boundary HasOwnPending already draws.                            |
//+------------------------------------------------------------------+
int CCharonEngine::CollectPending(SCharonBridgePending &out[], const int maxRows)
  {
   int n = 0;
   if(maxRows <= 0)
      return 0;

   for(int i = OrdersTotal() - 1; i >= 0 && n < maxRows; i--)
     {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0)
         continue;
      if(!OrderSelect(ticket))
         continue;
      if(OrderGetString(ORDER_SYMBOL) != m_symbol)
         continue;
      if(!WantsTicket((ulong)OrderGetInteger(ORDER_MAGIC)))
         continue;

      long otype = OrderGetInteger(ORDER_TYPE);
      out[n].ticket    = ticket;
      //--- ORDER_TYPE_BUY_LIMIT and BUY_STOP are both buys; the side is what
      //--- the dashboard renders, the raw type says which kind it is.
      out[n].type      = (otype == (long)ORDER_TYPE_BUY_LIMIT ||
                          otype == (long)ORDER_TYPE_BUY_STOP
                          ? (long)POSITION_TYPE_BUY : (long)POSITION_TYPE_SELL);
      out[n].orderType = otype;
      out[n].price     = OrderGetDouble(ORDER_PRICE_OPEN);
      out[n].sl        = OrderGetDouble(ORDER_SL);
      out[n].lot       = OrderGetDouble(ORDER_VOLUME_CURRENT);
      out[n].placedAt  = (datetime)OrderGetInteger(ORDER_TIME_SETUP);
      n++;
     }
   return n;
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

   //--- the tag belongs to the ticket, not to its outcome, so it is claimed
   //--- before any verdict can be written: a rejected ticket still shows
   //--- which note was attached to it.
   m_orderJournalTag = cmd.journalTag;

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
   double budget = 0.0;
   if(cmd.riskMoney > 0.0)
     {
      //--- the account is the EA to know, so a figure the operator typed is
      //--- measured against the same ceiling every other path uses.
      //--- Refusing (rather than silently shrinking) an over-sized figure
      //--- keeps the verdict honest about what actually happened.
      double cap = m_risk.MaxAllowedRiskMoney();
      if(cap > 0.0 && cmd.riskMoney > cap + CHARON_EPS)
        {
         SetOrderVerdict("rejected",
                         StringFormat("requested risk %.2f exceeds the %.2f cap",
                                      cmd.riskMoney, cap),
                         side, 0.0, entry, stop, 0.0, 0.0, cmd.rung, 0);
         return false;
        }
      budget = cmd.riskMoney;
     }
   else
     {
      double wanted = cmd.riskPct;
      if(wanted > InpMaxRiskPct)
         wanted = InpMaxRiskPct;
      budget = base * wanted / 100.0;
     }

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

      //--- a pending order has no state row until it fills, so the
      //--- threshold is remembered against the ORDER ticket and applied when
      //--- the fill is adopted. Chart-wide behaviour is untouched when the
      //--- ticket carried neither value.
      if(cmd.trailStartRR > 0.0 || cmd.trailStartPoints > 0.0 ||
         cmd.trailStartPrice > 0.0)
        {
         m_pendingTrailTicket = ticket;
         m_pendingTrailRR     = cmd.trailStartRR;
         m_pendingTrailPoints = cmd.trailStartPoints;
         m_pendingTrailPrice  = cmd.trailStartPrice;
        }

      //--- the plan is parked the same way: no row exists to hang it on yet,
      //--- and a limit ticket that specifies its ladder must not lose it.
      SCharonStagePlan pendingPlan;
      ZeroMemory(pendingPlan);
      if(PlanFromTicket(cmd, pendingPlan))
        {
         ChCopyPlan(m_pendingPlan, pendingPlan);
         m_pendingPlanValid = true;
        }

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
      //--- the effective ceiling is the harder of the chart's InpPyramidMaxRiskPct
      //--- and any ceiling the ticket attached. A ticket can only ever tighten
      //--- it: dropping the chart's ceiling by sending a bigger number would
      //--- make a dashboard click more permissive than the chart it drives.
      double effectivePct = InpPyramidMaxRiskPct;
      if(cmd.basketCeilingPct > 0.0 &&
         (effectivePct <= 0.0 || cmd.basketCeilingPct < effectivePct))
         effectivePct = cmd.basketCeilingPct;
      double basketCap = base * effectivePct / 100.0;
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

   //--- a ticket may carry its own trail activation thresholds. They are
   //--- attached to the row that was just adopted so the threshold travels
   //--- with the position rather than with the chart.
   ApplyTicketTrail(cmd, opened);
   //--- the ticket's own pyramid ladder lands on the primary row, so a ticket
   //--- that named stages drives scale-ins instead of the chart inputs.
   ApplyTicketPlan(cmd, opened);

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
   snap.orderJournalTag = m_orderJournalTag;

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

   //--- working orders. Scanned the same way HasOwnPending does, and for the
   //--- same reason: a pending order is not a position, so the state table
   //--- (and therefore legs) cannot describe it.
   SCharonBridgePending pendings[];
   ArrayResize(pendings, CHARON_BRIDGE_MAX_LEGS);
   int np = CollectPending(pendings, CHARON_BRIDGE_MAX_LEGS);

   //--- armed watches, which are neither positions nor broker orders
   SCharonBridgeWatch watches[];
   ArrayResize(watches, CHARON_BRIDGE_MAX_WATCHES);
   int nw = CollectWatches(watches, CHARON_BRIDGE_MAX_WATCHES);

   //--- last tick, so a watch can be shown with its distance to target
   snap.bid = m_risk.Bid();
   snap.ask = m_risk.Ask();

   m_bridge.Publish(snap, legs, n, pendings, np, watches, nw);

   //--- surface the verdict. The publish result used to be discarded, so the
   //--- one failure the operator cannot see on the chart -- the snapshot never
   //--- reaching disk -- was also the one that was never logged. Both edges are
   //--- reported and the steady state is silent, so the log stays readable.
   bool ok = m_bridge.LastOk();
   if(!ok && !m_bridgeFailing)
     {
      m_bridgeFailing = true;
      PrintFormat("%s: bridge publish FAILED - %s. The dashboard will show "
                  "offline until this clears; trading is unaffected.",
                  CHARON_NAME, m_bridge.Note());
     }
   else if(ok && m_bridgeFailing)
     {
      m_bridgeFailing = false;
      PrintFormat("%s: bridge publish recovered", CHARON_NAME);
     }
  }

//+------------------------------------------------------------------+
#endif // __CHARON_ENGINE__

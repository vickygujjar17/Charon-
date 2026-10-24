//+------------------------------------------------------------------+
//|                                                       Charon.mq5 |
//|                    Charon - semi-automated MQL5 trading copilot  |
//|                                                                  |
//|  Charon does not decide WHEN to trade. It decides HOW MUCH and    |
//|  HOW FAR, and it never lets a position run without a strict stop. |
//|                                                                  |
//|  Strict scope:                                                    |
//|    1. strict stop loss enforcement                                |
//|    2. trailing stop engine                                        |
//|    3. RR targets and R-based protection ladder                    |
//|    4. risk measurement / position sizing                          |
//|    5. pyramiding with a basket-risk ceiling                       |
//|                                                                  |
//|  Deliberately absent: entry signals, indicators that pick a       |
//|  direction, news filters, market-structure scanners.             |
//+------------------------------------------------------------------+
#property copyright "Charon"
#property version   "1.01"
#property description "Semi-automated copilot: strict SL, trailing stops, RR/risk sizing, pyramiding."
#property link      "https://github.com/vickygujjar17/Charon-.git"
#property strict

#include "CharonDefines.mqh"
#include "CharonEngine.mqh"

//--- one engine per chart
CCharonEngine g_engine;

//+------------------------------------------------------------------+
//| Expert initialization                                            |
//+------------------------------------------------------------------+
int OnInit(void)
  {
   if(!g_engine.Init())
     {
      PrintFormat("%s: initialisation failed - see the log above", CHARON_NAME);
      return INIT_FAILED;
     }

   //--- a 1s heartbeat covers symbols that stop ticking and positions
   //--- opened by the broker between ticks
   EventSetTimer(1);

   PrintFormat("%s: chart panel %s | persistence %s",
               CHARON_NAME,
               (InpShowDashboard ? "on" : "off"),
               (InpPersistState ? "on" : "off"));
   return INIT_SUCCEEDED;
  }

//+------------------------------------------------------------------+
//| Expert deinitialization                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   EventKillTimer();
   g_engine.Deinit();
   PrintFormat("%s: stopped (reason %d)", CHARON_NAME, reason);
  }

//+------------------------------------------------------------------+
//| Expert tick                                                      |
//+------------------------------------------------------------------+
void OnTick(void)
  {
   g_engine.OnTickWork();
  }

//+------------------------------------------------------------------+
//| Timer heartbeat                                                  |
//+------------------------------------------------------------------+
void OnTimer(void)
  {
   g_engine.OnTimerWork();
  }

//+------------------------------------------------------------------+
//| Trade transaction: react immediately to a fill or a modification |
//| so a brand-new manual position is adopted on the same event,     |
//| not on the next tick.                                            |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
  {
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD ||
      trans.type == TRADE_TRANSACTION_REQUEST)
      g_engine.OnTimerWork();
  }

//+------------------------------------------------------------------+
//| Chart events - keep the panel alive after a chart change, and     |
//| forward hotkeys so the MANUAL pyramid trigger can be armed        |
//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long &lparam, const double &dparam,
                  const string &sparam)
  {
   if(id == CHARTEVENT_CHART_CHANGE)
      g_engine.OnTimerWork();
   else if(id == CHARTEVENT_KEYDOWN)
      g_engine.OnKey(lparam);
  }

//+------------------------------------------------------------------+

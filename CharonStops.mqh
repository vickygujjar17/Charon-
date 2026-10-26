//+------------------------------------------------------------------+
//|                                                  CharonStops.mqh |
//|  Strict stop loss: arming, enforcement, repair, RR projection     |
//|  and the half-risk / breakeven ladder.                           |
//|                                                                  |
//|  INVARIANT: Charon never lets a managed position run without a    |
//|  stop for more than a few ticks. If the stop cannot be placed it  |
//|  is retried, then the position is flattened (configurable).       |
//+------------------------------------------------------------------+
#property copyright "Charon"
#property version   "1.07"

#ifndef __CHARON_STOPS__
#define __CHARON_STOPS__

#include "CharonDefines.mqh"
#include "CharonMath.mqh"
#include "CharonRisk.mqh"
#include "CharonTrade.mqh"

//--- outcome of a stop enforcement attempt
enum ENUM_CHARON_STOP_RESULT
  {
   CH_STOP_OK       = 0,   // stop already valid
   CH_STOP_ATTACHED = 1,   // stop was written this tick
   CH_STOP_CLOSED   = 2,   // position was flattened
   CH_STOP_LOGGED   = 3,   // log-only mode, position left unprotected
   CH_STOP_FAILED   = 4    // broker rejected, will retry
  };

//+------------------------------------------------------------------+
//| CCharonStops                                                     |
//+------------------------------------------------------------------+
class CCharonStops
  {
private:
   CCharonRisk      *m_risk;
   SCharonExecOpts   m_opts;

   double            ClampStopToMarket(const double stop, const bool isLong) const;
   bool              StopIsBrokerValid(const double stop, const bool isLong) const;
   bool              StopIsProtective(const double stop, const bool isLong) const;

public:
                     CCharonStops(void);
   void              Init(CCharonRisk *risk, const SCharonExecOpts &opts);

   //--- stop geometry
   double            InitialStopPrice(const double entry, const bool isLong);
   double            TargetPrice(const double entry, const double riskPrice, const bool isLong,
                                 bool &configured);
   double            RPrice(const double entry, const double riskPrice,
                            const double rr, const bool isLong);

   //--- enforcement
   ENUM_CHARON_STOP_RESULT EnforceStrictStop(const ulong ticket, const double entry,
                                             const bool isLong, double &armedSl,
                                             string &note);
   bool              ApplyStop(const ulong ticket, const double stop, const double tp,
                               string &err);
   bool              ApplyTarget(const ulong ticket, const double stop, const double tp,
                                 string &err);

   //--- protection ladder (R driven)
   bool              LadderTarget(const double entry, const double riskPrice, const bool isLong,
                                  const double progressRR, double &stopOut, string &label);
  };

//+------------------------------------------------------------------+
CCharonStops::CCharonStops(void)
  {
   m_risk = NULL;
   m_opts.symbol   = "";
   m_opts.slippage = 0;
   m_opts.retries  = 1;
   m_opts.delayMs  = 0;
  }

//+------------------------------------------------------------------+
void CCharonStops::Init(CCharonRisk *risk, const SCharonExecOpts &opts)
  {
   m_risk = risk;
   m_opts = opts;
  }

//+------------------------------------------------------------------+
//| Never return a stop that sits inside the broker's stop level     |
//+------------------------------------------------------------------+
double CCharonStops::ClampStopToMarket(const double stop, const bool isLong) const
  {
   if(m_risk == NULL || !MathIsValidNumber(stop) || stop <= 0.0)
      return 0.0;

   double buffer = m_risk.StopDistanceWithBuffer();
   if(buffer <= 0.0)
      return m_risk.NormalizePrice(stop);

   double bid = m_risk.Bid();
   double ask = m_risk.Ask();
   if(bid <= 0.0 || ask <= 0.0)
      return 0.0;

   double clamped = stop;
   if(isLong && stop > bid - buffer)
      clamped = bid - buffer;
   if(!isLong && stop < ask + buffer)
      clamped = ask + buffer;

   clamped = m_risk.NormalizePrice(clamped);
   return (clamped > 0.0 ? clamped : 0.0);
  }

//+------------------------------------------------------------------+
//| Check a candidate stop against the live market                   |
//|  Used for NEW stops: they must clear the broker minimum distance.|
//+------------------------------------------------------------------+
bool CCharonStops::StopIsBrokerValid(const double stop, const bool isLong) const
  {
   if(m_risk == NULL || !MathIsValidNumber(stop) || stop <= 0.0)
      return false;
   double buffer = m_risk.StopDistanceWithBuffer();
   double bid = m_risk.Bid();
   double ask = m_risk.Ask();
   if(bid <= 0.0 || ask <= 0.0)
      return false;
   if(isLong)
      return (stop <= bid - buffer + CHARON_EPS);
   return (stop >= ask + buffer - CHARON_EPS);
  }

//+------------------------------------------------------------------+
//| Is an EXISTING stop still protective?                            |
//|                                                                  |
//|  Deliberately looser than StopIsBrokerValid: a stop already on the|
//|  position may legitimately sit closer to price than the broker's  |
//|  minimum distance (it was placed earlier, or it is a breakeven    |
//|  level). It only needs to be on the correct side of the market,   |
//|  otherwise re-arming it would WIDEN the stop and increase risk.   |
//+------------------------------------------------------------------+
bool CCharonStops::StopIsProtective(const double stop, const bool isLong) const
  {
   if(m_risk == NULL || !MathIsValidNumber(stop) || stop <= 0.0)
      return false;
   double bid = m_risk.Bid();
   double ask = m_risk.Ask();
   if(bid <= 0.0 || ask <= 0.0)
      return false;
   if(isLong)
      return (stop < bid);            // a long stop must sit below the bid
   return (stop > ask);               // a short stop must sit above the ask
  }

//+------------------------------------------------------------------+
//| Initial strict stop price for a fresh entry                      |
//+------------------------------------------------------------------+
double CCharonStops::InitialStopPrice(const double entry, const bool isLong)
  {
   if(m_risk == NULL || !MathIsValidNumber(entry) || entry <= 0.0)
      return 0.0;

   double atr = 0.0;
   if(InpSlUnit == CHARON_SL_ATR)
     {
      atr = m_risk.Atr();
      if(atr <= 0.0)
         return 0.0;                  // caller falls back / waits for ATR
     }

   double distance = ChSlDistance(InpSlUnit, InpSlPoints, InpSlAtrMult, atr,
                                  InpSlPricePct, entry, m_risk.Point());
   if(distance <= 0.0)
      return 0.0;

   distance = MathMax(distance, m_risk.StopDistanceWithBuffer());

   double stop = ChStopFromDistance(entry, distance, isLong);
   if(!MathIsValidNumber(stop) || stop <= 0.0)
      return 0.0;

   stop = ClampStopToMarket(stop, isLong);
   if(!StopIsBrokerValid(stop, isLong))
      return 0.0;
   return stop;
  }

//+------------------------------------------------------------------+
//| RR / points target price                                         |
//+------------------------------------------------------------------+
double CCharonStops::TargetPrice(const double entry, const double riskPrice,
                                 const bool isLong, bool &configured)
  {
   configured = false;
   if(m_risk == NULL || !MathIsValidNumber(entry) || entry <= 0.0)
      return 0.0;

   double tp = 0.0;
   switch(InpTpUnit)
     {
      case CHARON_TP_RR:
         if(riskPrice > 0.0 && InpTpRR > 0.0)
            tp = ChPriceAtRR(entry, riskPrice, InpTpRR, isLong);
         break;
      case CHARON_TP_POINTS:
         if(InpTpPoints > 0.0)
           {
            double dist = ChPriceFromPoints(InpTpPoints, m_risk.Point());
            tp = (isLong ? entry + dist : entry - dist);
           }
         break;
      case CHARON_TP_NONE:
      default:
         tp = 0.0;
         break;
     }

   if(!MathIsValidNumber(tp) || tp <= 0.0)
      return 0.0;

   double buffer = m_risk.StopDistanceWithBuffer();
   double bid = m_risk.Bid();
   double ask = m_risk.Ask();
   if(bid <= 0.0 || ask <= 0.0)
      return 0.0;
   //--- a target closer than the stop level is meaningless
   if(isLong && tp < ask + buffer)
      return 0.0;
   if(!isLong && tp > bid - buffer)
      return 0.0;

   configured = true;
   return m_risk.NormalizePrice(tp);
  }

//+------------------------------------------------------------------+
//| Price at an arbitrary R multiple                                 |
//+------------------------------------------------------------------+
double CCharonStops::RPrice(const double entry, const double riskPrice,
                            const double rr, const bool isLong)
  {
   if(m_risk == NULL || riskPrice <= 0.0 || !MathIsValidNumber(rr))
      return 0.0;
   double p = ChPriceAtRR(entry, riskPrice, rr, isLong);
   if(!MathIsValidNumber(p) || p <= 0.0)
      return 0.0;
   return m_risk.NormalizePrice(p);
  }

//+------------------------------------------------------------------+
//| Guarantee a valid stop exists on the position                    |
//|                                                                  |
//|  Called every tick for every managed ticket. armedSl returns the  |
//|  stop that is now live (0.0 when unprotected).                    |
//+------------------------------------------------------------------+
ENUM_CHARON_STOP_RESULT CCharonStops::EnforceStrictStop(const ulong ticket, const double entry,
                                                        const bool isLong, double &armedSl,
                                                        string &note)
  {
   armedSl = 0.0;
   note    = "";
   if(m_risk == NULL || ticket == 0)
     {
      note = "enforcer not initialised";
      return CH_STOP_FAILED;
     }

   //--- 1) read what the broker actually holds
   double liveSl = 0.0;
   double liveTp = 0.0;
   if(!PositionSelectByTicket(ticket))
     {
      note = "position closed";
      return CH_STOP_FAILED;
     }
   liveSl = PositionGetDouble(POSITION_SL);
   liveTp = PositionGetDouble(POSITION_TP);

   //--- 2) an existing stop is respected whenever it is still protective.
   //--- NOTE: the test is against the MARKET, not against entry. A long whose
   //--- stop has been trailed above entry is valid and already locked in
   //--- profit; testing "sl < entry" would wrongly re-arm a wider stop.
   if(MathIsValidNumber(liveSl) && liveSl > 0.0)
     {
      if(StopIsProtective(liveSl, isLong))
        {
         armedSl = liveSl;
         return CH_STOP_OK;
        }
      note = StringFormat("stop %.5f is not protective for a %s at bid %.5f/ask %.5f - rearming",
                          liveSl, (isLong ? "long" : "short"), m_risk.Bid(), m_risk.Ask());
      PrintFormat("%s %s", CHARON_NAME, note);
     }

   //--- 3) missing stop: decide what to do
   if(InpMissingSlAction == CHARON_SL_LOG_ONLY)
     {
      note = "no stop and log-only mode - position left unprotected";
      PrintFormat("%s %s (#%I64u)", CHARON_NAME, note, ticket);
      return CH_STOP_LOGGED;
     }

   if(InpMissingSlAction == CHARON_SL_CLOSE)
     {
      string cerr = "";
      if(ChExecClosePosition(m_opts, ticket, 0.0, cerr))
        {
         note = "flattened: no valid stop could be armed";
         PrintFormat("%s %s", CHARON_NAME, note);
         return CH_STOP_CLOSED;
        }
      note = "close attempt failed: " + cerr;
      PrintFormat("%s %s", CHARON_NAME, note);
      return CH_STOP_FAILED;
     }

   //--- 4) attach a strict stop
   double candidate = InitialStopPrice(entry, isLong);
   if(candidate <= 0.0)
     {
      //--- ATR not ready yet, or price sits on the level: fall back to
      //--- the tightest stop the broker will accept
      double buffer = m_risk.StopDistanceWithBuffer();
      double bid = m_risk.Bid();
      double ask = m_risk.Ask();
      if(buffer > 0.0 && bid > 0.0 && ask > 0.0)
         candidate = m_risk.NormalizePrice(isLong ? (bid - buffer) : (ask + buffer));
     }

   if(candidate <= 0.0 || !StopIsBrokerValid(candidate, isLong))
     {
      note = StringFormat("no valid stop can be placed (candidate %.5f)", candidate);
      PrintFormat("%s %s (#%I64u)", CHARON_NAME, note, ticket);
      return CH_STOP_FAILED;
     }

   string err = "";
   double keepTp = (MathIsValidNumber(liveTp) && liveTp > 0.0 ? liveTp : 0.0);
   if(!ChExecModifyStops(m_opts, ticket, candidate, keepTp, err))
     {
      note = "attach rejected: " + err;
      PrintFormat("%s %s (#%I64u)", CHARON_NAME, note, ticket);
      return CH_STOP_FAILED;
     }

   armedSl = candidate;
   note = StringFormat("strict stop armed at %.5f", candidate);
   PrintFormat("%s %s (#%I64u)", CHARON_NAME, note, ticket);
   return CH_STOP_ATTACHED;
  }

//+------------------------------------------------------------------+
//| Write a stop/target pair (target 0 = leave untouched)            |
//+------------------------------------------------------------------+
bool CCharonStops::ApplyStop(const ulong ticket, const double stop, const double tp,
                             string &err)
  {
   err = "";
   if(m_risk == NULL || ticket == 0)
     {
      err = "invalid request";
      return false;
     }
   if(!MathIsValidNumber(stop) || stop <= 0.0)
     {
      err = "invalid stop price";
      return false;
     }
   return ChExecModifyStops(m_opts, ticket, m_risk.NormalizePrice(stop),
                            (MathIsValidNumber(tp) && tp > 0.0 ? m_risk.NormalizePrice(tp) : 0.0),
                            err);
  }

//+------------------------------------------------------------------+
//| Write a target (kept as a separate name for call-site clarity)   |
//+------------------------------------------------------------------+
bool CCharonStops::ApplyTarget(const ulong ticket, const double stop, const double tp,
                               string &err)
  {
   return ApplyStop(ticket, stop, tp, err);
  }

//+------------------------------------------------------------------+
//| R-driven protection ladder: half-risk, then breakeven.           |
//| Returns the most protective stop the configured stages allow and |
//| never loosens an existing stop (the caller enforces that).       |
//+------------------------------------------------------------------+
bool CCharonStops::LadderTarget(const double entry, const double riskPrice, const bool isLong,
                                const double progressRR, double &stopOut, string &label)
  {
   stopOut = 0.0;
   label   = "";
   if(m_risk == NULL || riskPrice <= 0.0)
      return false;

   double candidate = 0.0;

   //--- half-risk stage (default 0.5R), always behind breakeven
   if(InpHalfRiskMode != CHARON_HALF_OFF && InpHalfRiskRR > 0.0 &&
      progressRR >= InpHalfRiskRR)
     {
      if(InpHalfRiskMode == CHARON_HALF_HALVE)
        {
         double halfRiskStop = (isLong ? entry - riskPrice * 0.5
                                       : entry + riskPrice * 0.5);
         candidate = m_risk.NormalizePrice(halfRiskStop);
         if(!StopIsBrokerValid(candidate, isLong))
            candidate = 0.0;
         if(candidate > 0.0)
            label = StringFormat("half-risk %.2fR", InpHalfRiskRR);
        }
      else
        {
         candidate = m_risk.NormalizePrice(entry);
         if(!StopIsBrokerValid(candidate, isLong))
            candidate = 0.0;
         if(candidate > 0.0)
            label = StringFormat("half-risk breakeven %.2fR", InpHalfRiskRR);
        }
     }

   //--- breakeven stage: never worse than entry once 1R is reached
   if(InpBreakevenRR > 0.0 && progressRR >= InpBreakevenRR)
     {
      double beStop = m_risk.NormalizePrice(entry);
      if(StopIsBrokerValid(beStop, isLong))
        {
         bool better = (candidate <= 0.0 ||
                        (isLong ? beStop > candidate : beStop < candidate));
         if(better)
           {
            candidate = beStop;
            label = StringFormat("breakeven %.2fR", InpBreakevenRR);
           }
        }
     }

   if(candidate <= 0.0)
      return false;
   stopOut = candidate;
   return true;
  }

//+------------------------------------------------------------------+
#endif // __CHARON_STOPS__
//+------------------------------------------------------------------+

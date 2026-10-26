//+------------------------------------------------------------------+
//|                                                   CharonTrail.mqh |
//|  Trailing stop engine: four independent modes, all driven off a   |
//|  per-position high-water mark (HWM). A trailing update is only    |
//|  ever accepted when it is strictly more protective than the stop  |
//|  already on the position.                                        |
//+------------------------------------------------------------------+
#property copyright "Charon"
#property version   "1.07"

#ifndef __CHARON_TRAIL__
#define __CHARON_TRAIL__

#include "CharonDefines.mqh"
#include "CharonMath.mqh"
#include "CharonRisk.mqh"

//+------------------------------------------------------------------+
//| CCharonTrail                                                     |
//+------------------------------------------------------------------+
class CCharonTrail
  {
private:
   CCharonRisk      *m_risk;
   ENUM_CHARON_TRAIL_MODE m_mode;   // live mode; seeds from InpTrailMode at Init()

public:
                     CCharonTrail(void);
   void              Init(CCharonRisk *risk);

   //--- the active mode is state, not a constant: the dashboard may change it
   //--- between ticks. Init() seeds it from the InpTrailMode input, so an
   //--- untouched chart behaves exactly as before.
   void              SetMode(const ENUM_CHARON_TRAIL_MODE mode);
   ENUM_CHARON_TRAIL_MODE Mode(void) const { return m_mode; }

   //--- true once the activation threshold has been reached. startRR,
   //--- startPoints and startPrice are the per-ticket thresholds: 0 means "use
   //--- the chart input", which is why an untouched ticket behaves exactly as
   //--- before. startPrice is an absolute level the market must reach; when it
   //--- is stated it wins, because a price the operator read off the chart is
   //--- not something the EA should second-guess.
   bool              IsActivated(const double entry, const double riskPrice,
                                 const double hwm, const bool isLong,
                                 const double startRR = 0.0,
                                 const double startPoints = 0.0,
                                 const double startPrice = 0.0) const;

   //--- trailing distance in price for the configured mode
   double            Distance(void);

   //--- candidate stop for the current mode, 0.0 when not applicable
   double            Candidate(const double entry, const double riskPrice,
                               const double hwm, const bool isLong,
                               const double currentStop, string &label);

   //--- convenience: activated + candidate + improvement test
   bool              NextStop(const double entry, const double riskPrice,
                              const double hwm, const bool isLong,
                              const double currentStop, double &stopOut, string &label,
                              const double startRR = 0.0,
                              const double startPoints = 0.0,
                              const double startPrice = 0.0);
  };

//+------------------------------------------------------------------+
CCharonTrail::CCharonTrail(void)
  {
   m_risk = NULL;
   m_mode = InpTrailMode;           // seed now so a bare instance is sane
  }

//+------------------------------------------------------------------+
void CCharonTrail::Init(CCharonRisk *risk)
  {
   m_risk = risk;
   m_mode = InpTrailMode;           // the input is the default, never a lock
  }

//+------------------------------------------------------------------+
void CCharonTrail::SetMode(const ENUM_CHARON_TRAIL_MODE mode)
  {
   //--- reject anything outside the declared enum rather than trusting it
   if(mode < CHARON_TRAIL_OFF || mode > CHARON_TRAIL_RR_LOCK)
      return;
   m_mode = mode;
  }

//+------------------------------------------------------------------+
//| Activation gate                                                  |
//|  * an absolute price, when stated, is the only test that counts  |
//|  * otherwise R activation (InpTrailStartRR) is primary           |
//|  * a points activation may also arm it (either may trigger)      |
//+------------------------------------------------------------------+
bool CCharonTrail::IsActivated(const double entry, const double riskPrice,
                               const double hwm, const bool isLong,
                               const double startRR, const double startPoints,
                               const double startPrice) const
  {
   if(m_mode == CHARON_TRAIL_OFF || m_risk == NULL)
      return false;
   if(!MathIsValidNumber(hwm) || hwm <= 0.0)
      return false;

   //--- an absolute-price ticket. "Touched or crossed" is tested, never
   //--- equality: a level the market gaps over must still arm the trail, and
   //--- an exact-tick comparison would silently never fire.
   if(MathIsValidNumber(startPrice) && startPrice > 0.0)
      return ChPriceAtOrBeyond(hwm, startPrice, isLong);

   //--- a ticket's own thresholds win over the chart inputs. A negative
   //--- value is not a threshold, so it is ignored rather than trusted.
   double wantRR     = (MathIsValidNumber(startRR) && startRR > 0.0
                        ? startRR : InpTrailStartRR);
   double wantPoints = (MathIsValidNumber(startPoints) && startPoints > 0.0
                        ? startPoints : InpTrailStartPoints);

   if(riskPrice > 0.0 && wantRR > 0.0)
     {
      double progress = ChProgressRR(entry, riskPrice, hwm, isLong);
      if(progress >= wantRR)
         return true;
     }

   //--- points-based activation measured on the HWM
   if(wantPoints > 0.0)
     {
      double travelled = ChPointsFromPrice(MathAbs(hwm - entry), m_risk.Point());
      if(travelled >= wantPoints)
         return true;
     }

   //--- when neither activation is configured, trail from the start
   if(wantRR <= 0.0 && wantPoints <= 0.0)
      return true;

   return false;
  }

//+------------------------------------------------------------------+
//| Fixed trailing distance for modes that use one                   |
//+------------------------------------------------------------------+
double CCharonTrail::Distance(void)
  {
   if(m_risk == NULL)
      return 0.0;

   switch(m_mode)
     {
      case CHARON_TRAIL_POINTS:
         return ChPriceFromPoints(InpTrailPoints, m_risk.Point());
      case CHARON_TRAIL_ATR:
        {
         double atr = m_risk.Atr();
         if(atr <= 0.0 || InpTrailAtrMult <= 0.0)
            return 0.0;
         return atr * InpTrailAtrMult;
        }
      default:
         return 0.0;
     }
  }

//+------------------------------------------------------------------+
//| Candidate stop for the active mode                               |
//|  0.0 = mode does not produce a stop right now (no ATR, no gain)  |
//+------------------------------------------------------------------+
double CCharonTrail::Candidate(const double entry, const double riskPrice,
                               const double hwm, const bool isLong,
                               const double currentStop, string &label)
  {
   label = "";
   if(m_risk == NULL || !MathIsValidNumber(hwm) || hwm <= 0.0)
      return 0.0;

   double candidate = 0.0;

   switch(m_mode)
     {
      case CHARON_TRAIL_POINTS:
      case CHARON_TRAIL_ATR:
        {
         double dist = Distance();
         if(dist <= 0.0)
            return 0.0;
         candidate = (isLong ? hwm - dist : hwm + dist);
         label = (m_mode == CHARON_TRAIL_ATR
                  ? StringFormat("ATR trail %.2fx", InpTrailAtrMult)
                  : StringFormat("points trail %.0f", InpTrailPoints));
         break;
        }

      case CHARON_TRAIL_HWM_PERCENT:
        {
         //--- give back at most N% of the gain already earned
         double gain = (isLong ? hwm - entry : entry - hwm);
         if(gain <= 0.0)
            return 0.0;
         if(InpTrailGivebackPct <= 0.0 || InpTrailGivebackPct >= 100.0)
            return 0.0;
         double keep = gain * (1.0 - InpTrailGivebackPct / 100.0);
         candidate = (isLong ? entry + keep : entry - keep);
         label = StringFormat("HWM trail %.1f%% giveback", InpTrailGivebackPct);
         break;
        }

      case CHARON_TRAIL_RR_LOCK:
        {
         //--- lock whatever has been earned minus a fixed R giveback
         if(riskPrice <= 0.0 || InpTrailGivebackRR < 0.0)
            return 0.0;
         double progress = ChProgressRR(entry, riskPrice, hwm, isLong);
         double lockRR = progress - InpTrailGivebackRR;
         if(lockRR <= 0.0)
            return 0.0;
         candidate = ChPriceAtRR(entry, riskPrice, lockRR, isLong);
         label = StringFormat("RR lock %.2fR (giveback %.2fR)", lockRR, InpTrailGivebackRR);
         break;
        }

      case CHARON_TRAIL_OFF:
      default:
         return 0.0;
     }

   if(!MathIsValidNumber(candidate) || candidate <= 0.0)
      return 0.0;

   candidate = m_risk.NormalizePrice(candidate);
   return (candidate > 0.0 ? candidate : 0.0);
  }

//+------------------------------------------------------------------+
//| Full step: activation, candidate, improvement gate               |
//+------------------------------------------------------------------+
bool CCharonTrail::NextStop(const double entry, const double riskPrice,
                            const double hwm, const bool isLong,
                            const double currentStop, double &stopOut, string &label,
                            const double startRR, const double startPoints,
                            const double startPrice)
  {
   stopOut = 0.0;
   label   = "";
   if(m_risk == NULL || m_mode == CHARON_TRAIL_OFF)
      return false;
   if(!IsActivated(entry, riskPrice, hwm, isLong, startRR, startPoints, startPrice))
      return false;

   double candidate = Candidate(entry, riskPrice, hwm, isLong, currentStop, label);
   if(candidate <= 0.0)
      return false;

   //--- never move a stop backwards; require the configured step
   double stepPrice = ChPriceFromPoints(InpTrailStepPoints, m_risk.Point());
   if(!ChStopImproves(currentStop, candidate, isLong, stepPrice))
      return false;

   //--- a trailing stop must stay on the protective side of price
   double buffer = m_risk.StopDistanceWithBuffer();
   double bid = m_risk.Bid();
   double ask = m_risk.Ask();
   if(buffer <= 0.0 || bid <= 0.0 || ask <= 0.0)
      return false;
   if(isLong && candidate > bid - buffer + CHARON_EPS)
      return false;
   if(!isLong && candidate < ask + buffer - CHARON_EPS)
      return false;

   stopOut = candidate;
   return true;
  }

//+------------------------------------------------------------------+
#endif // __CHARON_TRAIL__
//+------------------------------------------------------------------+

//+------------------------------------------------------------------+
//|                                                 CharonPyramid.mqh |
//|  Pyramiding / scale-in model.                                    |
//|                                                                  |
//|  A tier may only fire when ALL of the following hold:            |
//|    1. pyramiding is enabled and legs < InpPyramidMaxLegs          |
//|    2. the previous leg has reached the tier's R trigger           |
//|    3. the cooldown elapsed                                      |
//|    4. spread is inside the configured ceiling                    |
//|    5. the resulting BASKET risk stays under InpPyramidMaxRiskPct  |
//|    6. (MANUAL trigger only) the trader has armed the ladder       |
//|                                                                  |
//|  Scale-in legs are opened with a strict stop already attached so  |
//|  the basket is never unprotected, not even for one tick.          |
//+------------------------------------------------------------------+
#property copyright "Charon"
#property version   "1.06"

#ifndef __CHARON_PYRAMID__
#define __CHARON_PYRAMID__

#include "CharonDefines.mqh"
#include "CharonMath.mqh"
#include "CharonRisk.mqh"
#include "CharonTrade.mqh"

//--- why a tier did or did not fire
enum ENUM_CHARON_TIER_DECISION
  {
   CH_TIER_NONE      = 0,  // nothing to do
   CH_TIER_READY     = 1,  // every gate passed
   CH_TIER_DISABLED  = 2,
   CH_TIER_MAXLEGS   = 3,
   CH_TIER_NOTRIGGER = 4,
   CH_TIER_COOLDOWN  = 5,
   CH_TIER_SPREAD    = 6,
   CH_TIER_RISK      = 7,
   CH_TIER_LOTZERO   = 8,
   CH_TIER_SEND_FAIL = 9,
   CH_TIER_SEND_OK   = 10,
   CH_TIER_NOTARMED  = 11   // MANUAL mode and the trader has not armed
  };

//+------------------------------------------------------------------+
//| Tier ladder helpers (pure, no broker calls)                      |
//+------------------------------------------------------------------+
int ChTierCountAllowed(const int configuredMax)
  {
   //--- InpPyramidMaxLegs counts the primary leg, tiers are 2..N
   int legs = ChClampI(configuredMax, 1, CHARON_MAX_TIERS);
   return (legs - 1);                  // number of scale-ins permitted
  }

//+------------------------------------------------------------------+
//| Spec of tier number 'tier' (2-based)                             |
//+------------------------------------------------------------------+
bool ChTierSpec(const int tier, SCharonTierSpec &spec)
  {
   ZeroMemory(spec);
   switch(tier)
     {
      case 2:
         spec.index = 2;
         spec.activationRR = InpPyramidT2RR;
         spec.lotMultiplier = InpPyramidT2Mult;
         return (spec.activationRR > 0.0 && spec.lotMultiplier > 0.0);
      case 3:
         spec.index = 3;
         spec.activationRR = InpPyramidT3RR;
         spec.lotMultiplier = InpPyramidT3Mult;
         return (spec.activationRR > 0.0 && spec.lotMultiplier > 0.0);
      case 4:
         spec.index = 4;
         spec.activationRR = InpPyramidT4RR;
         spec.lotMultiplier = InpPyramidT4Mult;
         return (spec.activationRR > 0.0 && spec.lotMultiplier > 0.0);
      default:
         return false;
     }
  }

//+------------------------------------------------------------------+
//| Basket risk with one hypothetical extra leg added                |
//|                                                                  |
//|  openRisk  = money already at risk on the live legs              |
//|  newRisk   = money the candidate leg risks over its own stop      |
//|  credit    = money already locked in by stops beyond entry        |
//|  The net figure is what the basket would risk after the add.     |
//+------------------------------------------------------------------+
double ChBasketRiskWithLeg(const double openRisk, const double newRisk,
                           const double lockedCredit)
  {
   double net = openRisk + newRisk - lockedCredit;
   if(!MathIsValidNumber(net))
      return DBL_MAX;
   return (net < 0.0 ? 0.0 : net);
  }

//+------------------------------------------------------------------+
//| Does the projected basket risk stay inside the pyramid ceiling?  |
//+------------------------------------------------------------------+
bool ChPyramidRiskAccepts(const double projectedRiskMoney, const double accountBase,
                          const double maxPct)
  {
   if(accountBase <= 0.0 || maxPct <= 0.0)
      return false;
   if(!MathIsValidNumber(projectedRiskMoney) || projectedRiskMoney < 0.0)
      return false;
   return (projectedRiskMoney <= accountBase * maxPct / 100.0 + CHARON_EPS);
  }

//+------------------------------------------------------------------+
//| CCharonPyramid - gate evaluation and leg execution               |
//+------------------------------------------------------------------+
class CCharonPyramid
  {
private:
   CCharonRisk      *m_risk;
   SCharonExecOpts   m_opts;
   datetime          m_lastLegTime;
   bool              m_armed;          // MANUAL trigger: the trader armed a tier
   datetime          m_armedAt;        // when the arm was set (expiry clock)

public:
                     CCharonPyramid(void);
   void              Init(CCharonRisk *risk, const SCharonExecOpts &opts);

   //--- manual trigger state
   void              Arm(void);
   void              ClearArm(void);
   bool              IsArmed(void) const { return m_armed; }
   datetime          ArmedAt(void) const { return m_armedAt; }

   //--- gates, evaluated in a fixed order so logs explain refusals
   ENUM_CHARON_TIER_DECISION Evaluate(const int legs, const int nextTier, const int dirSign,
                                      const double progressRR,
                                      const double projectedRiskMoney,
                                      const double accountBase);
   void              NoteLegOpened(void);

   //--- the only place a pyramid leg is sent from
   ENUM_CHARON_TIER_DECISION OpenLeg(const int tier, const ENUM_POSITION_TYPE posType,
                                     const double refRiskPrice,
                                     const double projectedRiskMoney, const double accountBase,
                                     ulong &ticket, double &fillPrice, string &note);
  };

//+------------------------------------------------------------------+
CCharonPyramid::CCharonPyramid(void)
  {
   m_risk = NULL;
   m_lastLegTime = 0;
   m_armed = false;
   m_armedAt = 0;
   m_opts.symbol   = "";
   m_opts.slippage = 0;
   m_opts.retries  = 1;
   m_opts.delayMs  = 0;
  }

//+------------------------------------------------------------------+
void CCharonPyramid::Init(CCharonRisk *risk, const SCharonExecOpts &opts)
  {
   m_risk = risk;
   m_opts = opts;
   m_lastLegTime = 0;
   m_armed = false;
   m_armedAt = 0;
  }

//+------------------------------------------------------------------+
//| MANUAL trigger: the trader authorises the next tier              |
//|                                                                  |
//|  Arming only satisfies the trigger gate. Every other ceiling      |
//|  (max legs, cooldown, spread, basket risk) still applies, so an   |
//|  arm can never force an exposure add the risk model rejects.      |
//+------------------------------------------------------------------+
void CCharonPyramid::Arm(void)
  {
   m_armed   = true;
   m_armedAt = TimeCurrent();
  }

//+------------------------------------------------------------------+
void CCharonPyramid::ClearArm(void)
  {
   m_armed   = false;
   m_armedAt = 0;
  }

//+------------------------------------------------------------------+
//| Timestamp of the last leg Charon opened                          |
//+------------------------------------------------------------------+
void CCharonPyramid::NoteLegOpened(void)
  {
   m_lastLegTime = TimeCurrent();
  }

//+------------------------------------------------------------------+
//| Evaluate every gate. Order matters: the reported reason is the   |
//| first gate that failed.                                          |
//+------------------------------------------------------------------+
ENUM_CHARON_TIER_DECISION CCharonPyramid::Evaluate(const int legs, const int nextTier,
                                                   const int dirSign,
                                                   const double progressRR,
                                                   const double projectedRiskMoney,
                                                   const double accountBase)
  {
   if(!InpPyramidEnable)
      return CH_TIER_DISABLED;
   if(m_risk == NULL)
      return CH_TIER_DISABLED;

   //--- legs counts the positions currently open (>=1 when a basket exists);
   //--- nextTier is the tier being requested, which is NOT necessarily
   //--- legs+1 when the human opened more than one primary position.
   int maxLegs = ChClampI(InpPyramidMaxLegs, 1, CHARON_MAX_TIERS);
   if(legs >= maxLegs)
      return CH_TIER_MAXLEGS;
   if(nextTier < 2 || nextTier > maxLegs)
      return CH_TIER_MAXLEGS;

   //--- trigger is measured on the tier that is about to be added
   SCharonTierSpec spec;
   if(!ChTierSpec(nextTier, spec))
      return CH_TIER_NONE;

   //--- MANUAL trigger: the R trigger alone is not enough, the trader must
   //--- have armed the ladder. Placed before the R check so "not armed" is
   //--- reported as such instead of masquerading as "trigger not reached".
   if(InpPyramidTrigger == CHARON_PYR_MANUAL)
     {
      if(!ChArmIsLive(m_armed, m_armedAt, TimeCurrent(), InpPyramidArmTimeoutSec))
         return CH_TIER_NOTARMED;
     }

   if(progressRR < spec.activationRR - CHARON_EPS)
      return CH_TIER_NOTRIGGER;

   //--- direction must be a single, unambiguous basket side
   if(dirSign == 0)
      return CH_TIER_NONE;

   //--- cooldown
   if(InpPyramidCooldownSec > 0 && m_lastLegTime > 0)
     {
      long elapsed = (long)(TimeCurrent() - m_lastLegTime);
      if(elapsed < (long)InpPyramidCooldownSec)
         return CH_TIER_COOLDOWN;
     }

   //--- spread ceiling (0 disables the check)
   if(InpMaxSpreadPoints > 0.0 && m_risk.SpreadPoints() > InpMaxSpreadPoints)
      return CH_TIER_SPREAD;

   //--- basket risk ceiling
   if(!ChPyramidRiskAccepts(projectedRiskMoney, accountBase, InpPyramidMaxRiskPct))
      return CH_TIER_RISK;

   return CH_TIER_READY;
  }

//+------------------------------------------------------------------+
//| Open one pyramid leg with a strict stop already attached.        |
//|                                                                  |
//|  The leg's own stop is derived with the same strict-stop rules   |
//|  used for adoption, so a scale-in can never be unprotected.      |
//+------------------------------------------------------------------+
ENUM_CHARON_TIER_DECISION CCharonPyramid::OpenLeg(const int tier, const ENUM_POSITION_TYPE posType,
                                                  const double refRiskPrice,
                                                  const double projectedRiskMoney,
                                                  const double accountBase,
                                                  ulong &ticket, double &fillPrice, string &note)
  {
   ticket    = 0;
   fillPrice = 0.0;
   note      = "";

   if(m_risk == NULL)
     {
      note = "pyramid engine not initialised";
      return CH_TIER_DISABLED;
     }

   SCharonTierSpec spec;
   if(!ChTierSpec(tier, spec))
     {
      note = StringFormat("tier %d is not configured", tier);
      return CH_TIER_NONE;
     }

   //--- entry the leg is about to pay
   bool isLong = (posType == POSITION_TYPE_BUY);
   double price = (isLong ? m_risk.Ask() : m_risk.Bid());
   if(!MathIsValidNumber(price) || price <= 0.0)
     {
      note = "no price available";
      return CH_TIER_SEND_FAIL;
     }

   //--- strict stop for the new leg, at the same distance as the basket's R
   double distance = (refRiskPrice > 0.0 ? refRiskPrice : 0.0);
   if(distance <= 0.0)
     {
      note = "basket has no R reference - refusing to size a leg";
      return CH_TIER_RISK;
     }
   distance = MathMax(distance, m_risk.StopDistanceWithBuffer());
   double stop = m_risk.NormalizePrice(isLong ? (price - distance) : (price + distance));
   if(stop <= 0.0 || (isLong && stop >= price) || (!isLong && stop <= price))
     {
      note = "cannot derive a valid stop for the leg";
      return CH_TIER_SEND_FAIL;
     }

   //--- size the leg against its own stop, scaled by the tier multiplier
   bool sized = false;
   double lot = m_risk.LotForStop(distance, sized, true, spec.lotMultiplier);
   if(!sized || lot <= 0.0)
     {
      note = StringFormat("tier %d cannot be sized inside the risk budget", tier);
      return CH_TIER_LOTZERO;
     }

   //--- final basket-risk ceiling with the leg actually included
   double legRisk = m_risk.MoneyAtDistance(distance, lot);
   double projected = (MathIsValidNumber(projectedRiskMoney) ? projectedRiskMoney + legRisk
                                                             : legRisk);
   if(!ChPyramidRiskAccepts(projected, accountBase, InpPyramidMaxRiskPct))
     {
      note = StringFormat("tier %d would push basket risk to %.2f%% (cap %.2f%%)",
                          tier, (accountBase > 0.0 ? projected / accountBase * 100.0 : 0.0),
                          InpPyramidMaxRiskPct);
      return CH_TIER_RISK;
     }

   //--- target mirrors the strict stop geometry, exactly like adoption
   double tp = 0.0;
   if(InpTpUnit == CHARON_TP_RR && InpTpRR > 0.0)
      tp = ChPriceAtRR(price, distance, InpTpRR, isLong);
   else if(InpTpUnit == CHARON_TP_POINTS && InpTpPoints > 0.0)
     {
      double tdist = ChPriceFromPoints(InpTpPoints, m_risk.Point());
      tp = (isLong ? price + tdist : price - tdist);
     }
   if(!MathIsValidNumber(tp) || tp <= 0.0)
      tp = 0.0;
   else
      tp = m_risk.NormalizePrice(tp);

   string err = "";
   string comment = StringFormat("%s T%d", CHARON_COMMENT, tier);
   if(!ChExecOpenMarket(m_opts, posType, lot, stop, tp, InpMagic, comment,
                        ticket, fillPrice, err))
     {
      note = StringFormat("tier %d send failed: %s", tier, err);
      return CH_TIER_SEND_FAIL;
     }

   NoteLegOpened();
   note = StringFormat("tier %d opened %.2f lots @ %.5f, stop %.5f, risk %.2f",
                       tier, lot, fillPrice, stop, legRisk);
   PrintFormat("%s %s (#%I64u)", CHARON_NAME, note, ticket);
   return CH_TIER_SEND_OK;
  }

//+------------------------------------------------------------------+
#endif // __CHARON_PYRAMID__
//+------------------------------------------------------------------+

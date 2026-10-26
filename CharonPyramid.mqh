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
#property version   "1.07"

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
//| Is one stage of a ticket's plan usable as stated?                |
//|                                                                  |
//|  Deliberately per-kind. An RR stage must state a positive R        |
//|  multiple; a PRICE stage must state a positive absolute price. A   |
//|  PRICE stage has no R trigger at all, so the older                 |
//|  "activationRR > 0" predicate would have rejected every one of     |
//|  them -- which is exactly the bug this function exists to avoid.   |
//+------------------------------------------------------------------+
bool ChStageIsValid(const SCharonStage &stage)
  {
   if(!MathIsValidNumber(stage.trigger) || stage.trigger <= 0.0)
      return false;
   if(!MathIsValidNumber(stage.lotsMult) || stage.lotsMult <= 0.0)
      return false;
   if(stage.kind != (int)CHARON_STAGE_RR && stage.kind != (int)CHARON_STAGE_PRICE)
      return false;
   return true;
  }

//+------------------------------------------------------------------+
//| Copy a plan from one struct to another                           |
//|                                                                  |
//|  Explicit element-wise copy rather than '=' : a plan embeds a     |
//|  fixed array, and the safe way to move that between two structs   |
//|  is the one the compiler cannot misread. Both sides are cleared   |
//|  first, so no stale stage can survive a shorter plan.             |
//+------------------------------------------------------------------+
void ChCopyPlan(SCharonStagePlan &dst, const SCharonStagePlan &src)
  {
   ZeroMemory(dst);
   int count = ChClampI(src.count, 0, CHARON_MAX_TIERS - 1);
   for(int i = 0; i < count; i++)
      dst.stage[i] = src.stage[i];
   dst.count = count;
  }

//+------------------------------------------------------------------+
//| Append one stage to a plan, refusing anything unusable           |
//|                                                                  |
//|  The single place a stage enters a plan, so the ceiling and the   |
//|  per-kind validity rule cannot drift between the wire parser, the |
//|  watch file and the engine. Returns true when the stage landed;    |
//|  a full plan or an invalid stage is a silent refusal because the   |
//|  caller has already validated its own input.                      |
//+------------------------------------------------------------------+
bool ChAddStage(SCharonStagePlan &plan, const int kind,
                const double trigger, const double lotsMult)
  {
   if(plan.count < 0 || plan.count >= CHARON_MAX_TIERS - 1)
      return false;

   SCharonStage stage;
   stage.kind     = kind;
   stage.trigger  = trigger;
   stage.lotsMult = lotsMult;
   if(!ChStageIsValid(stage))
      return false;

   plan.stage[plan.count] = stage;
   plan.count++;
   return true;
  }

//+------------------------------------------------------------------+
//| Spec of 'tier' taken from a ticket's own plan                    |
//|                                                                  |
//|  Stage n drives tier n+2, because tier 1 IS the primary leg the    |
//|  operator already opened. Returns false when the plan does not     |
//|  reach that tier, which is the caller's cue to fall back to the    |
//|  chart-wide ladder in [5] PYRAMIDING.                              |
//+------------------------------------------------------------------+
bool ChPlanSpec(const SCharonStagePlan &plan, const int tier, SCharonTierSpec &spec)
  {
   ZeroMemory(spec);
   if(tier < 2 || tier > CHARON_MAX_TIERS)
      return false;

   int idx = tier - 2;
   if(idx < 0 || idx >= plan.count || idx >= CHARON_MAX_TIERS - 1)
      return false;

   const SCharonStage stage = plan.stage[idx];
   if(!ChStageIsValid(stage))
      return false;

   spec.index         = tier;
   spec.triggerKind   = (ENUM_CHARON_STAGE_KIND)stage.kind;
   spec.lotMultiplier = stage.lotsMult;
   if(spec.triggerKind == CHARON_STAGE_PRICE)
     {
      spec.triggerPrice = stage.trigger;
      spec.activationRR = 0.0;            // a price stage has no R trigger
     }
   else
     {
      spec.triggerRR    = stage.trigger;
      spec.activationRR = stage.trigger;  // legacy alias, kept in step
     }
   return true;
  }

//+------------------------------------------------------------------+
//| Spec of 'tier': the ticket's plan first, then the chart inputs   |
//|                                                                  |
//|  This ordering is what makes a two-stage plan reach exactly two    |
//|  scale-ins while a plan-less ticket behaves as it always has.      |
//+------------------------------------------------------------------+
bool ChResolveTierSpec(const SCharonStagePlan &plan, const int tier, SCharonTierSpec &spec)
  {
   if(ChPlanSpec(plan, tier, spec))
      return true;
   return ChTierSpec(tier, spec);
  }

//+------------------------------------------------------------------+
//| Effective leg ceiling for a ticket                               |
//|                                                                  |
//|  A plan states how many scale-ins its author intended, so it may   |
//|  raise the ceiling -- a three-stage ticket has to reach tier 4     |
//|  even when InpPyramidMaxLegs is the default 3. It may never        |
//|  LOWER it: the chart input is the operator's standing decision     |
//|  about how much exposure the symbol may reach, and a ticket is not |
//|  the place to widen or narrow that silently. CHARON_MAX_TIERS      |
//|  still binds, so a plan cannot invent a tier 5.                    |
//+------------------------------------------------------------------+
int ChPlanLegCeiling(const int planCount, const int configuredMax)
  {
   int configured = ChClampI(configuredMax, 1, CHARON_MAX_TIERS);
   if(planCount <= 0)
      return configured;
   int needed = planCount + 1;            // the primary leg plus one per stage
   return ChClampI(MathMax(configured, needed), 1, CHARON_MAX_TIERS);
  }

//+------------------------------------------------------------------+
//| May 'nextTier' be considered at all?                             |
//|                                                                  |
//|  Pure, so the leg ceiling is testable without a broker. It says    |
//|  nothing about the trigger, the cooldown, the spread or the risk   |
//|  ceiling: those are Evaluate()'s job and they stay there.          |
//+------------------------------------------------------------------+
bool ChPyramidAllowed(const bool enabled, const int legs, const int nextTier,
                      const int planCount, const int configuredMax)
  {
   if(!enabled)
      return false;
   int ceiling = ChPlanLegCeiling(planCount, configuredMax);
   if(legs >= ceiling)
      return false;
   if(nextTier < 2 || nextTier > ceiling)
      return false;
   return true;
  }

//+------------------------------------------------------------------+
//| Has this stage's trigger been reached?                           |
//|                                                                  |
//|  One predicate for both kinds, so callers never have to branch on  |
//|  the kind themselves -- and an RR stage compares with CHARON_EPS   |
//|  so that "1.99R holds, 2.00R fires" survives floating point.       |
//|  A stage with no usable trigger is never reached, which is the     |
//|  safe reading: a condition Charon cannot evaluate must not fire.   |
//+------------------------------------------------------------------+
bool ChStageTriggerReached(const SCharonTierSpec &spec, const double progressRR,
                           const double price, const bool isLong)
  {
   if(spec.triggerKind == CHARON_STAGE_PRICE)
      return ChPriceAtOrBeyond(price, spec.triggerPrice, isLong);

   if(!MathIsValidNumber(spec.triggerRR) || spec.triggerRR <= 0.0)
      return false;
   if(!MathIsValidNumber(progressRR))
      return false;
   return (progressRR >= spec.triggerRR - CHARON_EPS);
  }

//+------------------------------------------------------------------+
//| A plan as one persistable field                                  |
//|                                                                  |
//|  "kind,trigger,mult;kind,trigger,mult" -- empty means the ticket   |
//|  named no stages. Stages are ';'-separated and the numbers inside  |
//|  one stage are ','-separated, so the text rides in a               |
//|  tab-separated file without any escaping. Eight decimals keep a    |
//|  5-digit price exact and still round-trip an R multiple.          |
//|                                                                  |
//|  All-or-nothing: one unusable stage and the whole plan becomes the |
//|  empty string, because a plan that survives only in part is a       |
//|  ladder the operator never wrote. Plans are built through           |
//|  ChAddStage, so this normally cannot trigger.                      |
//+------------------------------------------------------------------+
string ChFormatStagePlan(const SCharonStagePlan &plan)
  {
   if(plan.count <= 0 || plan.count > CHARON_MAX_TIERS - 1)
      return "";

   string text = "";
   for(int i = 0; i < plan.count; i++)
     {
      if(!ChStageIsValid(plan.stage[i]))
         return "";
      if(i > 0)
         text += ";";
      text += StringFormat("%d,%s,%s",
                           plan.stage[i].kind,
                           DoubleToString(plan.stage[i].trigger, 8),
                           DoubleToString(plan.stage[i].lotsMult, 4));
     }
   return text;
  }

//+------------------------------------------------------------------+
//| Read a plan back out of ChFormatStagePlan's text                 |
//|                                                                  |
//|  An empty field means "no plan" and is not an error -- that is how |
//|  a watch saved before v1.7.0 reloads. Anything malformed refuses   |
//|  the whole plan rather than restoring a partial one: half a plan   |
//|  is a ticket the operator never wrote.                            |
//+------------------------------------------------------------------+
bool ChParseStagePlan(const string text, SCharonStagePlan &plan)
  {
   ZeroMemory(plan);
   string trimmed = text;
   StringTrimLeft(trimmed);
   StringTrimRight(trimmed);
   if(trimmed == "")
      return true;                        // no plan, not a failure

   //--- a trailing separator is refused explicitly rather than left to
   //--- StringSplit's edge behaviour: it decides the stage COUNT, and the
   //--- count is what bounds how many scale-ins the ladder may reach.
   if(StringGetCharacter(trimmed, StringLen(trimmed) - 1) == ';')
      return false;

   string stages[];
   int chunks = StringSplit(trimmed, ';', stages);
   if(chunks <= 0 || chunks > CHARON_MAX_TIERS - 1)
      return false;

   SCharonStagePlan built;
   ZeroMemory(built);
   for(int i = 0; i < chunks; i++)
     {
      //--- an empty chunk means a trailing or doubled ';'. Refused rather
      //--- than skipped: the count is what decides how many scale-ins the
      //--- ladder may reach, so a miscount is a different plan.
      string chunk = stages[i];
      StringTrimLeft(chunk);
      StringTrimRight(chunk);
      if(chunk == "")
         return false;

      string parts[];
      if(StringSplit(chunk, ',', parts) != 3)
         return false;

      SCharonStage stage;
      stage.kind     = (int)StringToInteger(parts[0]);
      stage.trigger  = StringToDouble(parts[1]);
      stage.lotsMult = StringToDouble(parts[2]);
      if(!ChStageIsValid(stage))
         return false;

      built.stage[i] = stage;
      built.count    = i + 1;
     }

   plan = built;
   return true;
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
   SCharonStagePlan  m_plan;           // v1.7.0: the ticket's own stage plan

public:
                     CCharonPyramid(void);
   void              Init(CCharonRisk *risk, const SCharonExecOpts &opts);

   //--- v1.7.0 per-ticket pyramid plan. Empty (count == 0) is the default
   //--- and means "use the chart-wide ladder in [5] PYRAMIDING", so every
   //--- ticket written before 1.7.0 keeps behaving exactly as it did.
   void              SetPlan(const SCharonStagePlan &plan) { ChCopyPlan(m_plan, plan); }
   void              ClearPlan(void)                       { ZeroMemory(m_plan); }
   bool              HasPlan(void)                  const  { return (m_plan.count > 0); }
   int               PlanCount(void)                const  { return m_plan.count; }
   bool              PlanSpec(const int tier, SCharonTierSpec &spec) const
                       { return ChPlanSpec(m_plan, tier, spec); }
   //--- the current plan as persistable text ("" when there is none), so a
   //--- caller can tell "restored the same plan" from "the plan changed".
   string            PlanText(void)                const  { return ChFormatStagePlan(m_plan); }
   //--- the ticket's stage when it has one, else the chart ladder. Used by
   //--- the engine to project the same multiplier Evaluate() will honour.
   bool              ResolveSpec(const int tier, SCharonTierSpec &spec) const
                       { return ChResolveTierSpec(m_plan, tier, spec); }

   //--- manual trigger state
   void              Arm(void);
   void              ClearArm(void);
   bool              IsArmed(void) const { return m_armed; }
   datetime          ArmedAt(void) const { return m_armedAt; }

   //--- gates, evaluated in a fixed order so logs explain refusals.
   //--- v1.7.0 adds 'price' so a ticket whose stage triggers on an absolute
   //--- price can be judged here instead of only on an R multiple.
   ENUM_CHARON_TIER_DECISION Evaluate(const int legs, const int nextTier, const int dirSign,
                                      const double progressRR,
                                      const double projectedRiskMoney,
                                      const double accountBase,
                                      const double price);
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
   ZeroMemory(m_plan);
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
                                                   const double accountBase,
                                                   const double price)
  {
   if(!InpPyramidEnable)
      return CH_TIER_DISABLED;
   if(m_risk == NULL)
      return CH_TIER_DISABLED;

   //--- legs counts the positions currently open (>=1 when a basket exists);
   //--- nextTier is the tier being requested, which is NOT necessarily
   //--- legs+1 when the human opened more than one primary position.
   //---
   //--- v1.7.0: the ceiling is resolved with the ticket's plan taken into
   //--- account. A three-stage ticket has to reach tier 4 even when
   //--- InpPyramidMaxLegs is 3, but a plan-less ticket keeps the old rules
   //--- byte for byte. ChPyramidAllowed is pure and carries that logic.
   if(!ChPyramidAllowed(true, legs, nextTier, m_plan.count, InpPyramidMaxLegs))
      return CH_TIER_MAXLEGS;

   //--- trigger is measured on the tier that is about to be added. The
   //--- ticket's own stage wins when it has one; otherwise the chart ladder.
   SCharonTierSpec spec;
   if(!ChResolveTierSpec(m_plan, nextTier, spec))
      return CH_TIER_NONE;

   //--- MANUAL trigger: the R trigger alone is not enough, the trader must
   //--- have armed the ladder. Placed before the trigger check so "not armed"
   //--- is reported as such instead of masquerading as "trigger not reached".
   if(InpPyramidTrigger == CHARON_PYR_MANUAL)
     {
      if(!ChArmIsLive(m_armed, m_armedAt, TimeCurrent(), InpPyramidArmTimeoutSec))
         return CH_TIER_NOTARMED;
     }

   //--- direction must be a single, unambiguous basket side. Checked before
   //--- the trigger because a PRICE stage needs to know which side "beyond"
   //--- means.
   if(dirSign == 0)
      return CH_TIER_NONE;

   if(!ChStageTriggerReached(spec, progressRR, price, (dirSign > 0)))
      return CH_TIER_NOTRIGGER;

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
   if(!ChResolveTierSpec(m_plan, tier, spec))
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

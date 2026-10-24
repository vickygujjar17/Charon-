//+------------------------------------------------------------------+
//|                                             CharonSelfTest.mq5   |
//|  Unit tests for Charon's pure arithmetic and pyramid tier model. |
//|                                                                  |
//|  CharonMath.mqh and the pure helpers in CharonPyramid.mqh make no |
//|  broker calls, so they can be asserted directly. The script never |
//|  trades and never reads market data.                             |
//|                                                                  |
//|  Run it on any chart. The verdict line is "SELFTEST RESULT: ..." |
//+------------------------------------------------------------------+
#property copyright "Charon"
#property version   "1.00"
#property script_show_inputs

#include "CharonMath.mqh"
#include "CharonPyramid.mqh"

//+------------------------------------------------------------------+
//| Harness                                                          |
//+------------------------------------------------------------------+
int    g_pass = 0;
int    g_fail = 0;

void Check(const bool ok, const string name, const string detail = "")
  {
   if(ok)
     {
      g_pass++;
      PrintFormat("PASS  %s", name);
      return;
     }
   g_fail++;
   Print("FAIL  ", name, "  ", detail);
  }

bool Near(const double a, const double b, const double tol = 1e-9)
  {
   return (MathAbs(a - b) <= tol);
  }

//+------------------------------------------------------------------+
//| Money and volume                                                 |
//+------------------------------------------------------------------+
void TestMoneyAndVolume(void)
  {
   //--- 100 points on a 5-digit FX symbol with tickValue 1.0 per lot
   double m = ChMoneyFromDistance(0.00100, 0.00001, 1.0, 1.0);
   Check(Near(m, 100.0), "money: 100 points x 1 lot = 100",
         StringFormat("got %.6f", m));

   m = ChMoneyFromDistance(0.00100, 0.00001, 1.0, 0.25);
   Check(Near(m, 25.0), "money: 100 points x 0.25 lot = 25",
         StringFormat("got %.6f", m));

   //--- degenerate inputs must return 0, never NaN
   Check(Near(ChMoneyFromDistance(0.0, 0.00001, 1.0, 1.0), 0.0), "money: zero distance = 0");
   Check(Near(ChMoneyFromDistance(0.001, 0.0, 1.0, 1.0), 0.0), "money: zero tickSize = 0");
   Check(Near(ChMoneyFromDistance(0.001, 0.00001, 1.0, 0.0), 0.0), "money: zero lot = 0");
   Check(Near(ChMoneyFromDistance(-0.001, 0.00001, 1.0, 1.0), 0.0), "money: negative distance = 0");

   Check(Near(ChNormalizeLot(0.1234, 0.01), 0.12, 1e-9), "lot: 0.1234 -> 0.12 (2dp step)",
         StringFormat("got %.4f", ChNormalizeLot(0.1234, 0.01)));
   Check(Near(ChNormalizeLot(0.126, 0.01), 0.13, 1e-9), "lot: 0.126 -> 0.13",
         StringFormat("got %.4f", ChNormalizeLot(0.126, 0.01)));
   Check(Near(ChNormalizeLot(1.0, 0.0), 1.0), "lot: zero step passes through");
  }

//+------------------------------------------------------------------+
//| Risk sizing: the volume must never exceed the budget             |
//+------------------------------------------------------------------+
void TestLotFromRisk(void)
  {
   //--- $100 budget, 100-point stop, $1 per point per lot -> 1.00 lot
   double lot = ChLotFromRisk(100.0, 0.00100, 0.00001, 1.0, 0.01, 0.01, 100.0);
   Check(Near(lot, 1.0, 1e-6), "lot: 100usd / 100pts = 1.00",
         StringFormat("got %.4f", lot));

   lot = ChLotFromRisk(50.0, 0.00100, 0.00001, 1.0, 0.01, 0.01, 100.0);
   Check(Near(lot, 0.50, 1e-6), "lot: 50usd / 100pts = 0.50",
         StringFormat("got %.4f", lot));

   //--- rounding must go DOWN so risk never overshoots
   lot = ChLotFromRisk(33.0, 0.00100, 0.00001, 1.0, 0.01, 0.01, 100.0);
   Check(lot <= 0.33 + 1e-9, "lot: 33usd rounds down to <= 0.33",
         StringFormat("got %.4f", lot));

   //--- below the minimum volume -> 0.0, the caller treats it as unsizable
   lot = ChLotFromRisk(0.5, 0.00100, 0.00001, 1.0, 0.01, 0.01, 100.0);
   Check(Near(lot, 0.0), "lot: budget below min lot = 0.0",
         StringFormat("got %.4f", lot));

   //--- capped by volMax
   lot = ChLotFromRisk(100000.0, 0.00100, 0.00001, 1.0, 0.01, 0.01, 5.0);
   Check(Near(lot, 5.0, 1e-6), "lot: capped at volMax",
         StringFormat("got %.4f", lot));

   //--- invalid input guards
   Check(Near(ChLotFromRisk(0.0, 0.001, 0.00001, 1.0, 0.01, 0.01, 100.0), 0.0),
         "lot: zero budget = 0.0");
   Check(Near(ChLotFromRisk(100.0, 0.0, 0.00001, 1.0, 0.01, 0.01, 100.0), 0.0),
         "lot: zero stop distance = 0.0");

   //--- the applied risk of the returned volume is inside the budget
   lot = ChLotFromRisk(25.0, 0.00250, 0.00001, 1.0, 0.01, 0.01, 100.0);
   double applied = ChMoneyFromDistance(0.00250, 0.00001, 1.0, lot);
   Check(applied <= 25.0 + 1e-9, "lot: applied risk never exceeds the budget",
         StringFormat("applied %.6f for lot %.4f", applied, lot));
  }

//+------------------------------------------------------------------+
//| Stop geometry                                                    |
//+------------------------------------------------------------------+
void TestStopGeometry(void)
  {
   double d = ChSlDistance(CHARON_SL_POINTS, 300.0, 0.0, 0.0, 0.0, 1.10000, 0.00001);
   Check(Near(d, 0.00300, 1e-9), "sl: 300 points = 0.00300",
         StringFormat("got %.6f", d));

   d = ChSlDistance(CHARON_SL_ATR, 0.0, 1.5, 0.00200, 0.0, 1.10000, 0.00001);
   Check(Near(d, 0.00300, 1e-9), "sl: ATR 0.00200 x 1.5 = 0.00300",
         StringFormat("got %.6f", d));

   d = ChSlDistance(CHARON_SL_PRICE_PCT, 0.0, 0.0, 0.0, 0.5, 1.10000, 0.00001);
   Check(Near(d, 0.00550, 1e-9), "sl: 0.5% of 1.1 = 0.00550",
         StringFormat("got %.6f", d));

   //--- ATR not ready must yield 0 so the caller can fall back
   d = ChSlDistance(CHARON_SL_ATR, 0.0, 1.5, 0.0, 0.0, 1.10000, 0.00001);
   Check(Near(d, 0.0), "sl: ATR unavailable = 0.0");

   Check(Near(ChStopFromDistance(1.10000, 0.00300, true), 1.09700, 1e-9),
         "sl: long stop below entry");
   Check(Near(ChStopFromDistance(1.10000, 0.00300, false), 1.10300, 1e-9),
         "sl: short stop above entry");
   Check(Near(ChStopFromDistance(1.10000, 0.0, true), 0.0),
         "sl: zero distance refused");
  }

//+------------------------------------------------------------------+
//| R mathematics                                                    |
//+------------------------------------------------------------------+
void TestRR(void)
  {
   Check(Near(ChPriceAtRR(1.10000, 0.00300, 2.0, true), 1.10600, 1e-9),
         "rr: long 2R target = 1.10600");
   Check(Near(ChPriceAtRR(1.10000, 0.00300, 2.0, false), 1.09400, 1e-9),
         "rr: short 2R target = 1.09400");
   Check(Near(ChPriceAtRR(1.10000, 0.00300, 1.0, true), 1.10300, 1e-9),
         "rr: long 1R = breakeven trigger price");

   Check(Near(ChProgressRR(1.10000, 0.00300, 1.10600, true), 2.0, 1e-9),
         "rr: price at 2R reports 2.0R");
   Check(Near(ChProgressRR(1.10000, 0.00300, 1.09700, true), -1.0, 1e-9),
         "rr: price at the stop reports -1.0R");
   Check(Near(ChProgressRR(1.10000, 0.00300, 1.10000, true), 0.0, 1e-9),
         "rr: entry reports 0.0R");
   Check(Near(ChProgressRR(1.10000, 0.00300, 1.09400, false), 2.0, 1e-9),
         "rr: short 2R reports 2.0R");
   Check(Near(ChProgressRR(1.10000, 0.0, 1.10600, true), 0.0),
         "rr: zero risk reports 0.0R (no division by zero)");
  }

//+------------------------------------------------------------------+
//| Improvement gate - the rule that makes trailing safe             |
//+------------------------------------------------------------------+
void TestImprovementGate(void)
  {
   const double point = 0.00001;
   double step = ChPriceFromPoints(5.0, point);       // 0.00005

   Check(ChStopImproves(1.09000, 1.09500, true, step), "gate: long higher stop accepted");
   Check(!ChStopImproves(1.09000, 1.08900, true, step), "gate: long lower stop rejected");
   Check(ChStopImproves(1.09000, 1.09010, true, step), "gate: long +10pts >= 5pt step");
   Check(!ChStopImproves(1.09000, 1.09002, true, step), "gate: long +2pts < 5pt step rejected");
   Check(!ChStopImproves(1.09000, 1.09000, true, step), "gate: long unchanged rejected");

   Check(ChStopImproves(1.11000, 1.10500, false, step), "gate: short lower stop accepted");
   Check(!ChStopImproves(1.11000, 1.11500, false, step), "gate: short higher stop rejected");

   //--- no existing stop -> any valid stop is an improvement
   Check(ChStopImproves(0.0, 1.09500, true, step), "gate: first stop always accepted");
   Check(!ChStopImproves(1.09000, 0.0, true, step), "gate: zero candidate rejected");

   //--- zero step still requires a strict improvement
   Check(ChStopImproves(1.09000, 1.09500, true, 0.0), "gate: zero step, real move accepted");
   Check(!ChStopImproves(1.09000, 1.09000, true, 0.0), "gate: zero step, no move rejected");
  }

//+------------------------------------------------------------------+
//| Extremum tracking and clamps                                     |
//+------------------------------------------------------------------+
void TestExtremeAndClamp(void)
  {
   Check(Near(ChExtreme(0.0, 1.1000, true), 1.1000), "hwm: seeds from first price");
   Check(Near(ChExtreme(1.1000, 1.1050, true), 1.1050), "hwm: long tracks new high");
   Check(Near(ChExtreme(1.1000, 1.0950, true), 1.1000), "hwm: long ignores pullback");
   Check(Near(ChExtreme(1.1000, 1.0950, false), 1.0950), "hwm: short tracks new low");
   Check(Near(ChExtreme(1.1000, 1.1050, false), 1.1000), "hwm: short ignores bounce");
   Check(Near(ChExtreme(1.1000, 0.0, true), 1.1000), "hwm: invalid price ignored");

   Check(Near(ChClampD(5.0, 0.0, 1.0), 1.0), "clamp: above range");
   Check(Near(ChClampD(-5.0, 0.0, 1.0), 0.0), "clamp: below range");
   Check(Near(ChClampD(0.5, 0.0, 1.0), 0.5), "clamp: inside range");
   Check(ChClampI(9, 1, 4) == 4, "clamp: int above range");
   Check(ChClampI(0, 1, 4) == 1, "clamp: int below range");
  }

//+------------------------------------------------------------------+
//| Pyramid tier model                                               |
//+------------------------------------------------------------------+
void TestPyramidModel(void)
  {
   Check(ChTierCountAllowed(3) == 2, "pyramid: 3 legs = 2 scale-ins");
   Check(ChTierCountAllowed(1) == 0, "pyramid: 1 leg = 0 scale-ins");
   Check(ChTierCountAllowed(99) == CHARON_MAX_TIERS - 1, "pyramid: max legs clamped");
   Check(ChTierCountAllowed(0) == 0, "pyramid: zero legs clamped");

   SCharonTierSpec spec;
   Check(ChTierSpec(2, spec), "pyramid: tier 2 spec resolves");
   Check(ChTierSpec(3, spec), "pyramid: tier 3 spec resolves");
   Check(ChTierSpec(4, spec), "pyramid: tier 4 spec resolves");
   Check(!ChTierSpec(1, spec), "pyramid: tier 1 is not a scale-in");
   Check(!ChTierSpec(5, spec), "pyramid: tier 5 rejected");

   //--- basket risk arithmetic
   Check(Near(ChBasketRiskWithLeg(100.0, 50.0, 0.0), 150.0), "pyramid: risk adds");
   Check(Near(ChBasketRiskWithLeg(100.0, 50.0, 30.0), 120.0), "pyramid: locked credit offsets");
   Check(Near(ChBasketRiskWithLeg(10.0, 5.0, 100.0), 0.0), "pyramid: risk floors at zero");

   //--- ceiling test: 1.5% of 10 000 = 150
   Check(ChPyramidRiskAccepts(100.0, 10000.0, 1.5), "pyramid: 100 under the 150 cap");
   Check(ChPyramidRiskAccepts(150.0, 10000.0, 1.5), "pyramid: exactly at the cap");
   Check(!ChPyramidRiskAccepts(200.0, 10000.0, 1.5), "pyramid: 200 over the 150 cap");
   Check(!ChPyramidRiskAccepts(100.0, 0.0, 1.5), "pyramid: no account base rejects");
   Check(!ChPyramidRiskAccepts(-1.0, 10000.0, 1.5), "pyramid: negative risk rejects");

   //--- 1R must round-trip through the stop and the target
   double risk = 0.00300;
   double stopPrice = ChStopFromDistance(1.10000, risk, true);
   Check(Near(MathAbs(1.10000 - stopPrice), risk, 1e-9),
         "rr: stop distance recovers 1R exactly");
   double tp = ChPriceAtRR(1.10000, risk, InpTpRR, true);
   Check(Near(ChProgressRR(1.10000, risk, tp, true), InpTpRR, 1e-9),
         "rr: RR target reports exactly InpTpRR progress");
  }

//+------------------------------------------------------------------+
//| Script entry point                                               |
//+------------------------------------------------------------------+
void OnStart(void)
  {
   PrintFormat("==================================================");
   PrintFormat(" %s %s - self test", CHARON_NAME, CHARON_VERSION);
   PrintFormat("==================================================");

   TestMoneyAndVolume();
   TestLotFromRisk();
   TestStopGeometry();
   TestRR();
   TestImprovementGate();
   TestExtremeAndClamp();
   TestPyramidModel();

   PrintFormat("==================================================");
   PrintFormat("passed: %d   failed: %d", g_pass, g_fail);
   if(g_fail > 0)
      PrintFormat("SELFTEST RESULT: FAIL (%d failures of %d)", g_fail, g_pass + g_fail);
   else
      PrintFormat("SELFTEST RESULT: PASS (%d/%d)", g_pass, g_pass);
   PrintFormat("==================================================");
  }

//+------------------------------------------------------------------+

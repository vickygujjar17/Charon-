//+------------------------------------------------------------------+
//|                                                   CharonMath.mqh |
//|  Pure arithmetic for Charon: no terminal calls, no inputs, no     |
//|  side effects. Everything here is unit-testable by               |
//|  Scripts/CharonSelfTest.mq5 and is the single place where the    |
//|  risk / RR mathematics is defined.                               |
//+------------------------------------------------------------------+
#property copyright "Charon"
#property version   "1.07"

#ifndef __CHARON_MATH__
#define __CHARON_MATH__

#include "CharonDefines.mqh"

//--- tolerance used for step/volume comparisons
#define CHARON_EPS 1e-8

//+------------------------------------------------------------------+
//| Direction helper: true when a POSITION_TYPE / ORDER_TYPE is long |
//+------------------------------------------------------------------+
bool ChIsLong(const long positionType)
  {
   return (positionType == (long)POSITION_TYPE_BUY);
  }

//+------------------------------------------------------------------+
//| Price distance expressed in points                               |
//+------------------------------------------------------------------+
double ChPointsFromPrice(const double priceDistance, const double point)
  {
   if(point <= 0.0 || !MathIsValidNumber(priceDistance))
      return 0.0;
   return priceDistance / point;
  }

//+------------------------------------------------------------------+
//| Points expressed as a price distance                             |
//+------------------------------------------------------------------+
double ChPriceFromPoints(const double points, const double point)
  {
   if(point <= 0.0 || !MathIsValidNumber(points))
      return 0.0;
   return points * point;
  }

//+------------------------------------------------------------------+
//| Money risked by a price distance for a given volume              |
//| (tickSize/tickValue come from SYMBOL_TRADE_TICK_SIZE/VALUE)      |
//+------------------------------------------------------------------+
double ChMoneyFromDistance(const double priceDistance, const double tickSize,
                           const double tickValue, const double lot)
  {
   if(tickSize <= 0.0 || tickValue <= 0.0 || lot <= 0.0)
      return 0.0;
   if(!MathIsValidNumber(priceDistance) || priceDistance <= 0.0)
      return 0.0;
   return (priceDistance / tickSize) * tickValue * lot;
  }

//+------------------------------------------------------------------+
//| Round a volume to the broker step (nearest, never above the cap) |
//+------------------------------------------------------------------+
double ChNormalizeLot(const double lot, const double volStep)
  {
   if(volStep <= 0.0)
      return lot;
   double steps = MathRound(lot / volStep);
   return NormalizeDouble(steps * volStep, 8);
  }

//+------------------------------------------------------------------+
//| Position size for an exact money risk over a stop distance        |
//|                                                                  |
//|  * rounds the volume DOWN to the broker step, so the applied      |
//|    risk can never exceed the requested budget                    |
//|  * returns 0.0 when the result is below the minimum volume, i.e. |
//|    "this tier cannot be sized without breaking the budget"       |
//+------------------------------------------------------------------+
double ChLotFromRisk(const double riskMoney, const double slPriceDistance,
                     const double tickSize, const double tickValue,
                     const double volStep, const double volMin, const double volMax)
  {
   if(!MathIsValidNumber(riskMoney) || riskMoney <= 0.0)
      return 0.0;
   if(!MathIsValidNumber(slPriceDistance) || slPriceDistance <= 0.0)
      return 0.0;
   if(tickSize <= 0.0 || tickValue <= 0.0 || volStep <= 0.0 || volMin <= 0.0)
      return 0.0;

   double moneyPerLot = (slPriceDistance / tickSize) * tickValue;
   if(moneyPerLot <= 0.0)
      return 0.0;

   double lot = MathFloor((riskMoney / moneyPerLot) / volStep + CHARON_EPS) * volStep;
   if(volMax > 0.0 && lot > volMax)
      lot = MathFloor(volMax / volStep + CHARON_EPS) * volStep;
   if(lot < volMin)
      return 0.0;                       // cannot size without overshooting the budget

   double applied = ChMoneyFromDistance(slPriceDistance, tickSize, tickValue, lot);
   if(applied > riskMoney + CHARON_EPS && lot > volMin)
      lot -= volStep;                   // belt and braces: never exceed the budget

   if(lot < volMin)
      return 0.0;
   if(volMax > 0.0 && lot > volMax)
      return 0.0;

   return NormalizeDouble(lot, 8);
  }

//+------------------------------------------------------------------+
//| Strict stop distance in price terms for one of the SL units      |
//+------------------------------------------------------------------+
double ChSlDistance(const ENUM_CHARON_SL_UNIT unit, const double points,
                    const double atrMult, const double atrValue,
                    const double pricePct, const double entryPrice, const double point)
  {
   double dist = 0.0;
   switch(unit)
     {
      case CHARON_SL_POINTS:
         dist = ChPriceFromPoints(points, point);
         break;
      case CHARON_SL_ATR:
         if(atrValue > 0.0 && atrMult > 0.0)
            dist = atrValue * atrMult;
         break;
      case CHARON_SL_PRICE_PCT:
         if(entryPrice > 0.0 && pricePct > 0.0)
            dist = entryPrice * pricePct / 100.0;
         break;
      default:
         dist = 0.0;
         break;
     }
   if(!MathIsValidNumber(dist) || dist <= 0.0)
      return 0.0;
   return dist;
  }

//+------------------------------------------------------------------+
//| Stop price from an entry price and a signed distance             |
//+------------------------------------------------------------------+
double ChStopFromDistance(const double entryPrice, const double distance, const bool isLong)
  {
   if(!MathIsValidNumber(entryPrice) || !MathIsValidNumber(distance) || distance <= 0.0)
      return 0.0;
   return (isLong ? (entryPrice - distance) : (entryPrice + distance));
  }

//+------------------------------------------------------------------+
//| Price reached at a given R multiple, measured from the fill      |
//+------------------------------------------------------------------+
double ChPriceAtRR(const double entryPrice, const double riskPrice,
                   const double rrMultiple, const bool isLong)
  {
   if(!MathIsValidNumber(entryPrice) || !MathIsValidNumber(riskPrice))
      return 0.0;
   if(riskPrice <= 0.0 || !MathIsValidNumber(rrMultiple))
      return 0.0;
   return (isLong ? (entryPrice + riskPrice * rrMultiple)
                  : (entryPrice - riskPrice * rrMultiple));
  }

//+------------------------------------------------------------------+
//| R multiple currently reached by a market price (may be negative) |
//+------------------------------------------------------------------+
double ChProgressRR(const double entryPrice, const double riskPrice,
                    const double price, const bool isLong)
  {
   if(riskPrice <= 0.0 || !MathIsValidNumber(riskPrice))
      return 0.0;
   if(!MathIsValidNumber(entryPrice) || !MathIsValidNumber(price))
      return 0.0;
   double travel = (isLong ? (price - entryPrice) : (entryPrice - price));
   return travel / riskPrice;
  }

//+------------------------------------------------------------------+
//| Has price touched or crossed level?                              |
//|                                                                  |
//|  "Touched or crossed", never equality. A level the market gaps    |
//|  over must still register, and an exact-tick comparison would     |
//|  silently never fire -- the worst failure for a condition whose   |
//|  whole job is to wait. A non-positive level means "no level",      |
//|  which every caller reads as "not yet", never "already there".    |
//+------------------------------------------------------------------+
bool ChPriceAtOrBeyond(const double price, const double level, const bool isLong)
  {
   if(!MathIsValidNumber(level) || level <= 0.0)
      return false;
   if(!MathIsValidNumber(price) || price <= 0.0)
      return false;
   return (isLong ? (price >= level) : (price <= level));
  }

//+------------------------------------------------------------------+
//| True when candidate is a better (more protective) stop           |
//| minStepPrice is the minimum improvement that justifies a modify  |
//+------------------------------------------------------------------+
bool ChStopImproves(const double currentStop, const double candidate,
                    const bool isLong, const double minStepPrice)
  {
   if(!MathIsValidNumber(candidate) || candidate <= 0.0)
      return false;
   if(currentStop <= 0.0 || !MathIsValidNumber(currentStop))
      return true;                     // no stop yet: any valid stop is an improvement

   //--- the move must be strictly protective, in the right direction
   double delta = (isLong ? (candidate - currentStop) : (currentStop - candidate));
   if(delta <= CHARON_EPS)
      return false;                    // unchanged or looser: never send a modify

   //--- and it must clear the configured step, otherwise the broker would be
   //--- hit on every tick for a negligible gain
   double step = (minStepPrice > 0.0 ? minStepPrice : 0.0);
   return (delta >= step - CHARON_EPS);
  }

//+------------------------------------------------------------------+
//| Extremum tracking: keeps the high for longs, the low for shorts  |
//+------------------------------------------------------------------+
double ChExtreme(const double currentExtreme, const double price, const bool isLong)
  {
   if(!MathIsValidNumber(price) || price <= 0.0)
      return currentExtreme;
   if(currentExtreme <= 0.0 || !MathIsValidNumber(currentExtreme))
      return price;
   if(isLong)
      return (price > currentExtreme ? price : currentExtreme);
   return (price < currentExtreme ? price : currentExtreme);
  }

//+------------------------------------------------------------------+
//| Clamp a value into [lo, hi]; used for tier/safety guards         |
//+------------------------------------------------------------------+
double ChClampD(const double value, const double lo, const double hi)
  {
   if(!MathIsValidNumber(value))
      return lo;
   if(value < lo)
      return lo;
   if(value > hi)
      return hi;
   return value;
  }

//+------------------------------------------------------------------+
//| Integer clamp                                                    |
//+------------------------------------------------------------------+
int ChClampI(const int value, const int lo, const int hi)
  {
   if(value < lo)
      return lo;
   if(value > hi)
      return hi;
   return value;
  }

//+------------------------------------------------------------------+
//| Price ladder at three R multiples (pure)                         |
//|                                                                  |
//|  Fills out[0..2] with the price reached at levels a/b/c, skipping |
//|  any level that is <= 0 (disabled) or unusable. Returns how many  |
//|  levels were written, so the caller can format exactly that many. |
//|  Levels are emitted in the caller's order, not sorted: the input  |
//|  order is what the trader configured.                            |
//+------------------------------------------------------------------+
int ChRrLadderPrices(const double entryPrice, const double riskPrice, const bool isLong,
                     const double levelA, const double levelB, const double levelC,
                     double &out[])
  {
   //--- MQL5 only allows dynamic arrays as reference parameters; the callers
   //--- pass a fixed 3-slot array, so every write is bounded by its real size
   int slots = ArraySize(out);
   for(int k = 0; k < slots; k++)
      out[k] = 0.0;

   if(!MathIsValidNumber(entryPrice) || entryPrice <= 0.0)
      return 0;
   if(!MathIsValidNumber(riskPrice) || riskPrice <= 0.0)
      return 0;

   double levels[3];
   levels[0] = levelA;
   levels[1] = levelB;
   levels[2] = levelC;

   int count = 0;
   for(int i = 0; i < 3; i++)
     {
      //--- a level of 0 (or nonsense) simply disables that rung
      if(!MathIsValidNumber(levels[i]) || levels[i] <= 0.0)
         continue;
      double p = ChPriceAtRR(entryPrice, riskPrice, levels[i], isLong);
      if(!MathIsValidNumber(p) || p <= 0.0)
         continue;
      if(count < slots)
         out[count] = p;
      count++;
     }
   return (count > slots ? slots : count);
  }

//+------------------------------------------------------------------+
//| Is a manual pyramid arm still live? (pure)                       |
//|                                                                  |
//|  armed=false                       -> never live                 |
//|  timeoutSec <= 0                   -> live until cleared          |
//|  armedAt in the future / clock skew -> treated as just armed      |
//+------------------------------------------------------------------+
bool ChArmIsLive(const bool armed, const datetime armedAt,
                 const datetime now, const int timeoutSec)
  {
   if(!armed)
      return false;
   if(timeoutSec <= 0)
      return true;
   if(armedAt <= 0)
      return true;                     // armed without a stamp: honour it
   if(now <= armedAt)
      return true;                     // clock skew must not silently disarm
   long elapsed = (long)(now - armedAt);
   return (elapsed < (long)timeoutSec);
  }

//+------------------------------------------------------------------+
#endif // __CHARON_MATH__
//+------------------------------------------------------------------+


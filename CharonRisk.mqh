//+------------------------------------------------------------------+
//|                                                   CharonRisk.mqh |
//|  Symbol specification, ATR access and position sizing.           |
//|  Every monetary decision in Charon flows through this module.    |
//+------------------------------------------------------------------+
#property copyright "Charon"
#property version   "1.03"

#ifndef __CHARON_RISK__
#define __CHARON_RISK__

#include <Trade\SymbolInfo.mqh>
#include "CharonDefines.mqh"
#include "CharonMath.mqh"

//+------------------------------------------------------------------+
//| Broker-side trading constraints for one symbol                   |
//+------------------------------------------------------------------+
struct SCharonSymSpec
  {
   string            symbol;
   double            point;
   double            tickSize;
   double            tickValue;
   double            volMin;
   double            volStep;
   double            volMax;
   double            stopsLevel;      // broker minimum stop distance in price
   double            freezeLevel;     // freeze distance in price
   int               digits;
   int               spreadPoints;
   bool              valid;
  };

//+------------------------------------------------------------------+
//| CCharonRisk - spec, ATR and money sizing                         |
//+------------------------------------------------------------------+
class CCharonRisk
  {
private:
   CSymbolInfo       m_sym;
   SCharonSymSpec    m_spec;
   int               m_atrHandle;
   string            m_atrSymbol;
   ENUM_TIMEFRAMES   m_atrTf;
   int               m_atrPeriod;

   bool              LoadSpec(void);

public:
                     CCharonRisk(void);
                    ~CCharonRisk(void);

   bool              Init(const string symbol, const ENUM_TIMEFRAMES tf, const int atrPeriod);
   void              Deinit(void);
   bool              Refresh(void);

   //--- symbol accessors
   double            Point(void)       const { return m_spec.point; }
   int               Digits(void)      const { return m_spec.digits; }
   double            TickSize(void)    const { return m_spec.tickSize; }
   double            TickValue(void)   const { return m_spec.tickValue; }
   double            VolMin(void)      const { return m_spec.volMin; }
   double            VolMax(void)      const { return m_spec.volMax; }
   double            VolStep(void)     const { return m_spec.volStep; }
   double            StopsLevel(void)  const { return m_spec.stopsLevel; }
   double            FreezeLevel(void) const { return m_spec.freezeLevel; }
   double            SpreadPoints(void) const { return (double)m_spec.spreadPoints; }
   double            Bid(void)         { return m_sym.Bid(); }
   double            Ask(void)         { return m_sym.Ask(); }
   bool              IsValid(void)     const { return m_spec.valid; }

   //--- prices
   double            NormalizePrice(const double price) const;
   double            MinStopDistance(void) const;
   double            StopDistanceWithBuffer(void) const;

   //--- ATR
   double            Atr(void);
   bool              AtrReady(void);

   //--- money
   double            AccountBase(void) const;
   double            RiskBudgetMoney(void) const;
   double            MoneyAtDistance(const double distance, const double lot) const;
   double            MaxAllowedRiskMoney(void) const;
   double            LotForStop(const double distance, bool &sized,
                                const bool isScaleIn = false, const double multiplier = 1.0);
   double            LotForBudget(const double distance, const double budget,
                                  bool &sized);
  };

//+------------------------------------------------------------------+
//| Implementation                                                   |
//+------------------------------------------------------------------+

//+------------------------------------------------------------------+
//| Constructor                                                      |
//+------------------------------------------------------------------+
CCharonRisk::CCharonRisk(void)
  {
   m_atrHandle = INVALID_HANDLE;
   m_atrSymbol = "";
   m_atrTf     = PERIOD_CURRENT;
   m_atrPeriod = 14;

   //--- explicit init: SCharonSymSpec holds a string, so ZeroMemory is unsafe
   m_spec.symbol       = "";
   m_spec.point        = 0.0;
   m_spec.tickSize     = 0.0;
   m_spec.tickValue    = 0.0;
   m_spec.volMin       = 0.0;
   m_spec.volStep      = 0.0;
   m_spec.volMax       = 0.0;
   m_spec.stopsLevel   = 0.0;
   m_spec.freezeLevel  = 0.0;
   m_spec.digits       = 0;
   m_spec.spreadPoints = 0;
   m_spec.valid        = false;
  }

//+------------------------------------------------------------------+
//| Destructor                                                       |
//+------------------------------------------------------------------+
CCharonRisk::~CCharonRisk(void)
  {
   Deinit();
  }

//+------------------------------------------------------------------+
//| Release the ATR handle                                           |
//+------------------------------------------------------------------+
void CCharonRisk::Deinit(void)
  {
   if(m_atrHandle != INVALID_HANDLE)
     {
      IndicatorRelease(m_atrHandle);
      m_atrHandle = INVALID_HANDLE;
     }
  }

//+------------------------------------------------------------------+
//| Read the broker constraints once                                 |
//+------------------------------------------------------------------+
bool CCharonRisk::LoadSpec(void)
  {
   m_spec.symbol       = m_sym.Name();
   m_spec.point        = m_sym.Point();
   m_spec.digits       = m_sym.Digits();
   m_spec.tickSize     = m_sym.TickSize();
   m_spec.tickValue    = m_sym.TickValue();
   m_spec.volMin       = m_sym.LotsMin();
   m_spec.volMax       = m_sym.LotsMax();
   m_spec.volStep      = m_sym.LotsStep();
   m_spec.stopsLevel   = (double)m_sym.StopsLevel() * m_spec.point;
   m_spec.freezeLevel  = (double)m_sym.FreezeLevel() * m_spec.point;
   double spread       = m_sym.Ask() - m_sym.Bid();
   m_spec.spreadPoints = (int)MathRound(ChPointsFromPrice(spread, m_spec.point));

   m_spec.valid = (m_spec.point > 0.0 && m_spec.tickSize > 0.0 &&
                   m_spec.tickValue > 0.0 && m_spec.volMin > 0.0 &&
                   m_spec.volStep > 0.0);
   return m_spec.valid;
  }

//+------------------------------------------------------------------+
//| Initialise the module                                            |
//+------------------------------------------------------------------+
bool CCharonRisk::Init(const string symbol, const ENUM_TIMEFRAMES tf, const int atrPeriod)
  {
   if(!m_sym.Name(symbol))
     {
      PrintFormat("%s: cannot select symbol %s (error %d)", CHARON_NAME, symbol, GetLastError());
      return false;
     }
   if(!m_sym.RefreshRates())
      return false;
   if(!LoadSpec())
     {
      PrintFormat("%s: invalid symbol specification for %s", CHARON_NAME, symbol);
      return false;
     }

   m_atrSymbol = symbol;
   m_atrTf     = tf;
   m_atrPeriod = (atrPeriod < 2 ? 2 : atrPeriod);
   m_atrHandle = iATR(symbol, tf, m_atrPeriod);
   if(m_atrHandle == INVALID_HANDLE)
      PrintFormat("%s: iATR failed (error %d) - ATR stops/trails unavailable",
                  CHARON_NAME, GetLastError());
   return true;
  }

//+------------------------------------------------------------------+
//| Refresh rates and the fast-changing spec fields                  |
//+------------------------------------------------------------------+
bool CCharonRisk::Refresh(void)
  {
   if(!m_sym.RefreshRates())
      return false;
   double spread = m_sym.Ask() - m_sym.Bid();
   m_spec.spreadPoints = (int)MathRound(ChPointsFromPrice(spread, m_spec.point));
   return true;
  }

//+------------------------------------------------------------------+
//| Round a price to the symbol digits                               |
//+------------------------------------------------------------------+
double CCharonRisk::NormalizePrice(const double price) const
  {
   if(!MathIsValidNumber(price) || price <= 0.0)
      return 0.0;
   return NormalizeDouble(price, m_spec.digits);
  }

//+------------------------------------------------------------------+
//| Broker minimum stop distance, in price                           |
//+------------------------------------------------------------------+
double CCharonRisk::MinStopDistance(void) const
  {
   return m_spec.stopsLevel;
  }

//+------------------------------------------------------------------+
//| Broker minimum stop distance plus the configured safety buffer   |
//+------------------------------------------------------------------+
double CCharonRisk::StopDistanceWithBuffer(void) const
  {
   return m_spec.stopsLevel + ChPriceFromPoints(InpStrictStopBufferPts, m_spec.point);
  }

//+------------------------------------------------------------------+
//| Current ATR value, 0.0 when the indicator is not ready           |
//+------------------------------------------------------------------+
double CCharonRisk::Atr(void)
  {
   if(m_atrHandle == INVALID_HANDLE)
      return 0.0;
   double buf[];
   if(CopyBuffer(m_atrHandle, 0, 0, 1, buf) != 1)
      return 0.0;
   if(!MathIsValidNumber(buf[0]) || buf[0] <= 0.0)
      return 0.0;
   return buf[0];
  }

//+------------------------------------------------------------------+
//| True when ATR produced a usable value                            |
//+------------------------------------------------------------------+
bool CCharonRisk::AtrReady(void)
  {
   return (Atr() > 0.0);
  }

//+------------------------------------------------------------------+
//| Account base used for sizing (equity or balance)                 |
//+------------------------------------------------------------------+
double CCharonRisk::AccountBase(void) const
  {
   double base = (InpUseEquity ? AccountInfoDouble(ACCOUNT_EQUITY)
                               : AccountInfoDouble(ACCOUNT_BALANCE));
   if(!MathIsValidNumber(base) || base <= 0.0)
      base = AccountInfoDouble(ACCOUNT_BALANCE);
   return (MathIsValidNumber(base) && base > 0.0 ? base : 0.0);
  }

//+------------------------------------------------------------------+
//| Money the next position is allowed to risk                       |
//+------------------------------------------------------------------+
double CCharonRisk::RiskBudgetMoney(void) const
  {
   double base = AccountBase();
   if(base <= 0.0)
      return 0.0;

   double budget = 0.0;
   switch(InpLotMode)
     {
      case CHARON_LOT_RISK_PCT:
         budget = base * InpRiskPct / 100.0;
         break;
      case CHARON_LOT_FIXED_MONEY:
         budget = InpRiskMoney;
         break;
      case CHARON_LOT_FIXED_LOTS:
         budget = ChMoneyFromDistance(InpSlPoints * m_spec.point,
                                      m_spec.tickSize, m_spec.tickValue, InpFixedLots);
         break;
      default:
         budget = 0.0;
         break;
     }

   if(!MathIsValidNumber(budget) || budget <= 0.0)
      return 0.0;
   return budget;
  }

//+------------------------------------------------------------------+
//| Account-currency risk of a distance/volume pair, commissions in  |
//+------------------------------------------------------------------+
double CCharonRisk::MoneyAtDistance(const double distance, const double lot) const
  {
   double money = ChMoneyFromDistance(distance, m_spec.tickSize, m_spec.tickValue, lot);
   if(money > 0.0 && InpCommissionPerLot > 0.0)
      money += InpCommissionPerLot * lot;
   return money;
  }

//+------------------------------------------------------------------+
//| Hard ceiling on the risk of a single position                    |
//+------------------------------------------------------------------+
double CCharonRisk::MaxAllowedRiskMoney(void) const
  {
   double base = AccountBase();
   if(base <= 0.0)
      return 0.0;
   return base * InpMaxRiskPct / 100.0;
  }

//+------------------------------------------------------------------+
//| Volume that risks the configured budget over the stop distance   |
//|                                                                  |
//|  isScaleIn/multiplier scale the budget for pyramid tiers so that  |
//|  tier N can never risk the full primary budget.                   |
//|  sized=false means "no volume satisfies the budget".              |
//+------------------------------------------------------------------+
double CCharonRisk::LotForStop(const double distance, bool &sized,
                               const bool isScaleIn, const double multiplier)
  {
   sized = false;
   if(!MathIsValidNumber(distance) || distance <= 0.0)
      return 0.0;

   //--- FIXED_LOTS bypasses money sizing entirely
   if(InpLotMode == CHARON_LOT_FIXED_LOTS)
     {
      double lots = InpFixedLots * (isScaleIn ? multiplier : 1.0);
      lots = ChNormalizeLot(lots, m_spec.volStep);
      if(lots > m_spec.volMax)
         lots = ChNormalizeLot(m_spec.volMax, m_spec.volStep);
      if(lots < m_spec.volMin)
         return 0.0;
      sized = true;
      return lots;
     }

   double budget = RiskBudgetMoney();
   if(budget <= 0.0)
      return 0.0;

   if(isScaleIn)
     {
      if(multiplier <= 0.0)
         return 0.0;
      budget *= multiplier;
     }

   //--- commission is charged per lot, so solve for the volume that
   //--- keeps (distance risk + commission) inside the budget
   double perLotRisk = 0.0;
   if(m_spec.tickSize > 0.0 && m_spec.tickValue > 0.0)
      perLotRisk = (distance / m_spec.tickSize) * m_spec.tickValue;
   if(perLotRisk <= 0.0)
      return 0.0;
   perLotRisk += (InpCommissionPerLot > 0.0 ? InpCommissionPerLot : 0.0);

   double lot = ChNormalizeLot(MathFloor((budget / perLotRisk) / m_spec.volStep + CHARON_EPS) * m_spec.volStep,
                               m_spec.volStep);
   if(lot > m_spec.volMax)
      lot = ChNormalizeLot(m_spec.volMax, m_spec.volStep);

   //--- verify the exact applied risk, not just the rounded intent
   while(lot >= m_spec.volMin &&
         MoneyAtDistance(distance, lot) > budget + CHARON_EPS)
      lot = ChNormalizeLot(lot - m_spec.volStep, m_spec.volStep);

   if(lot < m_spec.volMin)
     {
      if(!InpAllowMinLot)
         return 0.0;
      lot = m_spec.volMin;
      //--- the min lot must still respect the hard ceiling
      if(MoneyAtDistance(distance, lot) > MaxAllowedRiskMoney() + CHARON_EPS)
         return 0.0;
     }

   sized = true;
   return lot;
  }

//+------------------------------------------------------------------+
//| Volume that risks an EXPLICIT money budget over a stop distance  |
//|                                                                  |
//|  Used by attended execution, where the ticket states its own     |
//|  risk percentage instead of inheriting InpRiskPct. Five rules    |
//|  are kept identical to LotForStop() so the two paths cannot      |
//|  disagree:                                                       |
//|    * FIXED_LOTS bypasses money sizing entirely;                  |
//|    * commission is charged per lot and solved for, not ignored;  |
//|    * the result is rounded to the broker's volume step;          |
//|    * the applied risk is verified against the budget, not just   |
//|      the rounded intent;                                         |
//|    * the min-lot fallback still respects MaxAllowedRiskMoney().  |
//|  size (not budget) is the binding constraint: a ticket can ask   |
//|  for LESS than InpMaxRiskPct but never for more, because the     |
//|  caller clamps the budget before it gets here.                   |
//+------------------------------------------------------------------+
double CCharonRisk::LotForBudget(const double distance, const double budget,
                                 bool &sized)
  {
   sized = false;
   if(!MathIsValidNumber(distance) || distance <= 0.0)
      return 0.0;
   if(!MathIsValidNumber(budget) || budget <= 0.0)
      return 0.0;

   //--- FIXED_LOTS is an operator override of money sizing: honour it
   if(InpLotMode == CHARON_LOT_FIXED_LOTS)
     {
      double lots = ChNormalizeLot(InpFixedLots, m_spec.volStep);
      if(lots > m_spec.volMax)
         lots = ChNormalizeLot(m_spec.volMax, m_spec.volStep);
      if(lots < m_spec.volMin)
         return 0.0;
      sized = true;
      return lots;
     }

   double perLotRisk = 0.0;
   if(m_spec.tickSize > 0.0 && m_spec.tickValue > 0.0)
      perLotRisk = (distance / m_spec.tickSize) * m_spec.tickValue;
   if(perLotRisk <= 0.0)
      return 0.0;
   perLotRisk += (InpCommissionPerLot > 0.0 ? InpCommissionPerLot : 0.0);

   double lot = ChNormalizeLot(MathFloor((budget / perLotRisk) / m_spec.volStep + CHARON_EPS) * m_spec.volStep,
                               m_spec.volStep);
   if(lot > m_spec.volMax)
      lot = ChNormalizeLot(m_spec.volMax, m_spec.volStep);

   while(lot >= m_spec.volMin &&
         MoneyAtDistance(distance, lot) > budget + CHARON_EPS)
      lot = ChNormalizeLot(lot - m_spec.volStep, m_spec.volStep);

   if(lot < m_spec.volMin)
     {
      if(!InpAllowMinLot)
         return 0.0;
      lot = m_spec.volMin;
      //--- the min lot must still respect the hard ceiling, which is
      //--- independent of what the ticket asked for
      if(MoneyAtDistance(distance, lot) > MaxAllowedRiskMoney() + CHARON_EPS)
         return 0.0;
     }

   sized = true;
   return lot;
  }

//+------------------------------------------------------------------+
#endif // __CHARON_RISK__
//+------------------------------------------------------------------+

//+------------------------------------------------------------------+

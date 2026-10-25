//+------------------------------------------------------------------+
//|                                                  CharonTrade.mqh |
//|  Raw broker primitives. This is the ONLY module that calls        |
//|  OrderSend(): every call is initiated by a human decision         |
//|  (adoption, pyramid escalation confirmed by the model) or by a    |
//|  protective stop update. There is no signal logic here.           |
//+------------------------------------------------------------------+
#property copyright "Charon"
#property version   "1.03"

#ifndef __CHARON_TRADE__
#define __CHARON_TRADE__

#include "CharonDefines.mqh"
#include "CharonMath.mqh"

//--- execution settings resolved once per tick
struct SCharonExecOpts
  {
   string            symbol;
   int               slippage;
   int               retries;
   int               delayMs;
  };

//+------------------------------------------------------------------+
//| Filling mode the symbol actually supports                        |
//+------------------------------------------------------------------+
ENUM_ORDER_TYPE_FILLING ChPickFilling(const string symbol)
  {
   long fill = SymbolInfoInteger(symbol, SYMBOL_FILLING_MODE);
   if((fill & SYMBOL_FILLING_FOK) != 0)
      return ORDER_FILLING_FOK;
   if((fill & SYMBOL_FILLING_IOC) != 0)
      return ORDER_FILLING_IOC;
   return ORDER_FILLING_RETURN;
  }

//+------------------------------------------------------------------+
//| True for retcodes that mean "the broker accepted the request"    |
//+------------------------------------------------------------------+
bool ChRetcodeIsOk(const uint retcode)
  {
   return (retcode == TRADE_RETCODE_DONE ||
           retcode == TRADE_RETCODE_PLACED ||
           retcode == TRADE_RETCODE_DONE_PARTIAL);
  }

//+------------------------------------------------------------------+
//| Human-readable reason for a retcode                              |
//+------------------------------------------------------------------+
string ChRetcodeText(const uint retcode)
  {
   switch(retcode)
     {
      case TRADE_RETCODE_DONE:              return "done";
      case TRADE_RETCODE_PLACED:            return "placed";
      case TRADE_RETCODE_DONE_PARTIAL:      return "partial fill";
      case TRADE_RETCODE_REQUOTE:           return "requote";
      case TRADE_RETCODE_REJECT:            return "rejected";
      case TRADE_RETCODE_INVALID:           return "invalid request";
      case TRADE_RETCODE_INVALID_VOLUME:    return "invalid volume";
      case TRADE_RETCODE_INVALID_PRICE:     return "invalid price";
      case TRADE_RETCODE_INVALID_STOPS:     return "invalid stops";
      case TRADE_RETCODE_TRADE_DISABLED:    return "trading disabled";
      case TRADE_RETCODE_MARKET_CLOSED:     return "market closed";
      case TRADE_RETCODE_NO_MONEY:          return "not enough money";
      case TRADE_RETCODE_PRICE_CHANGED:     return "price changed";
      case TRADE_RETCODE_PRICE_OFF:         return "no quotes";
      case TRADE_RETCODE_INVALID_EXPIRATION:return "invalid expiration";
      case TRADE_RETCODE_POSITION_CLOSED:   return "position already closed";
      case TRADE_RETCODE_INVALID_FILL:      return "unsupported filling mode";
      case TRADE_RETCODE_LIMIT_VOLUME:      return "volume limit reached";
      case TRADE_RETCODE_ERROR:             return "server error";
      case TRADE_RETCODE_TIMEOUT:           return "timeout";
      case TRADE_RETCODE_CONNECTION:        return "no connection";
      case TRADE_RETCODE_TOO_MANY_REQUESTS: return "too many requests";
      default:                              return ("retcode " + IntegerToString((int)retcode));
     }
  }

//+------------------------------------------------------------------+
//| Open a market position with a strict stop already attached       |
//+------------------------------------------------------------------+
bool ChExecOpenMarket(const SCharonExecOpts &opts, const ENUM_POSITION_TYPE posType,
                      const double lot, const double sl, const double tp,
                      const long magic, const string comment,
                      ulong &ticket, double &fillPrice, string &err)
  {
   ticket    = 0;
   fillPrice = 0.0;
   err       = "";

   if(lot <= 0.0)
     {
      err = "volume must be positive";
      return false;
     }
   if(!MathIsValidNumber(sl) || sl <= 0.0)
     {
      err = "refusing to open without a strict stop";
      return false;
     }

   int attempts = (opts.retries < 1 ? 1 : opts.retries);
   for(int i = 0; i < attempts; i++)
     {
      MqlTradeRequest req;
      MqlTradeResult  res;
      ZeroMemory(req);
      ZeroMemory(res);

      double price = (posType == POSITION_TYPE_BUY ? SymbolInfoDouble(opts.symbol, SYMBOL_ASK)
                                                  : SymbolInfoDouble(opts.symbol, SYMBOL_BID));
      if(!MathIsValidNumber(price) || price <= 0.0)
        {
         err = "no price available";
         break;
        }

      req.action       = TRADE_ACTION_DEAL;
      req.symbol       = opts.symbol;
      req.volume       = lot;
      req.type         = (posType == POSITION_TYPE_BUY ? ORDER_TYPE_BUY : ORDER_TYPE_SELL);
      req.price        = price;
      req.sl           = sl;
      req.tp           = (MathIsValidNumber(tp) && tp > 0.0 ? tp : 0.0);
      req.deviation    = (uint)(opts.slippage < 0 ? 0 : opts.slippage);
      req.magic        = (ulong)magic;
      req.comment      = comment;
      req.type_filling = ChPickFilling(opts.symbol);
      req.type_time    = ORDER_TIME_GTC;

      if(!OrderSend(req, res))
        {
         err = "OrderSend failed: " + ChRetcodeText(res.retcode) +
               " (last error " + IntegerToString((int)GetLastError()) + ")";
         if(opts.delayMs > 0)
            Sleep(opts.delayMs);
         continue;
        }

      if(ChRetcodeIsOk(res.retcode))
        {
         ticket    = res.order;
         fillPrice = (MathIsValidNumber(res.price) && res.price > 0.0 ? res.price : price);
         if(ticket == 0)
            ticket = res.deal;
         return true;
        }

      err = "rejected: " + ChRetcodeText(res.retcode) +
            " (retcode " + IntegerToString((int)res.retcode) + ")";
      if(opts.delayMs > 0)
         Sleep(opts.delayMs);
     }
   return false;
  }

//+------------------------------------------------------------------+
//| Modify the stop (and optionally the target) of a position        |
//| sl/tp <= 0 means "leave this field unchanged"                    |
//+------------------------------------------------------------------+
bool ChExecModifyStops(const SCharonExecOpts &opts, const ulong ticket,
                       const double sl, const double tp, string &err)
  {
   err = "";
   if(ticket == 0)
     {
      err = "invalid ticket";
      return false;
     }

   int attempts = (opts.retries < 1 ? 1 : opts.retries);
   for(int i = 0; i < attempts; i++)
     {
      if(!PositionSelectByTicket(ticket))
        {
         err = "position no longer exists";
         return false;
        }

      MqlTradeRequest req;
      MqlTradeResult  res;
      ZeroMemory(req);
      ZeroMemory(res);

      //--- a zero target means "leave the live target alone". Sending 0 would
      //--- silently DELETE an existing take profit, so re-read it instead.
      double keepTp = (MathIsValidNumber(tp) && tp > 0.0 ? tp
                                                         : PositionGetDouble(POSITION_TP));

      req.action   = TRADE_ACTION_SLTP;
      req.symbol   = opts.symbol;
      req.position = ticket;
      req.sl       = (MathIsValidNumber(sl) && sl > 0.0 ? sl : 0.0);
      req.tp       = (MathIsValidNumber(keepTp) && keepTp > 0.0 ? keepTp : 0.0);
      req.magic    = (ulong)PositionGetInteger(POSITION_MAGIC);

      if(!OrderSend(req, res))
        {
         err = "SLTP failed: " + ChRetcodeText(res.retcode) +
               " (last error " + IntegerToString((int)GetLastError()) + ")";
         if(opts.delayMs > 0)
            Sleep(opts.delayMs);
         continue;
        }

      if(ChRetcodeIsOk(res.retcode))
         return true;

      err = "SLTP rejected: " + ChRetcodeText(res.retcode) +
            " (retcode " + IntegerToString((int)res.retcode) + ")";

      //--- price ran away from the requested stop: retry, the caller
      //--- recomputes against fresh prices on the next tick anyway
      if(res.retcode == TRADE_RETCODE_INVALID_STOPS ||
         res.retcode == TRADE_RETCODE_PRICE_CHANGED ||
         res.retcode == TRADE_RETCODE_REQUOTE)
        {
         if(opts.delayMs > 0)
            Sleep(opts.delayMs);
         continue;
        }
      break;
     }
   return false;
  }

//+------------------------------------------------------------------+
//| Close a position at market (full or partial), with retries       |
//+------------------------------------------------------------------+
bool ChExecClosePosition(const SCharonExecOpts &opts, const ulong ticket,
                         const double lot, string &err)
  {
   err = "";
   if(ticket == 0)
     {
      err = "invalid ticket";
      return false;
     }

   int attempts = (opts.retries < 1 ? 1 : opts.retries);
   for(int i = 0; i < attempts; i++)
     {
      if(!PositionSelectByTicket(ticket))
        {
         err = "position no longer exists";
         return false;
        }

      ENUM_POSITION_TYPE ptype = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      double volume = PositionGetDouble(POSITION_VOLUME);
      if(lot > 0.0 && lot < volume)
         volume = ChNormalizeLot(lot, SymbolInfoDouble(opts.symbol, SYMBOL_VOLUME_STEP));

      MqlTradeRequest req;
      MqlTradeResult  res;
      ZeroMemory(req);
      ZeroMemory(res);

      req.action       = TRADE_ACTION_DEAL;
      req.symbol       = opts.symbol;
      req.position     = ticket;
      req.volume       = volume;
      req.type         = (ptype == POSITION_TYPE_BUY ? ORDER_TYPE_SELL : ORDER_TYPE_BUY);
      req.price        = (ptype == POSITION_TYPE_BUY ? SymbolInfoDouble(opts.symbol, SYMBOL_BID)
                                                     : SymbolInfoDouble(opts.symbol, SYMBOL_ASK));
      req.deviation    = (uint)(opts.slippage < 0 ? 0 : opts.slippage);
      req.magic        = (ulong)PositionGetInteger(POSITION_MAGIC);
      req.comment      = CHARON_COMMENT + " close";
      req.type_filling = ChPickFilling(opts.symbol);

      if(!OrderSend(req, res))
        {
         err = "close failed: " + ChRetcodeText(res.retcode) +
               " (last error " + IntegerToString((int)GetLastError()) + ")";
         if(opts.delayMs > 0)
            Sleep(opts.delayMs);
         continue;
        }

      if(ChRetcodeIsOk(res.retcode))
         return true;

      err = "close rejected: " + ChRetcodeText(res.retcode) +
            " (retcode " + IntegerToString((int)res.retcode) + ")";
      if(opts.delayMs > 0)
         Sleep(opts.delayMs);
     }
   return false;
  }

//+------------------------------------------------------------------+
//| Place a pending limit order with a strict stop already attached  |
//|                                                                  |
//|  A pending ticket is the only way to express "enter here, not     |
//|  now" on this broker. It goes out with its stop in the same       |
//|  request, so the moment it fills the resulting position is        |
//|  already protected -- there is no unprotected window between the  |
//|  fill and a follow-up modify.                                     |
//|                                                                  |
//|  price must already be normalized and must respect the broker's   |
//|  stop level; the caller validates that before calling in, because |
//|  the direction of the stop depends on the block geometry the      |
//|  caller owns.                                                     |
//+------------------------------------------------------------------+
bool ChExecOpenPending(const SCharonExecOpts &opts, const ENUM_POSITION_TYPE posType,
                       const double lot, const double price, const double sl,
                       const long magic, const string comment,
                       ulong &ticket, string &err)
  {
   ticket = 0;
   err    = "";

   if(lot <= 0.0)
     {
      err = "volume must be positive";
      return false;
     }
   if(!MathIsValidNumber(sl) || sl <= 0.0)
     {
      err = "refusing to place a pending order without a strict stop";
      return false;
     }
   if(!MathIsValidNumber(price) || price <= 0.0)
     {
      err = "pending price must be positive";
      return false;
     }

   int attempts = (opts.retries < 1 ? 1 : opts.retries);
   for(int i = 0; i < attempts; i++)
     {
      MqlTradeRequest req;
      MqlTradeResult  res;
      ZeroMemory(req);
      ZeroMemory(res);

      req.action       = TRADE_ACTION_PENDING;
      req.symbol       = opts.symbol;
      req.volume       = lot;
      req.type         = (posType == POSITION_TYPE_BUY ? ORDER_TYPE_BUY_LIMIT
                                                       : ORDER_TYPE_SELL_LIMIT);
      req.price        = price;
      req.sl           = sl;
      req.tp           = 0.0;
      req.magic        = (ulong)magic;
      req.comment      = comment;
      req.type_filling = ChPickFilling(opts.symbol);
      req.type_time    = ORDER_TIME_GTC;

      if(!OrderSend(req, res))
        {
         err = "pending OrderSend failed: " + ChRetcodeText(res.retcode) +
               " (last error " + IntegerToString((int)GetLastError()) + ")";
         if(opts.delayMs > 0)
            Sleep(opts.delayMs);
         continue;
        }

      if(ChRetcodeIsOk(res.retcode))
        {
         ticket = res.order;
         return (ticket > 0);
        }

      err = "pending rejected: " + ChRetcodeText(res.retcode) +
            " (retcode " + IntegerToString((int)res.retcode) + ")";
      if(opts.delayMs > 0)
         Sleep(opts.delayMs);
     }
   return false;
  }

//+------------------------------------------------------------------+
//| Remove a pending order by ticket                                 |
//+------------------------------------------------------------------+
bool ChExecCancelPending(const SCharonExecOpts &opts, const ulong ticket, string &err)
  {
   err = "";
   if(ticket == 0)
     {
      err = "invalid ticket";
      return false;
     }

   int attempts = (opts.retries < 1 ? 1 : opts.retries);
   for(int i = 0; i < attempts; i++)
     {
      MqlTradeRequest req;
      MqlTradeResult  res;
      ZeroMemory(req);
      ZeroMemory(res);

      req.action = TRADE_ACTION_REMOVE;
      req.order  = ticket;

      if(!OrderSend(req, res))
        {
         err = "cancel failed: " + ChRetcodeText(res.retcode) +
               " (last error " + IntegerToString((int)GetLastError()) + ")";
         if(opts.delayMs > 0)
            Sleep(opts.delayMs);
         continue;
        }

      if(ChRetcodeIsOk(res.retcode))
         return true;

      err = "cancel rejected: " + ChRetcodeText(res.retcode) +
            " (retcode " + IntegerToString((int)res.retcode) + ")";
      if(opts.delayMs > 0)
         Sleep(opts.delayMs);
     }
   return false;
  }

//+------------------------------------------------------------------+
#endif // __CHARON_TRADE__
//+------------------------------------------------------------------+

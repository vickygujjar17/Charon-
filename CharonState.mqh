//+------------------------------------------------------------------+
//|                                                  CharonState.mqh |
//|  Per-ticket state table, basket aggregation and persistence.     |
//|                                                                  |
//|  Persistence uses terminal GlobalVariables keyed by ticket so a   |
//|  restart (or a chart timeframe change) keeps the R reference,     |
//|  the high-water mark and the latches for every live position.     |
//+------------------------------------------------------------------+
#property copyright "Charon"
#property version   "1.05"

#ifndef __CHARON_STATE__
#define __CHARON_STATE__

#include "CharonDefines.mqh"
#include "CharonMath.mqh"

//+------------------------------------------------------------------+
//| CCharonState                                                     |
//+------------------------------------------------------------------+
class CCharonState
  {
private:
   SCharonPosState   m_rows[CHARON_MAX_STATES];
   int               m_count;

   string            Key(const ulong ticket, const string field) const;

public:
                     CCharonState(void);

   void              Clear(void);
   int               Count(void) const { return m_count; }

   //--- table access
   int               Find(const ulong ticket) const;
   int               Add(const ulong ticket, const long type, const double entry,
                         const double lot, const double initialSL, const double riskPrice,
                         const datetime entryTime, const int tier);
   void              RemoveAt(const int index);
   void              Compact(void);
   bool              Get(const int index, SCharonPosState &out) const;
   bool              GetByTicket(const ulong ticket, SCharonPosState &out) const;
   bool              Set(const int index, const SCharonPosState &row);

   //--- persistence
   void              Save(const int index, const bool force = false);
   void              FlushAll(void);
   bool              Load(const ulong ticket, const int tierDefault, SCharonPosState &out);
   void              Erase(const ulong ticket);
   void              ResetAll(void);
  };

//+------------------------------------------------------------------+
//| Constructor                                                      |
//+------------------------------------------------------------------+
CCharonState::CCharonState(void)
  {
   Clear();
  }

//+------------------------------------------------------------------+
//| Drop every row                                                   |
//+------------------------------------------------------------------+
void CCharonState::Clear(void)
  {
   for(int i = 0; i < CHARON_MAX_STATES; i++)
      ZeroMemory(m_rows[i]);
   m_count = 0;
  }

//+------------------------------------------------------------------+
//| GlobalVariable name for one ticket field                         |
//+------------------------------------------------------------------+
string CCharonState::Key(const ulong ticket, const string field) const
  {
   return (CHARON_GV_ROOT + "_" + IntegerToString((long)ticket) + "_" + field);
  }

//+------------------------------------------------------------------+
//| Index of a ticket in the table, -1 when absent                   |
//+------------------------------------------------------------------+
int CCharonState::Find(const ulong ticket) const
  {
   if(ticket == 0)
      return -1;
   for(int i = 0; i < m_count; i++)
     {
      if(m_rows[i].ticket == ticket)
         return i;
     }
   return -1;
  }

//+------------------------------------------------------------------+
//| Copy a row out (structs cannot be returned by pointer)           |
//+------------------------------------------------------------------+
bool CCharonState::Get(const int index, SCharonPosState &out) const
  {
   if(index < 0 || index >= m_count)
      return false;
   out = m_rows[index];
   return true;
  }

//+------------------------------------------------------------------+
//| Locate a row by its ticket                                      |
//|  Replaces the Get/Set pair callers would otherwise need: the     |
//|  index of a ticket is an implementation detail, and callers that  |
//|  re-derive it tend to drift from the table's ordering rules.      |
//+------------------------------------------------------------------+
bool CCharonState::GetByTicket(const ulong ticket, SCharonPosState &out) const
  {
   int index = Find(ticket);
   if(index < 0)
      return false;
   out = m_rows[index];
   return true;
  }

//+------------------------------------------------------------------+
//| Copy a row in                                                    |
//+------------------------------------------------------------------+
bool CCharonState::Set(const int index, const SCharonPosState &row)
  {
   if(index < 0 || index >= m_count)
      return false;
   m_rows[index] = row;
   return true;
  }

//+------------------------------------------------------------------+
//| Remove one row, preserving order of the remainder                |
//+------------------------------------------------------------------+
void CCharonState::RemoveAt(const int index)
  {
   if(index < 0 || index >= m_count)
      return;
   for(int i = index; i < m_count - 1; i++)
      m_rows[i] = m_rows[i + 1];
   ZeroMemory(m_rows[m_count - 1]);
   m_count--;
  }

//+------------------------------------------------------------------+
//| Drop rows whose position no longer exists                        |
//+------------------------------------------------------------------+
void CCharonState::Compact(void)
  {
   int write = 0;
   for(int i = 0; i < m_count; i++)
     {
      if(!PositionSelectByTicket(m_rows[i].ticket))
        {
         Erase(m_rows[i].ticket);
         continue;
        }
      if(write != i)
         m_rows[write] = m_rows[i];
      write++;
     }
   for(int i = write; i < m_count; i++)
      ZeroMemory(m_rows[i]);
   m_count = write;
  }

//+------------------------------------------------------------------+
//| Register (or refresh) a ticket. Returns the row index or -1.      |
//+------------------------------------------------------------------+
int CCharonState::Add(const ulong ticket, const long type, const double entry,
                      const double lot, const double initialSL, const double riskPrice,
                      const datetime entryTime, const int tier)
  {
   if(ticket == 0)
      return -1;

   int idx = Find(ticket);
   if(idx < 0)
     {
      if(m_count >= CHARON_MAX_STATES)
         return -1;
      idx = m_count;
      ZeroMemory(m_rows[idx]);
      m_count++;
     }

   m_rows[idx].ticket    = ticket;
   m_rows[idx].type      = type;
   m_rows[idx].entry     = entry;
   m_rows[idx].lot       = lot;
   m_rows[idx].initialSL = initialSL;
   m_rows[idx].riskPrice = riskPrice;
   m_rows[idx].entryTime = entryTime;
   m_rows[idx].tier      = ChClampI(tier, 1, CHARON_MAX_TIERS);
   if(m_rows[idx].currentSL <= 0.0 && initialSL > 0.0)
      m_rows[idx].currentSL = initialSL;
   if(m_rows[idx].hwm <= 0.0 && entry > 0.0)
      m_rows[idx].hwm = entry;
   return idx;
  }

//+------------------------------------------------------------------+
//| Persist the non-derivable fields of one row                      |
//|                                                                  |
//|  HWM changes on almost every tick, so writes are throttled to     |
//|  one per second per ticket; a forced save happens on adoption,    |
//|  on a stop change and on shutdown.                                |
//+------------------------------------------------------------------+
void CCharonState::Save(const int index, const bool force)
  {
   if(!InpPersistState || index < 0 || index >= m_count)
      return;

   datetime now = TimeCurrent();
   if(!force && m_rows[index].savedAt > 0 &&
      now - m_rows[index].savedAt < CHARON_SAVE_INTERVAL_SEC)
      return;
   m_rows[index].savedAt = now;

   const ulong tk = m_rows[index].ticket;
   GlobalVariableSet(Key(tk, "sl"),       m_rows[index].initialSL);
   GlobalVariableSet(Key(tk, "risk"),     m_rows[index].riskPrice);
   GlobalVariableSet(Key(tk, "hwm"),      m_rows[index].hwm);
   GlobalVariableSet(Key(tk, "tier"),     (double)m_rows[index].tier);
   GlobalVariableSet(Key(tk, "time"),     (double)m_rows[index].entryTime);
   GlobalVariableSet(Key(tk, "trail"),    (m_rows[index].trailActive ? 1.0 : 0.0));
   GlobalVariableSet(Key(tk, "half"),     (m_rows[index].halfRiskDone ? 1.0 : 0.0));
   GlobalVariableSet(Key(tk, "be"),       (m_rows[index].beDone ? 1.0 : 0.0));
   GlobalVariableSet(Key(tk, "tp"),       (m_rows[index].tpApplied ? 1.0 : 0.0));
   GlobalVariableSet(Key(tk, "cur"),      m_rows[index].currentSL);
   GlobalVariableSet(Key(tk, "applies"),  (double)m_rows[index].stopApplies);
   //--- v1.4.0 per-ticket trail thresholds. A pre-1.4.0 install simply has
   //--- no such variables, and Load() defaults them to 0 -- which means
   //--- "use the chart input", exactly the old behaviour.
   GlobalVariableSet(Key(tk, "trR"),      m_rows[index].trailStartRR);
   GlobalVariableSet(Key(tk, "trP"),      m_rows[index].trailStartPoints);
   //--- v1.5.0 activation expressed as an absolute price. 0 means the row
   //--- still derives its threshold from the chart input.
   GlobalVariableSet(Key(tk, "trX"),      m_rows[index].trailStartPrice);
  }

//+------------------------------------------------------------------+
//| Force-persist every tracked row (called on shutdown)             |
//+------------------------------------------------------------------+
void CCharonState::FlushAll(void)
  {
   for(int i = 0; i < m_count; i++)
      Save(i, true);
  }

//+------------------------------------------------------------------+
//| Restore a row from GlobalVariables; false when nothing is stored |
//+------------------------------------------------------------------+
bool CCharonState::Load(const ulong ticket, const int tierDefault, SCharonPosState &out)
  {
   ZeroMemory(out);
   if(!InpPersistState || ticket == 0)
      return false;

   string kRisk = Key(ticket, "risk");
   if(!GlobalVariableCheck(kRisk))
      return false;

   out.ticket    = ticket;
   out.initialSL = GlobalVariableGet(Key(ticket, "sl"));
   out.riskPrice = GlobalVariableGet(kRisk);
   out.hwm       = GlobalVariableGet(Key(ticket, "hwm"));
   out.tier      = ChClampI((int)GlobalVariableGet(Key(ticket, "tier")),
                           1, CHARON_MAX_TIERS);
   out.entryTime = (datetime)(long)GlobalVariableGet(Key(ticket, "time"));
   out.trailActive   = (GlobalVariableGet(Key(ticket, "trail"))   > 0.5);
   out.halfRiskDone  = (GlobalVariableGet(Key(ticket, "half"))    > 0.5);
   out.beDone        = (GlobalVariableGet(Key(ticket, "be"))      > 0.5);
   out.tpApplied     = (GlobalVariableGet(Key(ticket, "tp"))      > 0.5);
   out.currentSL     = GlobalVariableGet(Key(ticket, "cur"));
   out.stopApplies   = (int)GlobalVariableGet(Key(ticket, "applies"));
   //--- 0 when absent, so a row restored from a pre-1.4.0 build falls back
   //--- to the chart-wide thresholds instead of disabling the trail.
   out.trailStartRR     = GlobalVariableGet(Key(ticket, "trR"));
   out.trailStartPoints = GlobalVariableGet(Key(ticket, "trP"));
   out.trailStartPrice  = GlobalVariableGet(Key(ticket, "trX"));

   if(out.tier <= 0)
      out.tier = ChClampI(tierDefault, 1, CHARON_MAX_TIERS);
   return (out.riskPrice > 0.0);
  }

//+------------------------------------------------------------------+
//| Delete every stored field for a ticket                           |
//+------------------------------------------------------------------+
void CCharonState::Erase(const ulong ticket)
  {
   if(ticket == 0)
      return;
   string fields[] = {"sl","risk","hwm","tier","time","trail","half","be","tp","cur","applies","trR","trP","trX"};
   for(int i = 0; i < ArraySize(fields); i++)
     {
      string k = Key(ticket, fields[i]);
      if(GlobalVariableCheck(k))
         GlobalVariableDel(k);
     }
  }

//+------------------------------------------------------------------+
//| Remove every Charon-prefixed global variable                     |
//+------------------------------------------------------------------+
void CCharonState::ResetAll(void)
  {
   int total = GlobalVariablesTotal();
   for(int i = total - 1; i >= 0; i--)
     {
      string name = GlobalVariableName(i);
      if(StringFind(name, CHARON_GV_ROOT + "_") == 0)
         GlobalVariableDel(name);
     }
   Clear();
  }

//+------------------------------------------------------------------+
#endif // __CHARON_STATE__

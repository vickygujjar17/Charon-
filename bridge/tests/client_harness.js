// Executes static/app.js against a minimal DOM stub and asserts that it
// actually issues a request to /api/status and populates the page.
//
// This is the check that matters: the v1.0.3 file parsed perfectly and did
// nothing. Proving the repaired file runs is different from proving it parses.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const source = fs.readFileSync(
  path.resolve(__dirname, "..", "static", "app.js"),
  "utf8"
);

// --- a DOM small enough to read, complete enough to run the client
class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.dataset = {};
    this.textContent = "";
    this.title = "";
    this.disabled = false;
    this.value = "";
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.listeners = {};
    this.id = "";
    this.parent = null;
    this.attrs = {};
    // --- v1.6.1: focus() and closest() are load-bearing now that the ticket's
    // --- symbol picker is a real popover, so the stub has to model them.
    this.focused = false;
    this.classList = {
      _element: this,
      add: (c) => {
        this.classList._set.add(c);
        this._syncClassName();
      },
      remove: (c) => {
        this.classList._set.delete(c);
        this._syncClassName();
      },
      toggle: (c, on) => {
        const want = on === undefined ? !this.classList._set.has(c) : Boolean(on);
        if (want) this.classList._set.add(c);
        else this.classList._set.delete(c);
        this._syncClassName();
        return want;
      },
      contains: (c) => this.classList._set.has(c),
    };
    this.classList._set = new Set();
    this._className = "";
  }
  // --- className and classList must never disagree: the client sets one
  // --- (className = "hidden") and tests the other (classList.toggle("hidden")),
  // --- so a stub that kept them in separate boxes would let a real desync pass
  // --- unnoticed -- including a popover that is "open" by one measure and
  // --- "hidden" by the other.
  get className() { return this._className; }
  set className(next) {
    this._className = String(next);
    this.classList._set = new Set(this._className.split(/\s+/).filter(Boolean));
  }
  _syncClassName() {
    this._className = [...this.classList._set].join(" ");
  }
  get childElementCount() { return this.children.length; }
  get firstChild() { return this.children[0] || null; }
  get parentElement() { return this.parent; }
  append(...nodes) {
    for (const node of nodes) {
      if (node instanceof Element) {
        const at = this.children.indexOf(node);
        if (at !== -1) this.children.splice(at, 1);
        node.parent = this;
      }
      this.children.push(node);
    }
  }
  appendChild(node) { this.append(node); return node; }
  removeChild(node) {
    const at = this.children.indexOf(node);
    if (at !== -1) this.children.splice(at, 1);
    if (node instanceof Element) node.parent = null;
    return node;
  }
  remove() { if (this.parent) this.parent.removeChild(this); }
  replaceChildren(...nodes) {
    for (const child of this.children) {
      if (child instanceof Element) child.parent = null;
    }
    this.children = [];
    this.append(...nodes);
  }
  // --- focus bookkeeping: the client measures "does a repaint steal the
  // --- operator's cursor?" with document.activeElement !== node, so the stub has
  // --- to move that pointer the way a browser does.
  focus() {
    if (document.activeElement && document.activeElement !== this) {
      document.activeElement.focused = false;
    }
    this.focused = true;
    document.activeElement = this;
  }
  blur() {
    this.focused = false;
    if (document.activeElement === this) document.activeElement = null;
  }
  scrollIntoView() {}
  setAttribute(name, value) {
    const text = String(value);
    this.attrs[name] = text;
    if (name === "id") this.id = text;
    if (name === "class") this.className = text;
  }
  getAttribute(name) { return this.attrs[name] ?? null; }
  hasAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name); }
  removeAttribute(name) { delete this.attrs[name]; }
  // --- does this node contain `other`? The click-outside listener asks exactly
  // --- this, and a stub that answered wrongly would dismiss the popover on the
  // --- very click that opened it.
  contains(other) {
    let node = other;
    while (node) {
      if (node === this) return true;
      node = node.parent || null;
    }
    return false;
  }
  // --- nearest self-or-ancestor matching `[data-x]`. Used for delegated row
  // --- clicks, so it has to walk up rather than just filter children.
  closest(selector) {
    const attr = selector.match(/^\[data-([\w-]+)\]$/);
    if (!attr) return this._match(selector) ? this : null;
    let node = this;
    while (node) {
      if (node instanceof Element && String(node.dataset[attr[1]] ?? "") !== "") {
        return node;
      }
      node = node.parent || null;
    }
    return null;
  }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  _match(selector) {
    const attr = selector.match(/^(\w+)?\[data-([\w-]+)="?([^"\]]*)"?\]$/);
    if (attr) {
      if (attr[1] && this.tagName !== attr[1].toUpperCase()) return false;
      return String(this.dataset[attr[2]] ?? "") === attr[3];
    }
    // --- case-insensitive tag match, so "option" finds an <option>
    return this.tagName === selector.toUpperCase();
  }
  querySelectorAll(selector) {
    return this.children.filter(
      (child) => child instanceof Element && child._match(selector)
    );
  }
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
}

const registry = new Map();
const document = {
  activeElement: null,
  listeners: {},
  getElementById: (id) => registry.get(id) || null,
  createElement: (tag) => new Element(tag),
  // --- the equity curve is an inline SVG, so the client reaches for the
  // --- namespaced factory rather than createElement
  createElementNS: (_ns, tag) => new Element(tag),
  createTextNode: (text) => ({ nodeType: 3, text }),
  querySelectorAll: () => [],
  // --- delegated listeners (v1.4.0 row actions) attach to the document, so the
  // --- stub has to accept them or boot() fails before it ever polls.
  addEventListener(type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
  },
  removeEventListener(type, fn) {
    const list = this.listeners[type] || [];
    const at = list.indexOf(fn);
    if (at !== -1) list.splice(at, 1);
  },
};

// --- ids used by the client, so nothing is null
const IDS = ["banner", "statusPill", "ageLabel", "latencyLabel", "lastGoodLabel",
  "symbolLabel", "eaVersion", "equity", "balance", "openPl", "riskPct", "riskMoney",
  "basketDir", "basketLegs", "basketAvg", "basketLot", "ladder", "pyramidTrigger",
  "armState", "armCountdown", "armBtn", "armHint", "armMsg", "disarmBtn",
  "trailSelect", "trailMsg", "legRows", "noLegs", "logBox",
  "bridgeVersion", "schemaVersion", "bridgeRoot",
  // --- new order panel (v1.1.0)
  "tagline", "orderStatus", "orderSide", "orderType", "orderEntry", "orderRisk",
  "orderBlockTop", "orderBlockBottom", "orderTrail", "orderRung",
  "orderLots", "orderEntryPx", "orderStop", "orderRiskMoney", "orderTicket",
  "orderVerdict", "orderPlaceBtn", "orderModifyBtn", "orderCancelBtn", "orderMsg",
  // --- institutional ticket + working orders (v1.4.0)
  "orderRiskUnit", "orderRiskLabel", "orderSideHint", "orderTrailStart",
  "orderTrailPrice",
  "orderBasketCeiling", "orderBasketCeilingHint", "orderJournal", "journalHint",
  "orderJournalTag", "orderSymbolHint",
  "stageRows", "stageHint",
  "pendingPanel", "pendingBadge", "pendingSummary", "pendingRows", "noPending",
  "pendingTableWrap",
  // --- symbol picker + unmanaged panel (v1.3.0)
  "symbolPicker", "symbolSelect", "orderSymbol",
  // --- ticket symbol combobox (v1.6.1)
  "symbolCombo", "symbolTrigger", "symbolTriggerLabel", "symbolTriggerBadge",
  "symbolPopover", "symbolSearch", "symbolList", "symbolListFoot",
  "orderQuote", "orderQuoteSpread",
  "unmanagedPanel", "unmanagedBadge", "unmanagedSummary", "unmanagedBody",
  "unmanagedTableWrap", "unmanagedRows",
  // --- ticker strip + KPI footnotes (v1.6.0)
  "tickSymbol", "tickBasket", "tickPl", "tickEquity", "tickRisk", "tickSession",
  "equityFoot", "balanceFoot", "openPlFoot", "closedPl", "closedFoot",
  // --- closed-trade history (v1.6.0)
  "history", "historyBadge", "historyDays", "histTrades", "histTradesFoot",
  "histWinRate", "histWinRateFoot", "histProfitFactor", "histProfitFactorFoot",
  "histExpectancy", "histExpectancyFoot", "histAvgRr", "histAvgRrFoot",
  "histClock", "histEquityFoot", "equityCurve", "sessionRows",
  "historyTableWrap", "historyRows", "historyEmpty",
  // --- journal write-back (v1.6.0)
  "journal", "journalBadge", "journalFoot", "journalTicket", "journalNote",
  "journalPsych", "journalTags", "journalSaveBtn", "journalClearBtn",
  "journalMsg", "journalTableWrap", "journalRows", "journalEmpty"];

for (const id of IDS) {
  const el = new Element(id === "legRows" ? "tbody" : "div");
  el.id = id;
  registry.set(id, el);
}

// --- Wire up the symbol combobox's ancestry for real. The client's
// --- click-outside handler asks whether the event target is inside
// --- #symbolCombo, so a stub with flat, parentless nodes would dismiss the
// --- popover on the very click that opened it -- and every combobox assertion
// --- after that would be testing a closed menu.
registry.get("symbolCombo").append(
  registry.get("symbolTrigger"),
  registry.get("symbolPopover")
);
registry.get("symbolTrigger").append(
  registry.get("symbolTriggerLabel"),
  registry.get("symbolTriggerBadge")
);
registry.get("symbolPopover").append(
  registry.get("symbolSearch"),
  registry.get("symbolList"),
  registry.get("symbolListFoot")
);

// --- the classes index.html ships with. The popover starts hidden and the badge
// --- starts suppressed, and the client is expected to be the only thing that
// --- changes either: a stub that began with everything visible would never catch
// --- a popover that opens but cannot close.
for (const [id, cls] of Object.entries({
  symbolPopover: "combo-popover hidden",
  symbolTriggerLabel: "truncate",
  symbolTriggerBadge: "tag hidden",
  symbolSearch: "combo-search",
  symbolList: "combo-list",
  symbolListFoot: "combo-foot",
  symbolCombo: "relative mt-1",
})) {
  registry.get(id).className = cls;
}

// --- a real dispatch: walks from the target to the document running every
// --- listener registered on the way, which is how bubbled `click` reaches the
// --- delegated document handler and how `input` reaches the search box's own.
function fire(target, type, props) {
  const event = {
    type,
    target,
    defaultPrevented: false,
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { this.stopped = true; },
    ...(props || {}),
  };
  let node = target;
  while (node) {
    const handlers = node.listeners && node.listeners[type];
    if (handlers) for (const handler of handlers) handler(event);
    if (event.stopped) break;
    node = node === document ? null : node.parent || document;
    if (node === document) {
      const top = document.listeners[type] || [];
      for (const handler of top) handler(event);
      break;
    }
  }
  return event;
}

const calls = [];
const snapshot = {
  status: "live",
  age_seconds: 0.4,
  stale_after: 5,
  offline_after: 30,
  snapshot: {
    symbol: "GBPUSD", seq: 7, ea_version: "1.0.4", ready: true, digits: 5,
    currency: "USD", balance: 10000, equity: 10120.5,
    // --- the EA's last tick. Digits is 5 here, so ask - bid = 0.00012 is
    // --- 12 points; a "pip" reading would call it 1.2.
    bid: 1.26010, ask: 1.26022,
    basket: { valid: true, mixed: false, direction: "BUY", legs: 1, lot: 0.3,
      avg_entry: 1.26, open_pl: 120.5, risk_money: 45, risk_pct: 0.4447,
      best: 1.269, ladder: "1.50R 1.26450 | 2.00R 1.26600" },
    model: {}, trail: { mode: 2, mode_name: "ATR", mutable: true, options: {} },
    pyramid: { enabled: true, trigger: "MANUAL", armed: false, secs_left: -1 },
    // --- the EA's verdict on the last ticket. The numbers here are
    // --- deliberately "odd" so an assertion can prove the client rendered
    // --- them verbatim instead of recomputing its own.
    order: { status: "accepted", reason: "BUY filled 0.37 lots", side: "BUY",
      lots: 0.37, entry: 1.26050, stop: 1.24980, risk_money: 39.79,
      risk_pct: 0.3931, rung: 2, ticket: 4242 },
    legs: [{ ticket: 111, side: "BUY", tier: 1, entry: 1.26, lot: 0.3, sl: 1.257,
      risk_price: 0.003, hwm: 1.269, progress_rr: 3.0, trail_active: true,
      be_done: true, half_risk_done: true, ladder: "1.50R 1.26450" }],
  },
  // --- the journal travels with /api/status, newest ticket first
  journal: [
    { ticket: 555, note: "held through the retest", tag: "HELD THROUGH",
      psych: "patient", tags: ["playbook-a"] },
  ],
};

// --- a mutable hook so a test can change the catalogue between polls: the
// --- picker is repainted on a timer, and a repaint that moved the keyboard
// --- cursor would make Enter send an instrument nobody looked at.
let symbolsPrepend = [];

function fetchStub(url) {
  calls.push(url);
  let body;
  if (url.includes("/api/health")) {
    body = {
      ok: true,
      bridge_version: "1.0.4",
      schema: 1,
      // --- the real /api/health always carries this; the client renders
      // --- the resolved path from it, so the stub must not omit it.
      locations: {
        common_files: "/tmp/common/Files",
        bridge_root: "/tmp/common/Files/Charon",
        common_exists: "yes",
        root_exists: "yes",
      },
      symbols: ["GBPUSD"],
    };
  } else if (url.includes("/api/symbols")) {
    // --- one chart-only name, one name that is both, and one broker-only name.
    // --- The classes are deliberately mixed so the grouping and the badge map
    // --- are exercised rather than assumed.
    body = {
      symbols: symbolsPrepend.concat([
        { symbol: "GBPUSD", label: "GBPUSD", sources: ["chart"], asset_class: "forex" },
        {
          symbol: "EURUSD.a",
          label: "EURUSD",
          sources: ["chart", "broker"],
          asset_class: "forex",
        },
        { symbol: "AUDUSD", label: "AUDUSD", sources: ["broker"], asset_class: "forex" },
        {
          symbol: "XAUUSD",
          label: "XAUUSD",
          sources: ["broker"],
          asset_class: "metals",
        },
        { symbol: "US30.cash", label: "US30", sources: ["broker"], asset_class: "indices" },
      ]),
      asset_classes: ["forex", "metals", "indices", "energy", "commodities", "crypto",
        "stocks", "other"],
      charts: ["GBPUSD", "EURUSD.a"],
      broker_available: true,
      broker_count: 5,
    };
  } else if (url.includes("/api/unmanaged")) {
    body = {
      symbols: [{ symbol: "GBPUSD", state: "ok", reason: "", legs: 1, age_seconds: 0.4 }],
      positions: [{
        ticket: 900, symbol: "USDJPY", side: "SELL", volume: 0.2, profit: -3.5,
        magic: 0, verdict: "no_charon_on_symbol", seen: 1, confirmed: true,
        unmanaged_for: 0, reason: "no Charon chart is publishing USDJPY",
      }],
      pending: [],
      summary: { state: "ok", confirmed: 1, pending: 0, clean: false, confirmations: 3 },
    };
  } else if (url.includes("/api/history")) {
    // --- a closed round trip, so the history panel has something real to
    // --- format. Ratios are present (not null) because both a win and a loss
    // --- closed; the null case is covered by the Python API tests.
    body = {
      available: true,
      reason: null,
      symbol: "GBPUSD",
      days: 30,
      tz_source: "snapshot",
      tz_offset_minutes: 180,
      trades: [
        {
          position_id: 2, symbol: "GBPUSD", side: "BUY", volume: 0.5,
          entry_price: 1.26450, exit_price: 1.26950, opened_at: 1_760_000_000,
          closed_at: 1_760_003_600, profit: 250.0, swap: 0.0, commission: -7.0,
          realized: 243.0, outcome: "win", duration_seconds: 3600,
          session: "LONDON", comment: "tp1",
        },
        {
          position_id: 1, symbol: "GBPUSD", side: "SELL", volume: 0.5,
          entry_price: 1.26500, exit_price: 1.26650, opened_at: 1_759_990_000,
          closed_at: 1_759_996_400, profit: -75.0, swap: -1.0, commission: -7.0,
          realized: -83.0, outcome: "loss", duration_seconds: 6400,
          session: "NEW_YORK", comment: "",
        },
      ],
      metrics: {
        total: 2, wins: 1, losses: 1, flat: 0, net_realized: 160.0,
        gross_profit: 243.0, gross_loss: 83.0, win_rate: 0.5,
        profit_factor: 2.9277, avg_win: 243.0, avg_loss: 83.0,
        avg_rr: 2.9277, expectancy: 80.0, max_win_streak: 1,
        max_loss_streak: 1, best: 243.0, worst: -83.0, avg_hold_seconds: 5000,
      },
      sessions: [
        { session: "ASIAN", window: "00:00-08:00", total: 0, wins: 0, losses: 0,
          flat: 0, net_realized: 0, win_rate: null, profit_factor: null },
        { session: "LONDON", window: "08:00-13:00", total: 1, wins: 1, losses: 0,
          flat: 0, net_realized: 243.0, win_rate: 1.0, profit_factor: null },
        { session: "NEW_YORK", window: "13:00-22:00", total: 1, wins: 0, losses: 1,
          flat: 0, net_realized: -83.0, win_rate: 0.0, profit_factor: 0 },
      ],
      equity: [
        { closed_at: 1_759_990_000, cumulative: 0.0, position_id: 0 },
        { closed_at: 1_759_996_400, cumulative: -83.0, position_id: 1 },
        { closed_at: 1_760_003_600, cumulative: 160.0, position_id: 2 },
      ],
      session_windows: [
        { session: "ASIAN", from: 0, to: 8 },
        { session: "LONDON", from: 8, to: 13 },
        { session: "NEW_YORK", from: 13, to: 22 },
      ],
    };
  } else if (url.includes("/api/journal")) {
    // --- a write never reaches the EA; the stub answers like the real route
    body = {
      saved: true,
      symbol: "GBPUSD",
      entry: {
        ticket: 555, note: "held through the retest", tag: "HELD THROUGH",
        psych: "patient", tags: ["playbook-a"],
      },
    };
  } else {
    body = snapshot;
  }
  return Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve(body),
  });
}

class EventSourceStub {
  constructor(url) { this.url = url; this.readyState = 0; }
  close() {}
}

const timers = [];
const context = {
  document,
  window: {},
  fetch: fetchStub,
  EventSource: EventSourceStub,
  AbortController: class { constructor() { this.signal = {}; } abort() {} },
  performance: { now: () => Date.now() },
  setTimeout: (fn, ms) => { timers.push(["timeout", ms]); return timers.length; },
  clearTimeout: () => {},
  setInterval: (fn, ms) => { timers.push(["interval", ms]); return timers.length; },
  console,
};
context.globalThis = context;

vm.createContext(context);
vm.runInContext(source, context, { filename: "app.js" });


// --- let the promise chain from boot() settle
setTimeout(async () => {
  const problems = [];

  if (!calls.some((url) => url.includes("/api/status"))) {
    problems.push("the client never requested /api/status");
  }
  if (!calls.some((url) => url.includes("/api/health"))) {
    problems.push("the client never requested /api/health");
  }

  const equity = registry.get("equity").textContent;
  if (!String(equity).includes("10,120")) {
    problems.push(`equity was not populated (got ${JSON.stringify(equity)})`);
  }
  const pill = registry.get("statusPill").textContent;
  if (pill !== "live") {
    problems.push(`status pill says ${JSON.stringify(pill)}, expected "live"`);
  }
  const rows = registry.get("legRows").children.length;
  if (rows !== 1) {
    problems.push(`legs table has ${rows} rows, expected 1`);
  }
  const rungs = registry.get("ladder").children.length;
  if (rungs !== 2) {
    problems.push(`basket ladder rendered ${rungs} rungs, expected 2`);
  }
  const latency = registry.get("latencyLabel").textContent;
  if (!String(latency).includes("ms")) {
    problems.push(`latency badge not set (got ${JSON.stringify(latency)})`);
  }
  const lastGood = registry.get("lastGoodLabel").textContent;
  if (!String(lastGood).startsWith("last good")) {
    problems.push(`last-good label not set (got ${JSON.stringify(lastGood)})`);
  }
  if (!timers.some(([kind]) => kind === "interval")) {
    problems.push("the client never scheduled a refresh interval");
  }

  // --- new order panel: the EA's verdict must be rendered verbatim
  const orderPill = registry.get("orderStatus").textContent;
  if (orderPill !== "accepted") {
    problems.push(`order pill says ${JSON.stringify(orderPill)}, expected "accepted"`);
  }
  if (registry.get("orderLots").textContent !== "0.37") {
    problems.push(`order size shows ${JSON.stringify(registry.get("orderLots").textContent)}`);
  }
  if (registry.get("orderStop").textContent !== "1.24980") {
    problems.push(`order stop shows ${JSON.stringify(registry.get("orderStop").textContent)}`);
  }
  if (registry.get("orderTicket").textContent !== "#4242") {
    problems.push(`order ticket shows ${JSON.stringify(registry.get("orderTicket").textContent)}`);
  }
  if (!String(registry.get("orderVerdict").textContent).includes("0.37")) {
    problems.push("the order verdict line dropped the EA's own reason");
  }
  // --- the ticket form must not be pre-filled with a computed price
  if (registry.get("orderBlockTop").value !== "") {
    problems.push("the block top box was pre-filled by the client");
  }

  // --- symbol picker: two charts means a chooser, and its values must be
  // --- the exact broker names rather than the display labels
  if (!calls.some((url) => url.includes("/api/symbols"))) {
    problems.push("the client never requested /api/symbols");
  }
  const picker = registry.get("symbolPicker");
  if (picker.className.includes("hidden")) {
    problems.push("the symbol picker stayed hidden with two charts on offer");
  }
  const pickerOptions = registry.get("symbolSelect").children.length;
  if (pickerOptions !== 2) {
    problems.push(
      `the picker offered ${pickerOptions} charts, expected 2 (broker-only must not be listed)`
    );
  }
  const firstPick = registry.get("symbolSelect").children[0];
  if (!firstPick || firstPick.value !== "GBPUSD") {
    problems.push("the picker's value is not the exact broker name");
  }

  // --- the ticket's symbol is a real combobox (v1.6.1). These assertions are
  // --- about behaviour, not markup: they open it, type, arrow and press Enter,
  // --- then check what would actually be posted.
  const trigger = registry.get("symbolTrigger");
  const popover = registry.get("symbolPopover");
  const search = registry.get("symbolSearch");
  const list = registry.get("symbolList");
  const triggerLabel = registry.get("symbolTriggerLabel");

  if (!popover.className.includes("hidden")) {
    problems.push("the symbol popover was open before anything clicked it");
  }
  // --- the order field starts on the chart being shown
  if (registry.get("orderSymbol").value !== "GBPUSD") {
    problems.push(
      `the order symbol defaulted to ${JSON.stringify(registry.get("orderSymbol").value)}`
    );
  }
  if (triggerLabel.textContent !== "GBPUSD") {
    problems.push(
      `the trigger did not show the chosen symbol (got ${JSON.stringify(
        triggerLabel.textContent
      )})`
    );
  }

  fire(trigger, "click");
  if (popover.className.includes("hidden")) {
    problems.push("clicking the trigger did not open the symbol popover");
  }
  if (trigger.getAttribute("aria-expanded") !== "true") {
    problems.push("the trigger did not report aria-expanded=true while open");
  }
  if (document.activeElement !== search) {
    problems.push("opening the popover did not move focus into the search box");
  }
  // --- 5 instruments over 3 classes: 3 headings + 5 options
  if (list.children.length !== 8) {
    problems.push(
      `the open list rendered ${list.children.length} nodes, expected 8 (5 options + 3 headings)`
    );
  }
  const headings = list.children.filter((n) => n.className === "combo-group");
  if (headings.length !== 3) {
    problems.push(`the list has ${headings.length} class headings, expected 3`);
  }
  if (headings.some((n) => n.getAttribute("role") !== "presentation")) {
    problems.push("a class heading was exposed as a selectable option");
  }
  const options = list.children.filter((n) => n.getAttribute("role") === "option");
  if (options.length !== 5) {
    problems.push(`the list offered ${options.length} options, expected 5`);
  }
  // --- the badge is a real chip per asset class, and US30.cash must carry the
  // --- index badge rather than the equity one
  const us30 = options.find((n) => n.dataset.symbol === "US30.cash");
  if (!us30 || us30.children[1].textContent !== "IDX") {
    problems.push("US30.cash did not carry the index badge");
  }
  const eur = options.find((n) => n.dataset.symbol === "EURUSD.a");
  if (!eur || eur.children[0].textContent !== "EURUSD") {
    problems.push("a broker suffix bled into the displayed label");
  }
  if (search.getAttribute("aria-activedescendant") !== "symbolOption1") {
    problems.push(
      `aria-activedescendant was ${JSON.stringify(
        search.getAttribute("aria-activedescendant")
      )}, expected symbolOption1 (the first option, not the heading)`
    );
  }

  // --- typing filters on the canonical name AND the class heading. The raw row
  // --- for the typed text is appended last on purpose, so the first option --
  // --- what Enter takes -- is still a real instrument.
  search.value = "metal";
  fire(search, "input");
  const metalsOnly = list.children.filter((n) => n.getAttribute("role") === "option");
  const firstMetal = metalsOnly[0];
  if (!firstMetal || firstMetal.dataset.symbol !== "XAUUSD") {
    problems.push(
      `typing "metal" offered ${JSON.stringify(
        firstMetal && firstMetal.dataset.symbol
      )} first, expected XAUUSD`
    );
  }
  if (!firstMetal) {
    // --- a client that never painted the list has already been reported above;
    // --- every later assertion about a row would throw, and a harness that dies
    // --- on a TypeError cannot say what was wrong.
    problems.push("the list painted no rows, so the keyboard could not be checked");
  } else if (search.getAttribute("aria-activedescendant") !== firstMetal.id) {
    problems.push("the keyboard cursor did not land on the first real match");
  }
  if (registry.get("orderSymbol").value === "metal" && !firstMetal) {
    problems.push("the filter committed the raw query instead of waiting for a choice");
  }
  if (!String(registry.get("symbolListFoot").textContent).includes("instruments match")) {
    problems.push(
      `the footer did not disclose a narrowed list (got ${JSON.stringify(
        registry.get("symbolListFoot").textContent
      )})`
    );
  }
  // --- and the escape hatch must be REACHABLE, not merely present: arrowing to
  // --- the bottom of a filtered list is how an operator sends a name the bridge
  // --- has never heard of even while other rows match. A raw row that exists
  // --- but cannot be focused is a feature that only looks implemented.
  fire(search, "keydown", { key: "End" });
  const rawLast = list.children.filter((n) => n.getAttribute("role") === "option").pop();
  if (!rawLast || rawLast.dataset.symbol !== "metal") {
    problems.push("End did not reach the raw row for the typed text");
  }
  fire(search, "keydown", { key: "Enter" });
  if (registry.get("orderSymbol").value !== "metal") {
    problems.push(
      `End+Enter sent ${JSON.stringify(
        registry.get("orderSymbol").value
      )}, expected the typed text metal`
    );
  }



  // --- a name the bridge does not know is still offered verbatim, and Enter
  // --- sends exactly that: the escape hatch this picker exists to keep
  search.value = "GBPJPY.pro";
  fire(search, "input");
  const rawOptions = list.children.filter((n) => n.getAttribute("role") === "option");
  const rawRow = rawOptions.find((n) => n.dataset.symbol === "GBPJPY.pro");
  if (!rawRow) {
    problems.push("an unknown typed name was not offered as a selectable row");
  }
  fire(search, "keydown", { key: "ArrowDown" });
  fire(search, "keydown", { key: "Enter" });
  if (registry.get("orderSymbol").value !== "GBPJPY.pro") {
    problems.push(
      `Enter sent ${JSON.stringify(registry.get("orderSymbol").value)}, expected GBPJPY.pro`
    );
  }
  if (!String(registry.get("orderSymbolHint").textContent).includes("verbatim")) {
    problems.push("the hint did not say an unknown name is sent verbatim");
  }
  if (!popover.className.includes("hidden")) {
    problems.push("choosing a row did not close the popover");
  }
  if (trigger.getAttribute("aria-expanded") !== "false") {
    problems.push("the trigger still reported aria-expanded=true after closing");
  }

  // --- Escape must abort, not commit: re-open, type, then run away
  fire(trigger, "click");
  search.value = "xau";
  fire(search, "input");
  fire(search, "keydown", { key: "Escape" });
  if (registry.get("orderSymbol").value !== "GBPJPY.pro") {
    problems.push("Escape changed the chosen symbol");
  }
  if (document.activeElement !== trigger) {
    problems.push("Escape did not return focus to the trigger");
  }

  // --- choosing from the catalogue must send the EXACT name, never the label.
  // --- EURUSD.a's label is "EURUSD"; posting the label would address a symbol
  // --- the broker does not list.
  fire(trigger, "click");
  search.value = "eurusd.a";
  fire(search, "input");
  fire(search, "keydown", { key: "Enter" });
  if (registry.get("orderSymbol").value !== "EURUSD.a") {
    problems.push(
      `an exact-name pick sent ${JSON.stringify(
        registry.get("orderSymbol").value
      )}, which is not the broker name`
    );
  }
  if (triggerLabel.textContent !== "EURUSD") {
    problems.push(
      `the trigger showed ${JSON.stringify(
        triggerLabel.textContent
      )}, expected the label EURUSD`
    );
  }

  // --- the quote: bid/ask only for the symbol the bridge is actually reading.
  // --- Naming anything else must say so rather than borrow a neighbouring tick.
  const quoteText = String(registry.get("orderQuote").textContent);
  if (!quoteText.includes("no quote for EURUSD.a")) {
    problems.push(
      `the quote borrowed another symbol's price (got ${JSON.stringify(quoteText)})`
    );
  }
  fire(trigger, "click");
  const gbpRow = list.children.find(
    (n) => n.getAttribute("role") === "option" && n.dataset.symbol === "GBPUSD"
  );
  // --- guarded, because a client that never painted the list is exactly what
  // --- test_harness_can_fail feeds in: the harness has to report that as a
  // --- problem, not die on a TypeError before it can say anything.
  if (!gbpRow) {
    problems.push("the list never rendered a row for GBPUSD");
  } else {
    fire(gbpRow.children[0], "click");
  }
  if (registry.get("orderSymbol").value !== "GBPUSD") {
    problems.push("clicking a row did not choose its symbol");
  }
  const ownQuote = String(registry.get("orderQuote").textContent);
  if (!ownQuote.includes("1.26010") || !ownQuote.includes("1.26022")) {
    problems.push(
      `the quote did not show the published bid/ask (got ${JSON.stringify(ownQuote)})`
    );
  }
  const spreadText = String(registry.get("orderQuoteSpread").textContent);
  if (!spreadText.includes("12") || !spreadText.includes("points")) {
    problems.push(`the spread read ${JSON.stringify(spreadText)}, expected 12 points`);
  }
  if (/pip/i.test(spreadText)) {
    problems.push(
      "the spread was labelled in pips, which is wrong on 3-digit JPY and cash indices"
    );
  }

  // --- a background repaint must not move the keyboard cursor. /api/symbols is
  // --- polled every 10 s and the client repaints the open list, so an operator
  // --- browsing with the arrow keys would otherwise have the highlight yanked
  // --- back to the top match under their hands -- and the next Enter would send
  // --- an instrument they never looked at. The rows are SHIFTED between polls
  // --- (a new chart sorts above them) so holding the cursor by index cannot
  // --- pass this: only holding it by symbol can.
  fire(trigger, "click");
  search.value = "";
  fire(search, "input");
  fire(search, "keydown", { key: "ArrowDown" });
  const browsing = list.children.filter((n) => n.getAttribute("role") === "option");
  const heldRow = browsing.find((n) => n.dataset.active === "true");
  if (!heldRow) {
    problems.push("no row was highlighted after arrowing through the list");
  } else {
    const heldSymbol = heldRow.dataset.symbol;
    const heldIndex = heldRow.dataset.index;
    symbolsPrepend = [
      { symbol: "NZDUSD", label: "NZDUSD", sources: ["chart"], asset_class: "forex" },
    ];
    await context.loadSymbols();
    const after = list.children
      .filter((n) => n.getAttribute("role") === "option")
      .find((n) => n.dataset.active === "true");
    if (!after || after.dataset.symbol !== heldSymbol) {
      problems.push(
        `a background repaint moved the cursor from ${JSON.stringify(
          heldSymbol
        )} to ${JSON.stringify(after && after.dataset.symbol)}`
      );
    }
    if (!after || after.dataset.index === heldIndex) {
      problems.push(
        "the stub did not shift the rows, so the repaint proved nothing"
      );
    }
    if (search.getAttribute("aria-activedescendant") !== (after && after.id)) {
      problems.push("a background repaint left aria-activedescendant elsewhere");
    }
    // --- the highlight is only the promise. Enter is what posts to /api/order,
    // --- so that is what has to agree with what the operator was looking at.
    fire(search, "keydown", { key: "Enter" });
    if (registry.get("orderSymbol").value !== heldSymbol) {
      problems.push(
        `after a repaint Enter sent ${JSON.stringify(
          registry.get("orderSymbol").value
        )}, but the operator was looking at ${JSON.stringify(heldSymbol)}`
      );
    }
  }
  symbolsPrepend = [];

  // --- the price boxes are stepped to the instrument's digits, so a 5-digit
  // --- pair takes 0.00001 increments -- and no price is ever written into them
  if (registry.get("orderBlockTop").step !== "1e-5") {
    problems.push(
      `the block-top step is ${JSON.stringify(
        registry.get("orderBlockTop").step
      )}, expected 1e-5 from digits=5`
    );
  }
  if (registry.get("orderBlockTop").value !== "") {
    problems.push("the quote repaint wrote a price into the block top box");
  }

  // --- unmanaged panel: a confirmed orphan must be rendered, and the badge
  // --- must never read as an all-clear while one is listed
  if (!calls.some((url) => url.includes("/api/unmanaged"))) {
    problems.push("the client never requested /api/unmanaged");
  }
  const unmanagedBadge = registry.get("unmanagedBadge").textContent;
  if (unmanagedBadge === "all clear") {
    problems.push("the unmanaged badge said \"all clear\" with an orphan listed");
  }
  if (registry.get("unmanagedRows").children.length !== 1) {
    problems.push(
      `the unmanaged table has ${registry.get("unmanagedRows").children.length} rows, expected 1`
    );
  }
  if (registry.get("unmanagedPanel").className.includes("hidden")) {
    problems.push("the unmanaged panel stayed hidden with a finding to show");
  }

  // --- closed-trade history: the panel must have fetched, formatted the
  // --- realized figure, and never print a ratio it was not given
  if (!calls.some((url) => url.includes("/api/history"))) {
    problems.push("the client never requested /api/history");
  }
  const closedPl = registry.get("closedPl").textContent;
  if (!String(closedPl).includes("160")) {
    problems.push(`the realized total was not rendered (got ${JSON.stringify(closedPl)})`);
  }
  if (registry.get("histWinRate").textContent !== "50.0%") {
    problems.push(
      `the win rate did not format as a percentage (got ${JSON.stringify(
        registry.get("histWinRate").textContent
      )})`
    );
  }
  if (registry.get("histProfitFactor").textContent !== "2.93") {
    problems.push(
      `the profit factor did not format (got ${JSON.stringify(
        registry.get("histProfitFactor").textContent
      )})`
    );
  }
  if (registry.get("sessionRows").children.length !== 3) {
    problems.push(
      `the session matrix has ${registry.get("sessionRows").children.length} rows, expected 3`
    );
  }
  if (registry.get("historyRows").children.length !== 2) {
    problems.push(
      `the closed-trade table has ${registry.get("historyRows").children.length} rows, expected 2`
    );
  }
  if (!registry.get("histClock").textContent.includes("+3:00")) {
    problems.push(
      `the session clock was not stated (got ${JSON.stringify(
        registry.get("histClock").textContent
      )})`
    );
  }
  if (registry.get("equityCurve").children.length < 2) {
    problems.push("the equity curve was not drawn");
  }

  // --- journal: entries from /api/status must appear, and a save must post
  const journalRows = registry.get("journalRows").children.length;
  if (journalRows !== 1) {
    problems.push(`the journal table has ${journalRows} rows, expected 1`);
  }
  if (registry.get("journalBadge").textContent !== "1") {
    problems.push(
      `the journal badge read ${JSON.stringify(registry.get("journalBadge").textContent)}`
    );
  }

  console.log("requests issued:");
  for (const url of calls) console.log("  " + url);
  console.log("");
  console.log("rendered values:");
  console.log("  equity      : " + registry.get("equity").textContent);
  console.log("  balance     : " + registry.get("balance").textContent);
  console.log("  openPl      : " + registry.get("openPl").textContent);
  console.log("  riskPct     : " + registry.get("riskPct").textContent);
  console.log("  closedPl    : " + registry.get("closedPl").textContent);
  console.log("  winRate     : " + registry.get("histWinRate").textContent);
  console.log("  profitFactor: " + registry.get("histProfitFactor").textContent);
  console.log("  statusPill  : " + registry.get("statusPill").textContent);
  console.log("  legRows     : " + registry.get("legRows").children.length);
  console.log("  ladder rungs: " + registry.get("ladder").children.length);
  console.log("  latency     : " + registry.get("latencyLabel").textContent);
  console.log("  lastGood    : " + registry.get("lastGoodLabel").textContent);
  console.log("  eaVersion   : " + registry.get("eaVersion").textContent);
  // --- the client's own log tail is the only place a thrown error inside a
  // --- promise surfaces, so print it rather than guess at a blank panel
  function flatText(node) {
    if (node === null || node === undefined) return "";
    if (typeof node === "string") return node;
    if (node.nodeType === 3) return node.text || "";
    let text = node.textContent || "";
    for (const child of node.children || []) text += flatText(child);
    return text;
  }
  const logLines = registry.get("logBox").children;
  if (logLines.length) {
    console.log("log tail    :");
    for (const line of logLines) console.log("  " + flatText(line));
  }
  console.log("  timers      : " + JSON.stringify(timers));
  console.log("");

  if (problems.length) {
    console.log("EXECUTION CHECK FAILED:");
    for (const problem of problems) console.log("  - " + problem);
    process.exit(1);
  }
  console.log("EXECUTION CHECK PASSED: the client polled the bridge and rendered it.");
}, 300);


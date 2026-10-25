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
    this.className = "";
    this.dataset = {};
    this.textContent = "";
    this.title = "";
    this.disabled = false;
    this.value = "";
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.listeners = {};
    this.id = "";
    this.classList = {
      _set: new Set(),
      add: (c) => this.classList._set.add(c),
      remove: (c) => this.classList._set.delete(c),
      toggle: (c, on) => (on ? this.classList._set.add(c) : this.classList._set.delete(c)),
      contains: (c) => this.classList._set.has(c),
    };
  }
  get childElementCount() { return this.children.length; }
  get firstChild() { return this.children[0] || null; }
  append(...nodes) {
    for (const node of nodes) {
      if (node instanceof Element) {
        const at = this.children.indexOf(node);
        if (at !== -1) this.children.splice(at, 1);
      }
      this.children.push(node);
    }
  }
  appendChild(node) { this.append(node); return node; }
  removeChild(node) {
    const at = this.children.indexOf(node);
    if (at !== -1) this.children.splice(at, 1);
    return node;
  }
  remove() { if (this.parent) this.parent.removeChild(this); }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
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
  getElementById: (id) => registry.get(id) || null,
  createElement: (tag) => new Element(tag),
  createTextNode: (text) => ({ nodeType: 3, text }),
  querySelectorAll: () => [],
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
  // --- symbol picker + unmanaged panel (v1.3.0)
  "symbolPicker", "symbolSelect", "orderSymbol", "symbolOptions",
  "unmanagedPanel", "unmanagedBadge", "unmanagedSummary", "unmanagedBody",
  "unmanagedTableWrap", "unmanagedRows"];

for (const id of IDS) {
  const el = new Element(id === "legRows" ? "tbody" : "div");
  el.id = id;
  registry.set(id, el);
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
};

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
    body = {
      symbols: [
        { symbol: "GBPUSD", label: "GBPUSD", sources: ["chart"] },
        { symbol: "EURUSD.a", label: "EURUSD", sources: ["chart", "broker"] },
        { symbol: "AUDUSD", label: "AUDUSD", sources: ["broker"] },
      ],
      charts: ["GBPUSD", "EURUSD.a"],
      broker_available: true,
      broker_count: 3,
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
setTimeout(() => {
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

  // --- the datalist must actually be populated, not left as a text node
  const suggestions = registry.get("symbolOptions").children;
  if (suggestions.length !== 3) {
    problems.push(`the symbol datalist has ${suggestions.length} options, expected 3`);
  }
  if (suggestions.some((node) => node.tagName !== "OPTION")) {
    problems.push("the datalist contains something other than <option> elements");
  }
  // --- and the order field starts on the chart being shown
  if (registry.get("orderSymbol").value !== "GBPUSD") {
    problems.push(
      `the order symbol defaulted to ${JSON.stringify(registry.get("orderSymbol").value)}`
    );
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

  console.log("requests issued:");
  for (const url of calls) console.log("  " + url);
  console.log("");
  console.log("rendered values:");
  console.log("  equity      : " + registry.get("equity").textContent);
  console.log("  balance     : " + registry.get("balance").textContent);
  console.log("  openPl      : " + registry.get("openPl").textContent);
  console.log("  riskPct     : " + registry.get("riskPct").textContent);
  console.log("  statusPill  : " + registry.get("statusPill").textContent);
  console.log("  legRows     : " + registry.get("legRows").children.length);
  console.log("  ladder rungs: " + registry.get("ladder").children.length);
  console.log("  latency     : " + registry.get("latencyLabel").textContent);
  console.log("  lastGood    : " + registry.get("lastGoodLabel").textContent);
  console.log("  eaVersion   : " + registry.get("eaVersion").textContent);
  console.log("  timers      : " + JSON.stringify(timers));
  console.log("");

  if (problems.length) {
    console.log("EXECUTION CHECK FAILED:");
    for (const problem of problems) console.log("  - " + problem);
    process.exit(1);
  }
  console.log("EXECUTION CHECK PASSED: the client polled the bridge and rendered it.");
}, 300);


// Charon dashboard client.
//
// Three rules this file follows without exception:
//
//   1. It never computes a price, a risk figure or an R multiple. Every
//      number comes from /api/status, which comes from the EA.
//   2. It never pretends to be current. If the snapshot is stale or the
//      bridge is down, the banner says so and the values go dim.
//   3. It never reaches for an element that does not exist. Every id used
//      below is in index.html, and test_client_script.py proves it.

const REFRESH_MS = 1000;

// --- a poll that has not answered in this long counts as a failure, so a
// --- hung socket cannot leave the panel looking live forever.
const FETCH_TIMEOUT_MS = 4000;

// --- below this the poll interval is honoured exactly; above it the badge
// --- turns amber and says so.
const SLOW_FETCH_MS = 750;

// --- how many rendered log lines to keep before trimming the DOM
const LOG_MAX_LINES = 300;

const $ = (id) => document.getElementById(id);

let eventSource = null;
let lastLogSeq = 0;        // highest seq rendered, so replays never duplicate

// --- The symbol whose snapshot is on screen. Every command that has to name
// --- a chart uses this, so the panel and the buttons can never disagree
// --- about which chart is being driven. It is the EXACT broker name.
let activeSymbol = null;


//+------------------------------------------------------------------+
//| Formatting helpers                                               |
//+------------------------------------------------------------------+
function money(value, currency) {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }) + (currency ? " " + currency : "");
}

function price(value, digits) {
  if (value === null || value === undefined) return "—";
  return Number(value).toFixed(digits ?? 5);
}

function rr(value) {
  if (value === null || value === undefined) return "—";
  return Number(value).toFixed(2) + "R";
}

function pct(value) {
  if (value === null || value === undefined) return "—";
  return Number(value).toFixed(2) + "%";
}

function ageText(seconds) {
  if (seconds === null || seconds === undefined) return "";
  if (seconds < 1.5) return "just now";
  if (seconds < 60) return Math.round(seconds) + "s ago";
  return Math.round(seconds / 60) + "m ago";
}

function clockText(at) {
  if (!at) return "—";
  return new Date(at * 1000).toLocaleTimeString();
}
//+------------------------------------------------------------------+
//| Ladder rendering                                                 |
//|                                                                  |
//| The EA sends a string like "1.50R 1.26950 | 2.00R 1.27200".      |
//| We split it for layout ONLY -- the rung text and the price are    |
//| displayed exactly as received. If the field is absent we say so   |
//| rather than inventing a level, because an invented rung in a      |
//| risk tool is a lie with consequences.                             |
//+------------------------------------------------------------------+
function ladderRungs(text) {
  if (text === null || text === undefined) {
    return [{ level: "not reported", price: null, missing: true }];
  }
  if (text === "") {
    return [{ level: "no rung configured", price: null, missing: true }];
  }
  return text.split("|").map((raw) => {
    const part = raw.trim();
    const match = part.match(/^(\S+)\s+(\S+)$/);
    if (!match) return { level: part, price: null, missing: false };
    return { level: match[1], price: match[2], missing: false };
  });
}

function renderLadder(container, text) {
  container.replaceChildren();
  for (const rung of ladderRungs(text)) {
    const row = document.createElement("div");
    row.className = "rung";

    const level = document.createElement("span");
    level.className = rung.missing
      ? "text-body-sm italic text-tertiary"
      : "font-display text-caps uppercase text-alt";
    level.textContent = rung.level;

    const px = document.createElement("span");
    px.className = rung.missing
      ? "font-mono text-body-sm text-tertiary"
      : "readout text-xs font-semibold";
    px.textContent = rung.price ?? "—";

    row.append(level, px);
    container.append(row);
  }
}

//+------------------------------------------------------------------+
//| Status pill / banner / dimming                                   |
//|                                                                  |
//| lastGood is written on every successful poll and read on every    |
//| failure, so the panel can say *when* it last told the truth        |
//| instead of only that it is currently lying.                       |
//+------------------------------------------------------------------+
// --- Apex is flat: a status pill is a 2px chip with a coloured border, not
// --- a glowing capsule. Every class below is a complete literal, because the
// --- Tailwind build scans this file as text.
const STATUS_STYLE = {
  live: "bg-live/15 text-live border-live/50",
  stale: "bg-stale/15 text-stale border-stale/50",
  offline: "bg-offline/15 text-offline border-offline/50",
  starting: "bg-accent/15 text-brand border-accent/50",
  error: "bg-offline/15 text-offline border-offline/50",
};

let lastGoodAt = 0;   // epoch seconds of the last good payload

// --- the panels dim together, so a stale screen cannot look live in the
// --- corner while the numbers still read as current. The selector is
// --- deliberately broad: `section` and `details` cover the panels of the
// --- current markup, and `.card` is kept so an older cache of index.html
// --- still dims instead of silently staying bright.
const DIMMABLE_SELECTOR =
  "main > header, main > section, main > details, main .panel, main .card";
let dimmed = false;

function setDimmed(on) {
  if (on === dimmed) return;
  dimmed = on;
  for (const node of document.querySelectorAll(DIMMABLE_SELECTOR)) {
    node.classList.toggle("opacity-60", on);
  }
}

function hideBanner() {
  const banner = $("banner");
  banner.textContent = "";
  banner.classList.add("hidden");
}

// --- every class is a complete literal: the Tailwind build scans this file
// --- as text, so a `"text-" + kind` concatenation would never be emitted.
const BANNER_STYLE = {
  stale:
    "sticky top-0 z-50 border-b border-stale/40 bg-stale/15 px-4 py-2 " +
    "text-center text-sm font-semibold tracking-wide text-stale backdrop-blur-sm",
  offline:
    "sticky top-0 z-50 border-b border-offline/40 bg-offline/15 px-4 py-2 " +
    "text-center text-sm font-semibold tracking-wide text-offline backdrop-blur-sm",
};

function showBanner(kind, message) {
  const banner = $("banner");
  banner.className = BANNER_STYLE[kind] || BANNER_STYLE.offline;
  banner.textContent = message;
  banner.classList.remove("hidden");
}

function setLastGood(at) {
  lastGoodAt = at;
  const label = $("lastGoodLabel");
  label.textContent = at ? "last good " + clockText(at) : "";
  label.title = at
    ? "The bridge last delivered a valid snapshot at this time."
    : "No valid snapshot since the page opened.";
}

function setStatus(status, ageSeconds, detail) {
  const pill = $("statusPill");
  pill.className = "pill " + (STATUS_STYLE[status] || STATUS_STYLE.error);
  pill.textContent = status;

  $("ageLabel").textContent = ageText(ageSeconds);

  const since = lastGoodAt
    ? " Last good snapshot " + clockText(lastGoodAt) + "."
    : " No valid snapshot since the page opened.";

  if (status === "stale") {
    showBanner("stale",
      "STALE DATA — the last EA snapshot is " + ageText(ageSeconds) +
      ". Do not act on these numbers." + since);
    setDimmed(true);
  } else if (status === "offline") {
    showBanner("offline",
      "EA OFFLINE — no snapshot for " + ageText(ageSeconds) +
      ". The chart is not being described by this view." + since);
    setDimmed(true);
  } else if (status === "error") {
    showBanner("offline", (detail || "BRIDGE ERROR — cannot reach /api/status.") + since);
    setDimmed(true);
  } else {
    hideBanner();
    setDimmed(false);
  }
}

//+------------------------------------------------------------------+
//| Latency badge                                                    |
//|                                                                  |
//| The poll is the panel's only source of truth, so its round trip   |
//| is shown rather than hidden. A bridge that answers in 1.8s is     |
//| telling you something about loopback that you want to know.       |
//+------------------------------------------------------------------+
function setLatency(ms) {
  const label = $("latencyLabel");
  const rounded = Math.round(ms);
  label.textContent = rounded + " ms";
  label.title = "Round trip for GET /api/status. Poll interval " + REFRESH_MS + " ms.";
  label.className =
    "font-mono text-body-sm " + (rounded > SLOW_FETCH_MS ? "text-stale" : "text-tertiary");
}

function setLatencyFailed() {
  const label = $("latencyLabel");
  label.textContent = "—";
  label.title = "The last GET /api/status did not complete.";
  label.className = "font-mono text-body-sm text-offline";
}

//+------------------------------------------------------------------+
//| Render one payload                                               |
//|                                                                  |
//| Nothing here is recomputed: every figure is copied from the       |
//| snapshot the EA published. The only arithmetic is presentation    |
//| (toFixed, toLocaleString).                                        |
//+------------------------------------------------------------------+
function render(payload) {
  const snap = payload.snapshot;
  const digits = snap.digits ?? 5;

  activeSymbol = snap.symbol;
  $("symbolLabel").textContent = snap.symbol;
  $("eaVersion").textContent = snap.ea_version ? "EA " + snap.ea_version : "";

  // --- the tick the EA published, kept as the one sanctioned source for the
  // --- quote beside the ticket. It belongs to snap.symbol only: the bridge
  // --- polls the chart it is reading, so no other instrument has a price here.
  quote = {
    symbol: snap.symbol || null,
    bid: typeof snap.bid === "number" ? snap.bid : null,
    ask: typeof snap.ask === "number" ? snap.ask : null,
    digits: digits,
  };
  paintOrderQuote();

  // --- the block and entry boxes accept a price, so their step has to match the
  // --- instrument's digits: a 3-digit JPY pair stepped at 0.00001 would reject
  // --- every value the operator types. Only the step changes -- no price is
  // --- ever written into these boxes.
  const step = digits > 0 ? "1e-" + digits : "0.00001";
  $("orderEntry").step = step;
  $("orderBlockTop").step = step;
  $("orderBlockBottom").step = step;
  $("orderTrailPrice").step = step;


  $("equity").textContent = money(snap.equity, snap.currency);
  $("balance").textContent = money(snap.balance, snap.currency);
  $("openPl").textContent = money(snap.basket.open_pl, snap.currency);
  $("riskPct").textContent = pct(snap.basket.risk_pct);
  $("riskMoney").textContent = money(snap.basket.risk_money, snap.currency);

  // --- a negative P/L reads red, positive green, flat neutral
  const pl = snap.basket.open_pl;
  const plTone = pl > 0 ? "text-live" : pl < 0 ? "text-offline" : "text-primary";
  $("openPl").className = "kpi-value " + plTone;

  // --- the floating result, in words, under the number: a bare figure
  // --- coloured green does not say whether it is booked or still at risk
  const floating = snap.basket.valid ? "floating on " + snap.basket.legs + " leg(s)" : "flat";
  $("openPlFoot").textContent = floating;
  $("equityFoot").textContent = snap.currency ? "in " + snap.currency : "—";
  $("openPl").title = "Unrealized. Realized results are in Closed trades below.";

  // --- the ticker strip repeats the four figures a desk watches, because
  // --- it stays on screen while the KPI grid scrolls away
  $("tickSymbol").textContent = snap.symbol || "—";
  $("tickBasket").textContent = snap.basket.valid
    ? snap.basket.direction + " " + Number(snap.basket.lot ?? 0).toFixed(2)
    : "flat";
  $("tickPl").className = "readout truncate text-num-md font-semibold " + plTone;
  $("tickPl").textContent = money(snap.basket.open_pl, snap.currency);
  $("tickEquity").textContent = money(snap.equity, snap.currency);
  $("tickRisk").textContent = pct(snap.basket.risk_pct);
  $("tickRisk").className =
    "readout truncate text-num-md " +
    (snap.basket.risk_pct > 0 ? "text-stale" : "text-primary");
  $("tickSession").textContent = snap.basket.valid ? "in play" : "idle";

  // --- basket
  const dir = $("basketDir");
  dir.textContent = snap.basket.valid ? snap.basket.direction : "flat";
  dir.className =
    "pill " +
    (snap.basket.direction === "BUY"
      ? "bg-live/15 text-live border-live/50"
      : snap.basket.direction === "SELL"
        ? "bg-offline/15 text-offline border-offline/50"
        : "bg-surface-high text-secondary");

  $("basketLegs").textContent = snap.basket.legs;
  $("basketAvg").textContent = price(snap.basket.avg_entry, digits);
  $("basketLot").textContent =
    snap.basket.lot === null ? "—" : Number(snap.basket.lot).toFixed(2);

  renderLadder($("ladder"), snap.basket.ladder);

  // --- working orders. Rendered before the pyramid block so a LIMIT ticket
  // --- is visible even when pyramiding is disabled.
  renderPending(snap.pending, digits, snap.currency);

  // --- armed entries. Separate from pending because they are separate
  // --- objects: no broker ticket, cancelled by watch id. The EA's last quote
  // --- is passed so each row can show its remaining distance.
  renderWatches(snap.watch, digits, snap.bid, snap.ask);

  // --- pyramid
  const pyr = snap.pyramid;
  const armState = $("armState");
  const countdown = $("armCountdown");

  if (pyr.armed) {
    armState.textContent = "ARMED";
    armState.className = "font-display text-headline-sm text-live";
    countdown.textContent =
      pyr.secs_left !== null && pyr.secs_left >= 0 ? pyr.secs_left + "s" : "∞";
    countdown.className = "readout text-num-lg font-semibold text-live";
  } else {
    armState.textContent = pyr.enabled ? "disarmed" : "pyramiding off";
    armState.className = "font-display text-headline-sm text-tertiary";
    countdown.textContent = "";
    countdown.className = "readout text-num-lg font-semibold text-secondary";
  }

  const canArm = pyr.enabled && pyr.trigger === "MANUAL" && snap.basket.valid;
  $("armBtn").disabled = !canArm;
  $("armBtn").title = canArm
    ? "Queue one arm for the EA. The EA still enforces every risk gate."
    : "Arming is unavailable in the current state.";
  $("armHint").textContent = !pyr.enabled
    ? "Pyramiding is disabled on this chart."
    : pyr.trigger !== "MANUAL"
      ? "Trigger is AUTO — arming has no effect."
      : !snap.basket.valid
        ? "No basket to scale into yet."
        : "Arming authorises one scale-in. Risk gates still apply.";

  // --- trail selector reflects the EA, but does not fight the user: while
  // --- the select has focus, a refresh must not yank the value back
  const select = $("trailSelect");
  if (document.activeElement !== select) {
    select.value = String(snap.trail.mode);
  }
  select.disabled = !snap.trail.mutable;
  $("trailMsg").textContent = snap.trail.mutable
    ? ""
    : "The EA reports this mode as not mutable on this chart.";

  renderLegs(snap.legs, digits);
  renderOrder(snap.order, digits, snap.currency);
  $("noLegs").classList.toggle("hidden", snap.legs.length > 0);
}


//+------------------------------------------------------------------+
//| Legs table                                                       |
//|                                                                  |
//| Rows are keyed by ticket and updated in place. Rebuilding the     |
//| table every second would throw away text selection and make the   |
//| numbers flicker while a position is being watched.                |
//+------------------------------------------------------------------+
function legRow(leg) {
  const tr = document.createElement("tr");
  tr.className = "transition-colors";
  tr.dataset.ticket = String(leg.ticket);

  // --- ticket, side, tier, entry, vol, stop, 1R, progress, flags
  for (let index = 0; index < 9; index += 1) {
    const td = document.createElement("td");
    td.className = index === 0 ? "pl-3" : "";
    td.dataset.cell = String(index);
    tr.append(td);
  }

  // --- the ladder cell keeps its own small stack of rungs
  const tdLadder = document.createElement("td");
  tdLadder.className = "";
  const box = document.createElement("div");
  box.className = "space-y-1";
  box.dataset.role = "leg-ladder";
  renderLadder(box, null);
  tdLadder.append(box);
  tr.append(tdLadder);

  // --- per-row actions. These act on THIS row's ticket, which is the whole
  // --- point: v1.3.0's buttons only ever addressed whatever the last verdict
  // --- happened to name.
  const tdActions = document.createElement("td");
  tdActions.className = "text-right";
  const actions = document.createElement("div");
  actions.className = "flex gap-1";
  actions.append(rowButton("modify", "Modify", "btn-ghost", leg.ticket));
  actions.append(rowButton("cancel", "Close", "btn-danger", leg.ticket));
  tdActions.append(actions);
  tr.append(tdActions);

  return tr;
}

// --- one small per-row button. Kept as a helper so every row is built the
// --- same way and the handler receives the ticket from the button itself
// --- rather than from whatever is currently on screen.
function rowButton(action, label, variant, ticket) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = variant + " px-2 py-0.5 font-mono text-num-sm";
  button.dataset.rowAction = action;
  button.dataset.ticket = String(ticket);
  button.textContent = label;
  return button;
}

function fillLegRow(tr, leg, digits) {
  const flags = [
    leg.trail_active ? "trail" : null,
    leg.be_done ? "BE" : null,
    leg.half_risk_done ? "½R" : null,
  ].filter(Boolean).join(" ") || "—";

  const cells = [
    { text: "#" + leg.ticket, cls: "text-secondary" },
    {
      text: leg.side,
      cls: leg.side === "BUY" ? "font-semibold text-live" : "font-semibold text-offline",
    },
    { text: "T" + leg.tier, cls: "text-alt" },
    { text: price(leg.entry, digits), cls: "" },
    { text: leg.lot === null ? "—" : Number(leg.lot).toFixed(2), cls: "" },
    // --- a zero stop is not a tight stop, it is an unprotected leg
    { text: price(leg.sl, digits), cls: leg.sl > 0 ? "" : "text-offline" },
    { text: price(leg.risk_price, digits), cls: "text-tertiary" },
    { text: rr(leg.progress_rr), cls: leg.progress_rr > 0 ? "text-live" : "text-secondary" },
    { text: flags, cls: "font-display text-caps uppercase text-tertiary" },
  ];

  const tdNodes = tr.querySelectorAll("td[data-cell]");
  for (let index = 0; index < cells.length; index += 1) {
    const td = tdNodes[index];
    if (!td) continue;
    // --- the first cell keeps the table's left gutter; without it the
    // --- ticket number sits flush against the panel edge
    td.className =
      (index === 0 ? "pl-3 " : "") + cells[index].cls;
    td.textContent = cells[index].text;
  }

  const box = tr.querySelector('div[data-role="leg-ladder"]');
  if (box) renderLadder(box, leg.ladder);
}

function renderLegs(legs, digits) {
  const tbody = $("legRows");
  const existing = new Map();
  for (const tr of tbody.children) {
    existing.set(tr.dataset.ticket, tr);
  }

  // --- walk the payload in the EA's order; append moves an existing node,
  // --- so the DOM ends up in exactly the EA's sequence
  for (const leg of legs) {
    const key = String(leg.ticket);
    let tr = existing.get(key);
    if (tr) {
      existing.delete(key);
    } else {
      tr = legRow(leg);
    }
    fillLegRow(tr, leg, digits);
    tbody.append(tr);
  }

  // --- anything still in the map refers to a closed position
  for (const tr of existing.values()) {
    tr.remove();
  }
}


//+------------------------------------------------------------------+
//| Working orders                                                   |
//|                                                                  |
//| A pending order is not a position, so it cannot live in the legs  |
//| table: it has no entry, no progress and no 1R until it fills. It  |
//| gets its own table because the one thing an operator needs from a |
//| working order is the ability to cancel it.                        |
//+------------------------------------------------------------------+
function pendingRow(order) {
  const tr = document.createElement("tr");
  tr.className = "transition-colors";
  tr.dataset.ticket = String(order.ticket);
  for (let index = 0; index < 7; index += 1) {
    const td = document.createElement("td");
    td.className = index === 0 ? "pl-3" : "";
    td.dataset.cell = String(index);
    tr.append(td);
  }
  return tr;
}

function fillPendingRow(tr, order, digits) {
  const cells = [
    { text: "#" + order.ticket, cls: "text-secondary" },
    {
      text: order.side || "\u2014",
      cls: order.side === "BUY" ? "font-semibold text-live" : "font-semibold text-offline",
    },
    { text: order.order_type || "\u2014", cls: "text-secondary" },
    { text: price(order.price, digits), cls: "" },
    // --- a pending order without a stop is a broker-side hazard, so it says
    // --- so rather than showing a tidy 0.00000
    { text: price(order.sl, digits), cls: order.sl > 0 ? "" : "text-offline" },
    { text: order.lot === null ? "\u2014" : Number(order.lot).toFixed(2), cls: "" },
  ];

  const tdNodes = tr.querySelectorAll("td[data-cell]");
  for (let index = 0; index < cells.length; index += 1) {
    const td = tdNodes[index];
    if (!td) continue;
    td.className =
      (index === 0 ? "pl-3 " : "") + cells[index].cls;
    td.textContent = cells[index].text;
  }

  const tdActions = tdNodes[6];
  if (tdActions) {
    tdActions.className = "text-right";
    // --- rebuilt on every paint rather than diffed: the pending table is
    // --- tiny and a stale handler would keep acting on a dead ticket.
    while (tdActions.firstChild) tdActions.removeChild(tdActions.firstChild);
    const actions = document.createElement("div");
    actions.className = "flex gap-1";
    actions.append(rowButton("cancel", "Cancel", "btn-danger", order.ticket));
    tdActions.append(actions);
  }
}

function renderPending(orders, digits, currency) {
  const rows = Array.isArray(orders) ? orders : [];
  const tbody = $("pendingRows");
  const existing = new Map();
  for (const tr of tbody.children) {
    existing.set(tr.dataset.ticket, tr);
  }

  for (const order of rows) {
    const key = String(order.ticket);
    let tr = existing.get(key);
    if (tr) {
      existing.delete(key);
    } else {
      tr = pendingRow(order);
    }
    fillPendingRow(tr, order, digits);
    tbody.append(tr);
  }
  for (const tr of existing.values()) {
    tr.remove();
  }

  $("pendingBadge").textContent = String(rows.length);
  $("noPending").classList.toggle("hidden", rows.length > 0);
  $("pendingTableWrap").classList.toggle("hidden", rows.length === 0);

  // --- volume is the one number worth summarising: it is what is committed
  // --- to the book even though nothing has filled yet.
  let volume = 0;
  for (const order of rows) {
    if (typeof order.lot === "number") volume += order.lot;
  }
  $("pendingSummary").textContent = rows.length
    ? rows.length + " working \u00b7 " + volume.toFixed(2) + " lots committed"
    : "nothing working";
}
//+------------------------------------------------------------------+
//| Armed watches                                                    |
//|                                                                  |
//| A watch has no broker ticket, so it is not a pending order and is |
//| cancelled by a different endpoint. Shown separately so the        |
//| operator never has to guess which number to paste where.          |
//+------------------------------------------------------------------+
function watchRow(item) {
  const tr = document.createElement("tr");
  tr.className = "transition-colors";
  tr.dataset.watch = String(item.watch_id);
  for (let index = 0; index < 6; index += 1) {
    const td = document.createElement("td");
    td.className = index === 0 ? "pl-3" : "";
    td.dataset.cell = String(index);
    tr.append(td);
  }
  return tr;
}

// --- how far the market still has to travel to reach the target. Returns
// --- null when the EA published no quote (a schema-2/3 EA), because inventing
// --- a distance would be worse than showing a dash.
function watchDistance(item, bid, ask) {
  const target = typeof item.price === "number" ? item.price : null;
  if (target === null) return null;
  const quoted = item.side === "BUY" ? ask : bid;
  if (typeof quoted !== "number" || !isFinite(quoted)) return null;
  return Math.abs(target - quoted);
}

function fillWatchRow(tr, item, digits, bid, ask) {
  const cells = [
    { text: "#" + item.watch_id, cls: "text-secondary" },
    {
      text: item.side || "\u2014",
      cls: item.side === "BUY" ? "font-semibold text-live" : "font-semibold text-offline",
    },
    { text: price(item.price, digits), cls: "" },
  ];

  const gap = watchDistance(item, bid, ask);
  cells.push({
    text: gap === null ? "\u2014" : price(gap, digits),
    // --- a target the market has already reached is about to fire; colouring
    // --- it warns the operator before the fill rather than after it
    cls: gap === null ? "text-tertiary" : (gap === 0 ? "text-live" : ""),
  });
  cells.push({ text: item.status || "armed", cls: "text-stale" });

  const tdNodes = tr.querySelectorAll("td[data-cell]");
  for (let index = 0; index < cells.length; index += 1) {
    const td = tdNodes[index];
    if (!td) continue;
    td.className =
      (index === 0 ? "pl-3 " : "") + cells[index].cls;
    td.textContent = cells[index].text;
  }

  const tdActions = tdNodes[5];
  if (tdActions) {
    tdActions.className = "text-right";
    while (tdActions.firstChild) tdActions.removeChild(tdActions.firstChild);
    const actions = document.createElement("div");
    actions.className = "flex gap-1";
    actions.append(rowButton("watch-cancel", "Cancel", "btn-danger", item.watch_id));
    tdActions.append(actions);
  }
}

function renderWatches(items, digits, bid, ask) {
  const rows = Array.isArray(items) ? items : [];
  const tbody = $("watchRows");
  if (!tbody) return;

  const existing = new Map();
  for (const tr of tbody.children) {
    existing.set(tr.dataset.watch, tr);
  }

  for (const item of rows) {
    const key = String(item.watch_id);
    let tr = existing.get(key);
    if (tr) {
      existing.delete(key);
    } else {
      tr = watchRow(item);
    }
    fillWatchRow(tr, item, digits, bid, ask);
    tbody.append(tr);
  }
  for (const tr of existing.values()) {
    tr.remove();
  }

  $("watchBadge").textContent = String(rows.length);
  $("noWatch").classList.toggle("hidden", rows.length > 0);
  $("watchTableWrap").classList.toggle("hidden", rows.length === 0);
  $("watchSummary").textContent = rows.length
    ? rows.length + (rows.length === 1 ? " entry waiting" : " entries waiting")
    : "nothing armed";
}





//|                                                                  |
//| A slow bridge is not an outage, but a hung one is: every poll has |
//| a deadline, and a failure of any kind is reported as an error     |
//| rather than silently leaving the last payload on screen looking   |
//| current.                                                          |
//+------------------------------------------------------------------+
async function fetchStatus() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  const started = performance.now();
  try {
    const response = await fetch(
      "/api/status" +
        (activeSymbol ? "?symbol=" + encodeURIComponent(activeSymbol) : ""),
      { cache: "no-store", signal: controller.signal }
    );

    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      const error = new Error(body.detail || "HTTP " + response.status);
      error.httpStatus = response.status;
      error.detail = body.detail || null;
      throw error;
    }
    return { payload: await response.json(), elapsed: performance.now() - started };
  } finally {
    clearTimeout(timer);
  }
}

let refreshing = false;

async function refresh() {
  // --- a slow poll must not stack up behind itself
  if (refreshing) return;
  refreshing = true;
  try {
    const { payload, elapsed } = await fetchStatus();
    render(payload);
    setStatus(payload.status, payload.age_seconds);
    setLastGood(Date.now() / 1000);
    setLatency(elapsed);
    lastPollError = null;
  } catch (error) {
    setLatencyFailed();
    // --- one line, not one per second: a bridge that stayed down for an
    // --- hour should not fill the log with 3600 identical complaints
    const detail =
      error.name === "AbortError"
        ? "GET /api/status timed out after " + FETCH_TIMEOUT_MS + " ms."
        : "Cannot reach the bridge: " + error.message;
    if (detail !== lastPollError) {
      lastPollError = detail;
      appendLocalLog("warn", detail);
    }
    setStatus("error", null, detail);
  } finally {
    refreshing = false;
  }
}

let lastPollError = null;
//+------------------------------------------------------------------+
//| New order panel                                                  |
//|                                                                  |
//| This is the only part of the page that sends an order ticket. It  |
//| sends INTENT -- side, block, risk, rung -- and never a volume, a  |
//| stop distance or an R multiple. Those are the EA's answers, and   |
//| the EA publishes them back in snap.order for us to render.        |
//|                                                                  |
//| Rule 1 still holds here: nothing below multiplies, subtracts or   |
//| otherwise derives a price. If a number is missing we print a dash.|
//+------------------------------------------------------------------+
const ORDER_TONE = {
  idle: "pill bg-surface-high text-secondary",
  pending: "pill bg-stale/15 text-stale",
  accepted: "pill bg-live/15 text-live border-live/50",
  rejected: "pill bg-offline/15 text-offline border-offline/50",
};

function renderOrder(order, digits, currency) {
  const status = (order && order.status) || "idle";
  const badge = $("orderStatus");
  badge.textContent = status;
  badge.className = ORDER_TONE[status] || ORDER_TONE.idle;

  // --- the EA's numbers, rendered verbatim. A rejected ticket still shows
  // --- what was refused, which is the whole point of publishing them.
  $("orderLots").textContent =
    order && order.lots !== null && order.lots !== undefined
      ? Number(order.lots).toFixed(2)
      : "\u2014";
  $("orderEntryPx").textContent = order ? price(order.entry, digits) : "\u2014";
  $("orderStop").textContent = order ? price(order.stop, digits) : "\u2014";
  $("orderRiskMoney").textContent =
    order && order.risk_money !== null && order.risk_money !== undefined
      ? money(order.risk_money, currency) +
        (order.risk_pct !== null && order.risk_pct !== undefined
          ? " (" + pct(order.risk_pct) + ")"
          : "")
      : "\u2014";
  $("orderTicket").textContent =
    order && order.ticket ? "#" + order.ticket : "\u2014";
  // --- the tag the EA copied into the broker comment. Not the note: the note
  // --- lives in the bridge's journal and is shown in the journal list.
  $("orderJournalTag").textContent =
    order && order.journal_tag ? order.journal_tag : "\u2014";

  const verdict = $("orderVerdict");
  if (!order || status === "idle") {
    verdict.className = "mt-1.5 font-mono text-body-sm text-tertiary";
    verdict.textContent = "Nothing submitted yet.";
    return;
  }

  if (status === "rejected") {
    verdict.className = "mt-1.5 font-mono text-body-sm text-offline";
    verdict.textContent = "EA refused: " + (order.reason || "no reason given");
    return;
  }

  // --- a watch is neither a fill nor a working order: nothing was submitted,
  // --- and it may fire many minutes from now. Amber is the palette's "waiting,
  // --- but not broken" colour; green here would claim a position exists.
  if (status === "watching") {
    verdict.className = "mt-1.5 font-mono text-body-sm text-stale";
    const watching = [];
    if (order.side) watching.push(order.side);
    if (typeof order.entry === "number" && order.entry > 0) {
      watching.push("target " + price(order.entry, digits));
    }
    watching.push(order.reason || "armed, waiting for the price");
    verdict.textContent = watching.join(" \u00b7 ");
    return;
  }

  verdict.className =
    status === "pending" ? "mt-1.5 font-mono text-body-sm text-stale" : "mt-1.5 font-mono text-body-sm text-live";
  const parts = [];
  if (order.side) parts.push(order.side);
  if (order.rung) parts.push("rung " + order.rung);
  parts.push(order.reason || (status === "pending" ? "working" : "filled"));
  verdict.textContent = parts.join(" \u00b7 ");
}

// --- read the ticket out of the form. Values are strings in the DOM, so
// --- anything empty becomes null and the server decides whether that is
// --- acceptable (a MARKET ticket may have no entry; a LIMIT may not).
function readTicket() {
  // --- prefer what the operator typed; fall back to the chart on screen.
  // --- The value is the exact broker name either way -- the display label
  // --- above it is never sent.
  const typed = $("orderSymbol").value.trim();
  const symbol = typed || activeSymbol;
  const entryRaw = $("orderEntry").value.trim();
  const type = $("orderType").value;
  const unit = $("orderRiskUnit").value;
  const riskValue = Number($("orderRisk").value);

  // --- risk is stated ONCE. The server refuses a ticket carrying both, so the
  // --- unused field is omitted rather than sent as zero: a zero would read as
  // --- a real budget and would be rejected as "not positive".
  const ticket = {
    symbol: symbol && symbol !== "\u2014" ? symbol : null,
    side: $("orderSide").value,
    order_type: type,
    entry: entryRaw === "" ? null : Number(entryRaw),
    block_top: Number($("orderBlockTop").value),
    block_bottom: Number($("orderBlockBottom").value),
    trail_trigger: Number($("orderTrail").value),
    rung: Number($("orderRung").value),
  };
  if (unit === "money") {
    ticket.risk_money = riskValue;
  } else {
    ticket.risk_pct = riskValue;
  }

  // --- optional, and only sent when actually filled in: an empty box means
  // --- "use the chart setting", which is what the EA does with an absent key.
  // --- An absolute activation price and an RR/points threshold are mutually
  // --- exclusive -- the server refuses both -- so stating a price here clears
  // --- the RR box rather than letting the operator send a contradiction.
  const trailStart = optionalNumber("orderTrailStart");
  const trailPrice = optionalNumber("orderTrailPrice");
  if (trailPrice !== null) {
    ticket.trail_start_price = trailPrice;
  } else if (trailStart !== null) {
    ticket.trail_start_rr = trailStart;
  }
  const ceiling = optionalNumber("orderBasketCeiling");
  if (ceiling !== null) {
    ticket.basket_risk_ceiling_pct = ceiling;
  }

  const stages = readStages();
  if (stages.length > 0) {
    ticket.stages = stages;
  }

  const journal = $("orderJournal").value.trim();
  if (journal !== "") {
    ticket.journal = journal;
  }
  return ticket;
}

// --- an empty input is "not stated", which is different from zero. Sending 0
// --- would ask the EA for a threshold it would then have to ignore.
function optionalNumber(id) {
  const raw = $(id).value.trim();
  if (raw === "") return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

// --- collect the pyramid stage rows that have a trigger filled in. A row with
// --- an empty trigger is "not used" rather than an error, so an operator can
// --- leave stage 3 blank instead of having to delete it.
function readStages() {
  const rows = $("stageRows").children;
  const stages = [];
  for (const row of rows) {
    const trigger = Number(row.querySelector("[data-stage='trigger']").value);
    const kind = row.querySelector("[data-stage='kind']").value;
    const multRaw = row.querySelector("[data-stage='mult']").value.trim();
    if (!Number.isFinite(trigger) || trigger <= 0) continue;
    stages.push({
      kind: kind,
      trigger: trigger,
      lots_mult: multRaw === "" ? 1 : Number(multRaw),
    });
  }
  return stages;
}

async function postOrder(path, label) {
  const message = $("orderMsg");
  message.className = "mt-1.5 font-mono text-body-sm text-tertiary";
  message.textContent = "sending\u2026";

  const ticket = readTicket();
  // --- a blank box is not zero: catch it here rather than sending NaN,
  // --- which JSON turns into null and the server rejects with a message
  // --- far less useful than this one.
  for (const key of ["block_top", "block_bottom"]) {
    if (!Number.isFinite(ticket[key]) || ticket[key] <= 0) {
      message.className = "mt-1.5 font-mono text-body-sm text-offline";
      message.textContent = key.replace("_", " ") + " must be a positive number.";
      return;
    }
  }
  // --- whichever unit is selected, exactly one risk figure is sent, and it
  // --- has to be positive before it leaves the browser.
  const riskKey = ticket.risk_money !== undefined ? "risk_money" : "risk_pct";
  if (!Number.isFinite(ticket[riskKey]) || ticket[riskKey] <= 0) {
    message.className = "mt-1.5 font-mono text-body-sm text-offline";
    message.textContent =
      riskKey.replace("_", " ") + " must be a positive number.";
    return;
  }
  if (ticket.order_type === "LIMIT" && !Number.isFinite(ticket.entry)) {
    message.className = "mt-1.5 font-mono text-body-sm text-offline";
    message.textContent = "A LIMIT ticket needs an entry price.";
    return;
  }

  try {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(ticket),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      message.className = "mt-1.5 font-mono text-body-sm text-offline";
      const detail = body.detail;
      message.textContent =
        typeof detail === "string" ? detail : "HTTP " + response.status;
      return;
    }
    // --- queued is not filled: the EA adjudicates on its next tick and the
    // --- verdict arrives in the snapshot, not in this response.
    message.className = "mt-1.5 font-mono text-body-sm text-brand";
    message.textContent =
      label + " queued \u2014 the EA adjudicates it on its next tick.";
    appendLocalLog("command", "order: " + label + " queued from the dashboard");
    refresh();
  } catch (error) {
    message.className = "mt-1.5 font-mono text-body-sm text-offline";
    message.textContent = "failed: " + error.message;
  }
}

// --- build the three pyramid stage rows once, at wiring time. Three is
// --- MAX_STAGES on the server; the HTML never hard-codes a fourth because the
// --- bridge would refuse it.
function buildStageRows() {
  const host = $("stageRows");
  for (let index = 1; index <= 3; index += 1) {
    const cell = document.createElement("div");
    cell.className = "rounded-sm border border-borderline bg-surface-container p-2";

    const label = document.createElement("div");
    label.className = "label";
    label.textContent = "Stage " + index;
    cell.append(label);

    const kind = document.createElement("select");
    kind.className = "ticket-field mt-1 font-mono text-body-sm";
    kind.dataset.stage = "kind";
    const rrOption = document.createElement("option");
    rrOption.value = "rr";
    rrOption.textContent = "trigger at R";
    const priceOption = document.createElement("option");
    priceOption.value = "price";
    priceOption.textContent = "trigger at price";
    kind.append(rrOption, priceOption);
    cell.append(kind);

    const trigger = document.createElement("input");
    trigger.type = "number";
    trigger.step = "0.00001";
    trigger.dataset.stage = "trigger";
    trigger.placeholder = "not used";
    trigger.className = "ticket-field mt-1 font-mono text-body-sm";
    cell.append(trigger);

    const mult = document.createElement("input");
    mult.type = "number";
    mult.step = "0.05";
    mult.min = "0";
    mult.dataset.stage = "mult";
    mult.placeholder = "size x1.0";
    mult.className = "ticket-field mt-1 font-mono text-body-sm";
    cell.append(mult);

    host.append(cell);
  }
}

// --- per-row Modify / Cancel. One delegated listener on the document, because
// --- the rows are rebuilt on every poll: attaching a handler per button would
// --- leak one per repaint. The ticket comes from the button, so a row can
// --- never act on a different position by mistake.
function wireRowActions() {
  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-row-action]");
    if (!button) return;
    event.preventDefault();

    const ticket = button.dataset.ticket;
    const action = button.dataset.rowAction;
    if (!ticket || !action) return;

    if (action === "watch-cancel") {
      // --- a watch is cancelled by the id the EA minted, not by a broker
      // --- ticket: nothing was ever submitted, so /api/order/cancel would
      // --- look for a ticket that does not exist.
      const ok = window.confirm(
        "Cancel watch #" + ticket + "? Nothing has been submitted to the broker."
      );
      if (!ok) return;
      await cancelWatch(ticket, $("orderMsg"));
      return;
    }

    if (action === "cancel") {
      // --- a close is irreversible and closes real money, so it asks first.
      const ok = window.confirm(
        "Cancel #" + ticket + "? This removes the pending order or closes the position."
      );
      if (!ok) return;
      await cancelTicket(ticket, $("orderMsg"));
      return;
    }

    if (action === "modify") {
      // --- Modify re-uses the form's block geometry against THIS ticket, so
      // --- the operator types the new block once and clicks the row it
      // --- belongs to. It does not silently resend the last verdict's ticket.
      await postOrder(
        "/api/order/modify?position=" + encodeURIComponent(ticket),
        "Modify #" + ticket
      );
    }
  });
}


// --- the order form itself. Every handler here edits a field or posts the
// --- whole ticket; none of them derives a price, a volume or an R multiple.
function wireOrderPanel() {
  $("orderType").addEventListener("change", (event) => {
    const isLimit = event.target.value === "LIMIT";
    // --- MARKET with an entry price is a *watch*, not an immediate fill: the
    // --- EA holds the ticket until its own tick reaches that price. So the box
    // --- stays enabled for both types; only its wording changes.
    $("orderEntry").disabled = false;
    $("orderEntry").placeholder = isLimit
      ? "price inside the block"
      : "optional: wait for this price";
  });
  $("orderEntry").disabled = false;
  $("orderEntry").placeholder = "optional: wait for this price";

  // --- the risk unit changes the label, not the field: the operator keeps one
  // --- box and the ticket carries exactly one figure.
  $("orderRiskUnit").addEventListener("change", (event) => {
    const money = event.target.value === "money";
    $("orderRiskLabel").textContent = money ? "Risk (account ccy)" : "Risk %";
    $("orderRisk").step = money ? "1" : "0.01";
    $("orderRisk").placeholder = money ? "amount" : "percent";
  });

  // --- the direction wording, because a short is not a mistake to be nudged
  // --- away from -- it is a choice this panel must support explicitly.
  $("orderSide").addEventListener("change", (event) => {
    $("orderSideHint").textContent = event.target.value === "BUY" ? "long" : "short";
  });

  // --- the symbol picker owns its own event surface: the trigger opens the
  // --- popover, the search box filters it, and Enter commits the exact broker
  // --- name into the hidden #orderSymbol. Nothing here touches the value, and
  // --- nothing else may write it, so what is sent cannot drift from the label.
  wireSymbolCombo();

  // --- the hint under the symbol field has no input of its own to listen to any
  // --- more: the hidden field is written only by chooseSymbol(), which repaints
  // --- the trigger, the hint and the quote together.

  // --- a stage's trigger means something different per kind, so the
  // --- placeholder says which. Nothing is converted: the operator states a
  // --- number and the EA interprets it against the kind.
  $("stageRows").addEventListener("change", (event) => {
    const target = event.target;
    if (target.dataset.stage !== "kind") return;
    const trigger = target.parentElement.querySelector("[data-stage='trigger']");
    if (trigger) {
      trigger.placeholder = target.value === "price" ? "price level" : "R multiple";
    }
  });

  $("orderPlaceBtn").addEventListener("click", () =>
    postOrder("/api/order", "Place"));

  $("orderModifyBtn").addEventListener("click", () => {
    const ticket = $("orderTicket").textContent;
    if (!ticket.startsWith("#")) {
      $("orderMsg").className = "mt-1.5 font-mono text-body-sm text-offline";
      $("orderMsg").textContent = "No ticket to modify yet.";
      return;
    }
    postOrder("/api/order/modify?position=" + encodeURIComponent(ticket.slice(1)),
              "Modify");
  });

  $("orderCancelBtn").addEventListener("click", async () => {
    const message = $("orderMsg");
    const ticket = $("orderTicket").textContent;
    if (!ticket.startsWith("#")) {
      message.className = "mt-1.5 font-mono text-body-sm text-offline";
      message.textContent = "No ticket to cancel yet.";
      return;
    }
    await cancelTicket(ticket.slice(1), message);
  });
}

// --- cancel one ticket, whichever table it lives in. Shared by the verdict
// --- buttons, the leg rows and the pending rows so the request, the wording
// --- and the refusal handling cannot drift apart.
async function cancelTicket(ticketNumber, message) {
  const symbol = activeSymbol;
  const path =
    "/api/order/cancel?position=" +
    encodeURIComponent(ticketNumber) +
    (symbol ? "&symbol=" + encodeURIComponent(symbol) : "");
  if (message) {
    message.className = "mt-1.5 font-mono text-body-sm text-tertiary";
    message.textContent = "sending\u2026";
  }
  try {
    const response = await fetch(path, { method: "POST" });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (message) {
        message.className = "mt-1.5 font-mono text-body-sm text-offline";
        message.textContent =
          typeof body.detail === "string" ? body.detail : "HTTP " + response.status;
      }
      return false;
    }
    if (message) {
      message.className = "mt-1.5 font-mono text-body-sm text-brand";
      message.textContent =
        "Cancel queued for #" + ticketNumber + " \u2014 the EA applies it on its next tick.";
    }
    appendLocalLog("command", "order: cancel queued for #" + ticketNumber);
    refresh();
    return true;
  } catch (error) {
    if (message) {
      message.className = "mt-1.5 font-mono text-body-sm text-offline";
      message.textContent = "failed: " + error.message;
    }
    return false;
  }
}

// --- cancel one armed watch. Its own request because the object is its own
// --- kind: a watch has no broker ticket, so sending it to /api/order/cancel
// --- would name something the EA cannot find.
async function cancelWatch(watchId, message) {
  const symbol = activeSymbol;
  const path =
    "/api/orders/watch/cancel?watch_id=" +
    encodeURIComponent(watchId) +
    (symbol ? "&symbol=" + encodeURIComponent(symbol) : "");
  if (message) {
    message.className = "mt-1.5 font-mono text-body-sm text-tertiary";
    message.textContent = "sending\u2026";
  }
  try {
    const response = await fetch(path, { method: "POST" });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (message) {
        message.className = "mt-1.5 font-mono text-body-sm text-offline";
        message.textContent =
          typeof body.detail === "string" ? body.detail : "HTTP " + response.status;
      }
      return false;
    }
    if (message) {
      message.className = "mt-1.5 font-mono text-body-sm text-brand";
      message.textContent =
        "Watch #" + watchId + " cancel queued \u2014 the EA drops it on its next tick.";
    }
    appendLocalLog("command", "order: watch #" + watchId + " cancel queued");
    refresh();
    return true;
  } catch (error) {
    if (message) {
      message.className = "mt-1.5 font-mono text-body-sm text-offline";
      message.textContent = "failed: " + error.message;
    }
    return false;
  }
}







//+------------------------------------------------------------------+
//| Commands (never orders)                                          |
//|                                                                  |
//| Queued is not applied. The EA drains its inbox on its own timer   |
//| and can still refuse the command -- so the message says "queued",  |
//| never "done", and the verdict shows up later in the log tail.     |
//+------------------------------------------------------------------+
async function postCommand(path, messageEl, label) {
  messageEl.className = "mt-1.5 font-mono text-body-sm text-tertiary";
  messageEl.textContent = "sending…";
  try {
    const response = await fetch(path, { method: "POST" });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      messageEl.className = "mt-1.5 font-mono text-body-sm text-offline";
      messageEl.textContent = body.detail || "HTTP " + response.status;
      return;
    }
    // --- queued is not applied: the EA drains its inbox on its own timer
    messageEl.className = "mt-1.5 font-mono text-body-sm text-brand";
    messageEl.textContent = label + " queued — the EA applies it on its next tick.";
  } catch (error) {
    messageEl.className = "mt-1.5 font-mono text-body-sm text-offline";
    messageEl.textContent = "failed: " + error.message;
  }
}

function wireCommands() {
  wireOrderPanel();
  wireRowActions();
  buildStageRows();

  $("armBtn").addEventListener("click", () =>
    postCommand("/api/pyramid/arm", $("armMsg"), "Arm"));


  $("disarmBtn").addEventListener("click", () =>
    postCommand("/api/pyramid/disarm", $("armMsg"), "Disarm"));

  $("trailSelect").addEventListener("change", (event) => {
    const mode = event.target.value;
    postCommand("/api/trail?mode=" + encodeURIComponent(mode), $("trailMsg"), "Trail mode");
  });

  // --- choosing a chart re-points the poll, and with it every panel. The
  // --- ticket's symbol follows only while none is chosen: once the operator has
  // --- named another instrument, this must not overwrite it. It goes through
  // --- chooseSymbol so the trigger and the quote are repainted with it.
  $("symbolSelect").addEventListener("change", (event) => {
    activeSymbol = event.target.value || null;
    if (!$("orderSymbol").value.trim()) {
      chooseSymbol(activeSymbol, false);
    }
    refresh();
  });
}

//+------------------------------------------------------------------+
//| Symbol combobox                                                  |
//|                                                                  |
//|  Two independent things are going on and conflating them is how   |
//|  orders reach the wrong chart:                                    |
//|                                                                   |
//|  * which chart's snapshot is on screen  -- #symbolSelect;          |
//|  * which symbol an order ticket names   -- #orderSymbol.           |
//|                                                                   |
//|  #orderSymbol is a hidden input holding the EXACT broker name. The |
//|  label painted in the trigger is never sent: it is a display form  |
//|  the bridge canonicalised, and a broker listing ``EURUSD.a`` will  |
//|  reject ``EURUSD``.                                               |
//|                                                                   |
//|  The picker does not restrict the choice. A broker can list an     |
//|  instrument the bridge cannot enumerate, and refusing a correct    |
//|  name would be worse than accepting it -- so whatever was typed is |
//|  offered verbatim as its own row and sent exactly as typed.        |
//+------------------------------------------------------------------+
// --- the classifier's headings, in the order the bridge states. A class the
// --- bridge did not name still renders under its raw key rather than being
// --- dropped: an instrument must never disappear from the picker because the
// --- client did not recognise a heading.
const ASSET_LABELS = {
  forex: "Forex",
  metals: "Metals",
  indices: "Indices",
  energy: "Energy",
  commodities: "Commodities",
  crypto: "Crypto",
  stocks: "Stocks",
  other: "Other",
};

// --- the chip beside each row. A lookup table, not a built string, because
// --- the Tailwind build scans this file as text: a computed class would emit
// --- nothing and the badge would render unstyled. Keys mirror ASSET_LABELS.
const ASSET_BADGE = {
  forex: "FX",
  metals: "MET",
  indices: "IDX",
  energy: "ENR",
  commodities: "CMD",
  crypto: "CRY",
  stocks: "STK",
  other: "OTH",
};

// --- how many catalogue rows to paint before asking the operator to narrow.
// --- A full MT5 install lists thousands; painting them all costs a visible
// --- stall on every keystroke, and a list nobody can scan is no more helpful
// --- than a filtered one.
const COMBO_MAX_ROWS = 50;

function assetLabel(key) {
  return ASSET_LABELS[key] || key || "Other";
}

function symbolBadge(key) {
  if (ASSET_BADGE[key]) return ASSET_BADGE[key];
  const raw = String(key || "other").replace(/[^A-Za-z]/g, "");
  return (raw.slice(0, 3) || "OTH").toUpperCase();
}

// --- ``symbolAll`` is everything the bridge reported, kept whole so every
// --- paint starts from the full list. ``symbolFiltered`` is the row model of
// --- the list as it stands (headings included), so the active index can
// --- address it directly without re-deriving the layout on every arrow press.
let symbolAll = [];
let symbolClasses = [];
let symbolFiltered = [];
let symbolActive = -1;
let symbolOpen = false;

// --- What the bridge last published as a quote, and for which symbol. Only the
// --- chart being polled publishes a tick, so this is the one honest source for
// --- a bid/ask beside the ticket.
let quote = { symbol: null, bid: null, ask: null, digits: 5 };

function symbolEntryFor(name) {
  if (!name) return null;
  const lower = String(name).toLowerCase();
  for (const entry of symbolAll) {
    if (String(entry.symbol || "").toLowerCase() === lower) return entry;
  }
  return null;
}

// --- the searchable text of an entry: the broker name, the display label and
// --- the class heading, so "eur", "eurusd.a" and "forex" all find it.
function symbolMatches(needle) {
  if (!needle) return symbolAll.slice();
  const lower = String(needle).toLowerCase();
  return symbolAll.filter((entry) => {
    const haystack =
      (entry.symbol || "") + " " + (entry.label || "") + " " + assetLabel(entry.asset_class);
    return haystack.toLowerCase().indexOf(lower) !== -1;
  });
}

// --- Build the row model for the current query.
// ---
// --- Rows are one flat list of headings and options rather than nested groups,
// --- because a flat list has an index the keyboard can walk without re-deriving
// --- the layout on every arrow press.
function symbolRows() {
  const matched = symbolMatches($("symbolSearch").value.trim());
  const order = symbolClasses.slice();
  for (const entry of matched) {
    const key = entry.asset_class || "other";
    if (order.indexOf(key) === -1) order.push(key);
  }

  const chosen = $("orderSymbol").value.trim().toLowerCase();
  const rows = [];
  let budget = COMBO_MAX_ROWS;
  let shown = 0;

  for (const key of order) {
    if (budget <= 0) break;
    const group = matched.filter((entry) => (entry.asset_class || "other") === key);
    if (group.length === 0) continue;
    rows.push({ kind: "group", heading: assetLabel(key) });
    for (const entry of group) {
      if (budget <= 0) break;
      rows.push({
        kind: "option",
        symbol: entry.symbol,
        label: entry.label || entry.symbol,
        badge: symbolBadge(key),
        selected: String(entry.symbol || "").toLowerCase() === chosen,
        chart: (entry.sources || []).indexOf("chart") !== -1,
      });
      budget -= 1;
      shown += 1;
    }
  }

  // --- the escape hatch. Offered whenever what was typed is not itself a known
  // --- broker name, and always appended LAST so the first row -- the one that
  // --- is active on open and after every keystroke -- stays a real instrument.
  // --- Enter therefore never sends free text by accident, but an operator who
  // --- genuinely means "GBP" can still reach it: the row is there, it is
  // --- labelled, and the hint under the field says it will go out verbatim.
  const typed = $("symbolSearch").value.trim();
  if (typed !== "") {
    const exact = matched.some(
      (entry) => String(entry.symbol || "").toLowerCase() === typed.toLowerCase()
    );
    if (!exact) {
      rows.push({ kind: "group", heading: "as typed \u2014 not in the bridge's list" });
      rows.push({
        kind: "option",
        symbol: typed,
        label: typed,
        badge: "RAW",
        selected: typed.toLowerCase() === chosen,
        chart: false,
      });
    }
  }

  return { rows, total: matched.length, shown };
}

// --- The footer's one job is to never let a filtered list look like the whole
// --- one. Three separate facts, stated separately: no catalogue at all, nothing
// --- matched, some matched but more were withheld by the row cap, or the list is
// --- narrowed by the query.
function comboFootText(built) {
  if (symbolAll.length === 0) {
    return "no instrument list from the bridge \u2014 type a name and press Enter";
  }
  if (built.total === 0) {
    return "no match in " + symbolAll.length + " instruments \u2014 Enter sends what you typed";
  }
  if (built.shown < built.total) {
    return built.shown + " of " + built.total + " matches \u2014 keep typing to narrow";
  }
  if (built.total < symbolAll.length) {
    return built.total + " of " + symbolAll.length + " instruments match";
  }
  return built.total + " of " + symbolAll.length + " known instruments";
}

// --- the row numbers that can be chosen; headings are skipped, so Up and Down
// --- never land on a label.
function comboOptionIndexes() {
  const found = [];
  for (let index = 0; index < symbolFiltered.length; index += 1) {
    if (symbolFiltered[index].kind === "option") found.push(index);
  }
  return found;
}

function symbolListNode(index) {
  for (const node of $("symbolList").children) {
    if (node.dataset && node.dataset.index === String(index)) return node;
  }
  return null;
}

// --- the active row is the one Enter will take. It is marked here and painted
// --- by CSS, so the keyboard cursor and a hover lift are the same affordance.
function setComboActive(index) {
  symbolActive = index;
  for (const node of $("symbolList").children) {
    if (node.dataset && node.dataset.index !== undefined) {
      node.dataset.active = node.dataset.index === String(index) ? "true" : "false";
    }
  }
  const search = $("symbolSearch");
  if (index < 0) {
    search.removeAttribute("aria-activedescendant");
    return;
  }
  search.setAttribute("aria-activedescendant", "symbolOption" + index);
  const node = symbolListNode(index);
  if (node && node.scrollIntoView) node.scrollIntoView({ block: "nearest" });
}

function paintComboRows() {
  const built = symbolRows();
  symbolFiltered = built.rows;

  const nodes = [];
  for (let index = 0; index < symbolFiltered.length; index += 1) {
    const row = symbolFiltered[index];
    if (row.kind === "group") {
      const heading = document.createElement("li");
      heading.className = "combo-group";
      heading.setAttribute("role", "presentation");
      heading.textContent = row.heading;
      nodes.push(heading);
      continue;
    }
    const option = document.createElement("li");
    option.id = "symbolOption" + index;
    option.className = "combo-option";
    option.setAttribute("role", "option");
    option.setAttribute("aria-selected", row.selected ? "true" : "false");
    option.dataset.index = String(index);
    option.dataset.symbol = row.symbol;
    option.dataset.active = "false";

    const name = document.createElement("span");
    name.className = "truncate";
    name.textContent = row.label;

    const badge = document.createElement("span");
    badge.className = "tag";
    badge.textContent = row.badge;

    option.append(name, badge);
    nodes.push(option);
  }
  $("symbolList").replaceChildren(...nodes);
  $("symbolListFoot").textContent = comboFootText(built);

  // --- the first option is active on open and after every keystroke, so Enter
  // --- always has an answer and the operator can see what it will be.
  const options = comboOptionIndexes();
  setComboActive(options.length ? options[0] : -1);
}

// --- the hint under the field names the exact broker symbol that will be sent
// --- and what the bridge thinks it is. It never rewrites the choice: whatever
// --- was selected or typed is authoritative.
function paintSymbolHint() {
  const hint = $("orderSymbolHint");
  if (!hint) return;
  const chosen = $("orderSymbol").value.trim();

  if (chosen === "") {
    hint.textContent = symbolAll.length
      ? symbolAll.length + " instruments known to the bridge"
      : "";
    return;
  }
  const entry = symbolEntryFor(chosen);
  if (!entry) {
    // --- not in the catalogue is not an error: the bridge may simply not be
    // --- able to enumerate this broker's list. Saying so is honest.
    hint.textContent = "sends \"" + chosen + "\" verbatim \u2014 not in the bridge's list";
    return;
  }
  const live = (entry.sources || []).indexOf("chart") !== -1;
  hint.textContent =
    "sends " +
    entry.symbol +
    " \u00b7 " +
    assetLabel(entry.asset_class) +
    (live
      ? " \u00b7 live chart, can be driven now"
      : " \u00b7 listed by the terminal, no chart yet");
}

function paintSymbolTrigger() {
  const chosen = $("orderSymbol").value.trim();
  const entry = symbolEntryFor(chosen);
  const badge = $("symbolTriggerBadge");

  if (chosen === "") {
    $("symbolTriggerLabel").textContent = "no symbol";
    badge.textContent = "\u2014";
    badge.classList.add("hidden");
    $("symbolTrigger").title = "";
    return;
  }
  // --- the display label when the bridge knows the name, the name itself when
  // --- it does not: inventing a label for an unknown instrument would imply
  // --- the bridge had recognised it.
  $("symbolTriggerLabel").textContent = entry && entry.label ? entry.label : chosen;
  badge.textContent = entry ? symbolBadge(entry.asset_class) : "RAW";
  badge.classList.remove("hidden");
  $("symbolTrigger").title = "sends " + chosen + " exactly";
}

// --- The quote beside the ticket, and the rule it follows: the bridge polls one
// --- chart, so it publishes one bid/ask. A ticket naming any other instrument
// --- has no quote here, and borrowing the neighbour's would be exactly the kind
// --- of invention this client refuses to do.
function paintOrderQuote() {
  const out = $("orderQuote");
  const spreadOut = $("orderQuoteSpread");
  if (!out || !spreadOut) return;

  const chosen = $("orderSymbol").value.trim();
  const digits = quote.digits ?? 5;

  if (chosen === "") {
    out.className = "truncate text-tertiary";
    out.textContent = "no symbol chosen";
    spreadOut.textContent = "";
    return;
  }

  const same =
    quote.symbol !== null && String(quote.symbol).toLowerCase() === chosen.toLowerCase();
  if (!same) {
    out.className = "truncate text-tertiary";
    out.textContent = quote.symbol
      ? "no quote for " + chosen + " \u2014 the bridge is reading " + quote.symbol
      : "no quote \u2014 the bridge published no tick";
    spreadOut.textContent = "";
    return;
  }

  if (quote.bid === null || quote.ask === null) {
    // --- a schema-2/3 EA never published a tick; say so rather than leave a
    // --- blank that reads as zero
    out.className = "truncate text-stale";
    out.textContent = "bid/ask not published by this EA";
    spreadOut.textContent = "";
    return;
  }

  out.className = "truncate text-secondary";
  out.textContent = "bid " + price(quote.bid, digits) + " \u00b7 ask " + price(quote.ask, digits);

  // --- the spread is a subtraction of two published numbers, nothing more. It
  // --- is stated in POINTS because that is what the digits field defines: a
  // --- "pip" is 10 points on a 5-digit pair, 1 point on a 3-digit JPY pair and
  // --- undefined on US30.cash. Calling it a pip would be wrong on two of three.
  const points = (quote.ask - quote.bid) * Math.pow(10, digits);
  if (!isFinite(points) || points < 0) {
    spreadOut.className = "shrink-0 text-stale";
    spreadOut.textContent = "spread n/a";
    return;
  }
  spreadOut.className = "shrink-0 text-tertiary";
  spreadOut.textContent =
    "spread " + (points < 10 ? points.toFixed(1) : points.toFixed(0)) + " points";
}

// --- commit a choice. ``name`` goes into the hidden field untouched; this is
// --- the only place #orderSymbol is written, so what is sent and what is shown
// --- cannot drift apart.
function chooseSymbol(name, refocusTrigger) {
  const exact = String(name === null || name === undefined ? "" : name).trim();
  $("orderSymbol").value = exact;
  paintSymbolTrigger();
  paintSymbolHint();
  paintOrderQuote();
  closeCombo(Boolean(refocusTrigger));
}

function openCombo() {
  if (symbolOpen) return;
  symbolOpen = true;
  $("symbolPopover").classList.remove("hidden");
  $("symbolTrigger").setAttribute("aria-expanded", "true");
  $("symbolSearch").setAttribute("aria-expanded", "true");
  // --- opening offers the whole list: a query left over from last time would
  // --- silently hide the instrument the operator came to pick.
  $("symbolSearch").value = "";
  paintComboRows();
  $("symbolSearch").focus();
}

function closeCombo(refocusTrigger) {
  if (!symbolOpen) return;
  symbolOpen = false;
  $("symbolPopover").classList.add("hidden");
  $("symbolTrigger").setAttribute("aria-expanded", "false");
  $("symbolSearch").setAttribute("aria-expanded", "false");
  $("symbolSearch").removeAttribute("aria-activedescendant");
  symbolActive = -1;
  if (refocusTrigger) $("symbolTrigger").focus();
}

// --- walk the choosable rows, wrapping at both ends. Wrapping rather than
// --- stopping matters: the list is capped at 50 rows, so the operator should be
// --- able to reach the last one from the first without counting.
function moveComboActive(step) {
  const options = comboOptionIndexes();
  if (options.length === 0) return;
  if (step === "first") {
    setComboActive(options[0]);
    return;
  }
  if (step === "last") {
    setComboActive(options[options.length - 1]);
    return;
  }
  const at = options.indexOf(symbolActive);
  if (at === -1) {
    setComboActive(step > 0 ? options[0] : options[options.length - 1]);
    return;
  }
  setComboActive(options[(at + step + options.length) % options.length]);
}

function activateComboRow() {
  if (symbolActive >= 0 && symbolActive < symbolFiltered.length) {
    const row = symbolFiltered[symbolActive];
    if (row && row.kind === "option") {
      chooseSymbol(row.symbol, true);
      return;
    }
  }
  // --- nothing highlighted: treat Enter as "send exactly what I typed".
  const typed = $("symbolSearch").value.trim();
  if (typed !== "") chooseSymbol(typed, true);
}

// --- the multi-chart chooser in the header. Separate from the ticket's
// --- combobox on purpose: this picks which chart's snapshot is on screen, the
// --- ticket picks which chart an order names, and the two must stay
// --- independent so a trader can queue for a chart without losing the readout
// --- they are watching.
// --- the popover's own event surface. Kept together so the open/close state
// --- machine has exactly one owner, and so nothing else can toggle the hidden
// --- class behind its back.
function wireSymbolCombo() {
  $("symbolTrigger").addEventListener("click", () => {
    if (symbolOpen) {
      closeCombo(true);
      return;
    }
    openCombo();
  });

  // --- a button already opens on Enter and Space; only the arrows need adding,
  // --- and they are what a combobox is expected to answer.
  $("symbolTrigger").addEventListener("keydown", (event) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    openCombo();
  });

  $("symbolSearch").addEventListener("input", paintComboRows);

  $("symbolSearch").addEventListener("keydown", (event) => {
    const key = event.key;
    if (key === "ArrowDown") {
      event.preventDefault();
      moveComboActive(1);
    } else if (key === "ArrowUp") {
      event.preventDefault();
      moveComboActive(-1);
    } else if (key === "Home") {
      event.preventDefault();
      moveComboActive("first");
    } else if (key === "End") {
      event.preventDefault();
      moveComboActive("last");
    } else if (key === "Enter") {
      event.preventDefault();
      activateComboRow();
    } else if (key === "Escape") {
      event.preventDefault();
      closeCombo(true);
    } else if (key === "Tab") {
      // --- Tab means "I am leaving", so the popover must not stay open behind
      // --- the cursor the operator has just moved elsewhere.
      closeCombo(false);
    }
  });

  // --- one listener on the list, not one per row: the rows are rebuilt on every
  // --- keystroke and per-row handlers would leak on each repaint.
  $("symbolList").addEventListener("click", (event) => {
    const option = event.target.closest ? event.target.closest("[data-symbol]") : null;
    if (!option) return;
    event.preventDefault();
    chooseSymbol(option.dataset.symbol, true);
  });

  // --- clicking elsewhere dismisses, which is what a popup owes the page behind
  // --- it. The test is on the whole combo rather than the popover so the click
  // --- that lands on the trigger does not immediately re-close what it opened.
  document.addEventListener("click", (event) => {
    if (!symbolOpen) return;
    const combo = $("symbolCombo");
    const target = event.target;
    if (combo && combo.contains && target && combo.contains(target)) return;
    closeCombo(false);
  });
}

// --- the multi-chart chooser in the header. Separate from the ticket's
// --- combobox on purpose: this picks which chart's snapshot is on screen, the
// --- ticket picks which chart an order names, and the two must stay
// --- independent so a trader can queue for another chart without losing the
// --- readout they are watching.
function paintSymbolPicker(catalogue) {
  const charts = catalogue.filter(
    (entry) => (entry.sources || []).indexOf("chart") !== -1
  );
  const picker = $("symbolPicker");

  if (charts.length < 2) {
    picker.className = "hidden";        // one chart needs no chooser
    return;
  }
  picker.className = "ml-1 flex flex-col gap-1 border-l border-borderline pl-3";

  const select = $("symbolSelect");
  const options = charts.map((entry) => {
    const option = document.createElement("option");
    option.value = entry.symbol;
    option.textContent = entry.label || entry.symbol;
    return option;
  });
  select.replaceChildren(...options);
  // --- keep the choice across repaints: a 1 s refresh that reset the
  // --- dropdown would make a multi-chart terminal unusable
  select.value = activeSymbol || charts[0].symbol;
}

async function loadSymbols() {
  try {
    const response = await fetch("/api/symbols", { cache: "no-store" });
    if (!response.ok) return;
    const body = await response.json();
    symbolAll = Array.isArray(body.symbols) ? body.symbols : [];
    // --- the grouping order is the bridge's, so the client never keeps a
    // --- second copy of it in step with charon_bridge/symbols.py
    symbolClasses = Array.isArray(body.asset_classes) ? body.asset_classes : [];
    paintSymbolPicker(symbolAll);
    paintSymbolTrigger();
    paintSymbolHint();
    // --- columns can appear and disappear between polls, so a selection made
    // --- from a stale list must still show its class once the list catches up
    if (symbolOpen) paintComboRows();
  } catch {
    /* the picker is a convenience; the status poll is what matters */
  }
}

//+------------------------------------------------------------------+
//| Unmanaged positions                                              |
//|                                                                  |
//|  The rule this block exists to enforce: never render an unknown or |
//|  unavailable answer as "all clear". A green badge on a check that  |
//|  did not happen is worse than no badge at all.                     |
//+------------------------------------------------------------------+
const UNMANAGED_TONE = {
  ok: "pill bg-live/15 text-live",
  unknown: "pill bg-stale/15 text-stale",
  unavailable: "pill bg-surface-high text-secondary",
};

function unmanagedRowNodes(rows) {
  return rows.map((row) => {
    const tr = document.createElement("tr");
    const cells = [
      "#" + row.ticket,
      row.symbol,
      row.side,
      Number(row.volume).toFixed(2),
      Number(row.profit).toFixed(2),
      row.verdict === "no_charon_on_symbol"
        ? "no Charon chart on this symbol"
        : "absent from the chart's legs",
    ];
    for (const text of cells) {
      const cell = document.createElement("td");
      cell.className = "py-1 pr-3";
      cell.textContent = text;
      tr.append(cell);
    }
    return tr;
  });
}

function renderUnmanaged(body) {
  const summary = body.summary || {};
  const state = summary.state || "unavailable";
  const rows = body.positions || [];
  const pending = body.pending || [];

  $("unmanagedPanel").className = "panel mt-4";
  const badge = $("unmanagedBadge");
  badge.className = UNMANAGED_TONE[state] || UNMANAGED_TONE.unavailable;

  if (rows.length > 0) {
    badge.textContent = rows.length === 1 ? "1 unmanaged" : rows.length + " unmanaged";
  } else if (state === "ok") {
    badge.textContent = "all clear";
  } else if (state === "unknown") {
    badge.textContent = "unknown";
  } else {
    badge.textContent = "unavailable";
  }

  $("unmanagedRows").replaceChildren(...unmanagedRowNodes(rows));
  $("unmanagedTableWrap").className = rows.length
    ? "overflow-x-auto px-4 pb-3"
    : "hidden overflow-x-auto px-4 pb-3";

  const detail = $("unmanagedBody");
  if (rows.length) {
    detail.className = "px-3 py-2 font-mono text-body-md text-tertiary";
    detail.textContent = pending.length
      ? pending.length + " further position(s) not yet confirmed."
      : "Open, but no live Charon chart accounts for them.";
  } else if (state === "ok") {
    detail.className = "px-3 py-2 font-mono text-body-md text-tertiary";
    detail.textContent = "Checked every live chart: nothing unmanaged.";
  } else if (state === "unknown") {
    // --- the sentence that must never become "all clear"
    detail.className = "px-3 py-2 font-mono text-body-md text-stale";
    detail.textContent =
      "A chart is stale or still starting, so its positions cannot be " +
      "checked. This is not an all-clear.";
  } else {
    detail.className = "px-3 py-2 font-mono text-body-md text-tertiary";
    detail.textContent =
      body.reason || "No Charon chart is publishing, so nothing can be reconciled.";
  }

  $("unmanagedSummary").textContent =
    pending.length ? pending.length + " pending" : "";
}

async function loadUnmanaged() {
  try {
    const response = await fetch("/api/unmanaged", { cache: "no-store" });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      // --- an HTTP error is not a clean account
      renderUnmanaged({
        summary: { state: "unavailable" },
        reason:
          typeof body.detail === "string" ? body.detail : "HTTP " + response.status,
      });
      return;
    }
    renderUnmanaged(body);
  } catch (error) {
    renderUnmanaged({
      summary: { state: "unavailable" },
      reason: "cannot reach the bridge: " + error.message,
    });
  }
}

//+------------------------------------------------------------------+
//| Closed trades (history)                                          |
//|                                                                  |
//| Everything here is derived server-side. The client formats and    |
//| never recomputes: a win rate calculated twice would eventually be |
//| calculated two different ways.                                    |
//|                                                                  |
//| A null ratio renders as "--" with the reason in the footnote.     |
//| Printing 0.00% would claim every trade lost, which is a different |
//| statement from "no losing trade has closed yet".                  |
//+------------------------------------------------------------------+
const NS_SVG = "http://www.w3.org/2000/svg";

function ratio(value, suffix, digits) {
  if (value === null || value === undefined) return "—";
  return Number(value).toFixed(digits ?? 2) + (suffix || "");
}

function durationText(seconds) {
  if (seconds === null || seconds === undefined || seconds <= 0) return "—";
  if (seconds < 3600) return Math.round(seconds / 60) + "m";
  if (seconds < 86400) return (seconds / 3600).toFixed(1) + "h";
  return (seconds / 86400).toFixed(1) + "d";
}

function signedMoney(value, currency) {
  if (value === null || value === undefined) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return sign + money(Math.abs(value), currency);
}

// --- the tone a realized figure earns. Colour is a claim: green means this
// --- trade made money, red means it lost, neutral means it was flat.
function outcomeTone(value) {
  if (value > 0) return "text-live";
  if (value < 0) return "text-offline";
  return "text-tertiary";
}

function drawEquityCurve(points) {
  const svg = $("equityCurve");
  svg.replaceChildren();
  if (!points || points.length < 2) return;

  const values = points.map((p) => p.cumulative);
  const low = Math.min(0, ...values);
  const high = Math.max(0, ...values);
  const span = high - low || 1;
  const width = 600;
  const height = 120;
  const y = (value) => height - ((value - low) / span) * height;
  const step = width / (points.length - 1);

  // --- the zero line: without it a curve that never went negative looks
  // --- identical to one that never went positive
  const zero = document.createElementNS(NS_SVG, "line");
  zero.setAttribute("x1", "0");
  zero.setAttribute("x2", String(width));
  zero.setAttribute("y1", String(y(0)));
  zero.setAttribute("y2", String(y(0)));
  zero.setAttribute("stroke", "#424754");
  zero.setAttribute("stroke-width", "1");
  zero.setAttribute("stroke-dasharray", "3 3");
  svg.append(zero);

  const path = document.createElementNS(NS_SVG, "polyline");
  path.setAttribute(
    "points",
    points
      .map((p, i) => (i * step).toFixed(1) + "," + y(p.cumulative).toFixed(1))
      .join(" ")
  );
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", values[values.length - 1] >= 0 ? "#4edea3" : "#ffb4ab");
  path.setAttribute("stroke-width", "1.5");
  path.setAttribute("stroke-linejoin", "round");
  svg.append(path);
}

function sessionRowNode(row) {
  const tr = document.createElement("tr");
  const cells = [
    { text: row.session, cls: "font-display text-caps uppercase text-secondary" },
    { text: String(row.total), cls: "" },
    { text: ratio(row.win_rate === null ? null : row.win_rate * 100, "%", 0), cls: "" },
    { text: signedMoney(row.net_realized, ""), cls: outcomeTone(row.net_realized) },
  ];
  for (const cell of cells) {
    const td = document.createElement("td");
    td.className = cell.cls;
    td.textContent = cell.text;
    tr.append(td);
  }
  return tr;
}

function historyRowNode(trade, currency, digits) {
  const tr = document.createElement("tr");
  const cells = [
    { text: clockText(trade.closed_at), cls: "text-tertiary" },
    { text: trade.symbol, cls: "text-secondary" },
    { text: trade.side, cls: trade.side === "BUY" ? "text-live" : "text-offline" },
    { text: Number(trade.volume).toFixed(2), cls: "" },
    { text: price(trade.entry_price, digits), cls: "" },
    { text: price(trade.exit_price, digits), cls: "" },
    { text: trade.session, cls: "text-alt" },
    { text: durationText(trade.duration_seconds), cls: "text-tertiary" },
    { text: signedMoney(trade.realized, currency), cls: outcomeTone(trade.realized) },
    { text: trade.comment || "—", cls: "text-tertiary" },
  ];
  for (const cell of cells) {
    const td = document.createElement("td");
    td.className = cell.cls;
    td.textContent = cell.text;
    tr.append(td);
  }
  return tr;
}

function renderHistory(body) {
  const badge = $("historyBadge");
  const metrics = body.metrics || {};

  if (body.available !== true) {
    // --- "cannot read the account" is not "no losses". The badge says so and
    // --- the reason is printed, because a silently empty table would be read
    // --- as a clean record.
    badge.textContent = "n/a";
    badge.className = "pill bg-stale/15 text-stale border-stale/50";
    $("historyEmpty").className = "px-3 py-2 font-mono text-body-md text-stale";
    $("historyEmpty").textContent =
      body.reason || "Deal history could not be read from the terminal.";
    $("historyTableWrap").className = "hidden overflow-x-auto";
    $("closedPl").textContent = "—";
    $("closedPl").className = "kpi-value text-tertiary";
    $("closedFoot").textContent = "history unavailable";
    return;
  }

  badge.textContent = metrics.total ? metrics.total + " closed" : "empty";
  badge.className = metrics.total
    ? "pill bg-live/15 text-live border-live/50"
    : "pill bg-surface-high text-secondary";

  // --- the account strip's realized counterpart to the floating P/L above
  // --- it. Two numbers, two different questions.
  $("closedPl").textContent = signedMoney(metrics.net_realized, "");
  $("closedPl").className = "kpi-value " + outcomeTone(metrics.net_realized);
  $("closedFoot").textContent = metrics.total + " closed in " + body.days + "d";

  $("histTrades").textContent = metrics.total ?? "—";
  $("histTradesFoot").textContent =
    (metrics.wins ?? 0) + "W / " + (metrics.losses ?? 0) + "L / " +
    (metrics.flat ?? 0) + "F";

  const rate = metrics.win_rate === null || metrics.win_rate === undefined
    ? null
    : metrics.win_rate * 100;
  $("histWinRate").textContent = ratio(rate, "%", 1);
  $("histWinRateFoot").textContent = rate === null
    ? "no closed trades"
    : "of " + metrics.total;

  $("histProfitFactor").textContent = ratio(metrics.profit_factor, "", 2);
  $("histProfitFactorFoot").textContent =
    metrics.profit_factor === null ? "no losing trade yet" : "gross win / loss";

  $("histExpectancy").textContent = signedMoney(metrics.expectancy, "");
  $("histExpectancy").className = "kpi-value " + outcomeTone(metrics.expectancy);
  $("histExpectancyFoot").textContent = metrics.avg_hold_seconds
    ? "avg hold " + durationText(metrics.avg_hold_seconds)
    : "per trade";

  $("histAvgRr").textContent = ratio(metrics.avg_rr, "R", 2);
  $("histAvgRrFoot").textContent =
    metrics.avg_rr === null ? "needs a loss to compare" : "realized win / loss";

  // --- the clock the session buckets were cut on, stated not implied
  const offset = body.tz_offset_minutes || 0;
  const sign = offset >= 0 ? "+" : "-";
  const abs = Math.abs(offset);
  $("histClock").textContent =
    "server UTC" + sign + Math.floor(abs / 60) + ":" +
    String(abs % 60).padStart(2, "0") +
    (body.tz_source === "snapshot" ? "" : " (assumed)");
  $("histClock").title = body.tz_source === "snapshot"
    ? "Inferred from the EA's server_time against the bridge clock."
    : "No live snapshot, so UTC was assumed. Session buckets may be shifted.";

  $("histEquityFoot").textContent = metrics.total
    ? signedMoney(metrics.net_realized, "") + " cumulative"
    : "no closed trades";

  $("sessionRows").replaceChildren(...(body.sessions || []).map(sessionRowNode));

  const digits = body.digits ?? 5;
  const trades = body.trades || [];
  $("historyRows").replaceChildren(
    ...trades.map((trade) => historyRowNode(trade, body.currency || "", digits))
  );
  $("historyTableWrap").className = trades.length
    ? "overflow-x-auto"
    : "hidden overflow-x-auto";
  $("historyEmpty").className = "px-3 py-2 font-mono text-body-md text-tertiary";
  $("historyEmpty").textContent = trades.length
    ? ""
    : metrics.total
      ? "Showing the newest " + trades.length + " of " + metrics.total + "."
      : "No closed trades in this window.";

  drawEquityCurve(body.equity);
}

async function loadHistory() {
  const days = $("historyDays").value || "30";
  try {
    const response = await fetch(
      "/api/history?days=" + encodeURIComponent(days) +
        (activeSymbol ? "&symbol=" + encodeURIComponent(activeSymbol) : ""),
      { cache: "no-store" }
    );
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      renderHistory({
        available: false,
        reason: typeof body.detail === "string" ? body.detail : "HTTP " + response.status,
      });
      return;
    }
    renderHistory(body);
  } catch (error) {
    renderHistory({ available: false, reason: "cannot reach the bridge: " + error.message });
  }
}

//+------------------------------------------------------------------+
//| Journal                                                          |
//|                                                                  |
//| A write here creates a local sidecar entry. It is not an order and |
//| cannot become one: /api/journal only touches the journal file.     |
//+------------------------------------------------------------------+
function journalRowNode(row) {
  const tr = document.createElement("tr");

  const ticket = document.createElement("td");
  ticket.textContent = "#" + row.ticket;

  const note = document.createElement("td");
  note.className = "max-w-[28ch] truncate text-secondary";
  note.textContent = row.note || "—";
  note.title = row.note || "";

  const psych = document.createElement("td");
  psych.textContent = row.psych || "—";

  const tags = document.createElement("td");
  if (Array.isArray(row.tags) && row.tags.length) {
    tags.replaceChildren(
      ...row.tags.map((label) => {
        const chip = document.createElement("span");
        chip.className = "tag mr-1";
        chip.textContent = label;
        return chip;
      })
    );
  } else {
    tags.textContent = "—";
  }

  const tag = document.createElement("td");
  tag.className = "text-alt";
  tag.textContent = row.tag || "—";

  const actions = document.createElement("td");
  const remove = document.createElement("button");
  remove.className = "btn-danger h-6 px-2";
  remove.textContent = "Delete";
  remove.dataset.journalTicket = String(row.ticket);
  actions.append(remove);

  tr.append(ticket, note, psych, tags, tag, actions);
  return tr;
}

function renderJournal(rows) {
  const list = Array.isArray(rows) ? rows : [];
  $("journalBadge").textContent = String(list.length);
  $("journalRows").replaceChildren(...list.map(journalRowNode));
  $("journalTableWrap").className = list.length
    ? "overflow-x-auto"
    : "hidden overflow-x-auto";
  $("journalEmpty").classList.toggle("hidden", list.length > 0);
}

async function loadJournal() {
  try {
    const response = await fetch(
      "/api/status" + (activeSymbol ? "?symbol=" + encodeURIComponent(activeSymbol) : ""),
      { cache: "no-store" }
    );
    if (!response.ok) return;
    const body = await response.json();
    renderJournal(body.journal);
  } catch {
    /* the status poll already reports a dead bridge; do not double-report */
  }
}

async function saveJournal() {
  const message = $("journalMsg");
  const ticket = Number($("journalTicket").value);
  if (!Number.isFinite(ticket) || ticket <= 0) {
    // --- ticket 0 queues an order; it is not a place to file a reflection
    message.className = "font-mono text-body-sm text-offline";
    message.textContent = "A note needs a real ticket number.";
    return;
  }

  const tags = $("journalTags").value
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean);

  message.className = "font-mono text-body-sm text-tertiary";
  message.textContent = "saving…";
  try {
    const response = await fetch(
      "/api/journal" + (activeSymbol ? "?symbol=" + encodeURIComponent(activeSymbol) : ""),
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ticket: ticket,
          note: $("journalNote").value,
          psych: $("journalPsych").value,
          tags: tags,
        }),
      }
    );
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || "HTTP " + response.status);

    message.className = "font-mono text-body-sm text-live";
    message.textContent = "saved #" + ticket;
    await loadJournal();
  } catch (error) {
    message.className = "font-mono text-body-sm text-offline";
    message.textContent = "not saved: " + error.message;
  }
}

async function deleteJournal(ticket) {
  const message = $("journalMsg");
  try {
    const response = await fetch(
      "/api/journal?ticket=" + encodeURIComponent(ticket) +
        (activeSymbol ? "&symbol=" + encodeURIComponent(activeSymbol) : ""),
      { method: "DELETE" }
    );
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || "HTTP " + response.status);

    message.className = "font-mono text-body-sm text-live";
    message.textContent = "deleted #" + ticket;
    await loadJournal();
  } catch (error) {
    message.className = "font-mono text-body-sm text-offline";
    message.textContent = "not deleted: " + error.message;
  }
}

function clearJournalFields() {
  $("journalTicket").value = "";
  $("journalNote").value = "";
  $("journalPsych").value = "";
  $("journalTags").value = "";
  $("journalMsg").textContent = "";
}

// --- delegated, because the rows are rebuilt on every poll: a per-row
// --- listener would be re-attached a few hundred times a session
function wireJournal() {
  $("journalSaveBtn").addEventListener("click", saveJournal);
  $("journalClearBtn").addEventListener("click", clearJournalFields);
  $("historyDays").addEventListener("change", loadHistory);
  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!target || !target.dataset) return;
    const ticket = target.dataset.journalTicket;
    if (ticket) deleteJournal(ticket);
  });
}

//+------------------------------------------------------------------+
//| Log stream                                                       |
//|                                                                  |
//| The server assigns each entry a monotonic seq and honours        |
//| Last-Event-ID, but a reconnect can still replay the tail -- so    |
//| the client de-duplicates on seq as well. Belt and braces, because |
//| a log that repeats itself on every hiccup is worse than no log.   |
//+------------------------------------------------------------------+
const LOG_COLOUR = {
  info: "text-secondary",
  warn: "text-stale",
  error: "text-offline",
  command: "text-brand",
};

function appendLogLine(at, level, message) {
  const box = $("logBox");
  const line = document.createElement("div");
  line.className = "log-line " + (LOG_COLOUR[level] || "text-secondary");

  const stamp = document.createElement("span");
  stamp.className = "text-tertiary";
  stamp.textContent = clockText(at) + "  ";

  line.append(stamp, document.createTextNode(message));
  box.append(line);

  // --- keep the DOM bounded; the ring buffer is capped server-side too
  while (box.childElementCount > LOG_MAX_LINES) {
    box.removeChild(box.firstChild);
  }
  box.scrollTop = box.scrollHeight;
}

// --- client-side notices share the same pane, timestamped the same way
function appendLocalLog(level, message) {
  appendLogLine(Date.now() / 1000, level, message);
}

function appendLog(entry) {
  if (typeof entry.seq === "number") {
    if (entry.seq <= lastLogSeq) return;   // already rendered
    lastLogSeq = entry.seq;
  }
  appendLogLine(entry.at, entry.level, entry.message);
}

function startLog() {
  if (eventSource) eventSource.close();
  eventSource = new EventSource("/api/logs?stream=true");
  eventSource.onmessage = (event) => {
    try {
      appendLog(JSON.parse(event.data));
    } catch {
      /* a malformed frame is not worth breaking the tail over */
    }
  };
  eventSource.onerror = () => {
    // --- EventSource reconnects on its own; say so once rather than
    // --- pretending the tail is still live
    $("logBox").dataset.streaming = "reconnecting";
  };
  eventSource.onopen = () => {
    $("logBox").dataset.streaming = "open";
  };
}

//+------------------------------------------------------------------+
//| Boot                                                             |
//+------------------------------------------------------------------+
async function loadHealth() {
  try {
    const response = await fetch("/api/health", { cache: "no-store" });
    const health = await response.json();
    $("bridgeVersion").textContent = health.bridge_version || "—";
    $("schemaVersion").textContent = health.schema ?? "—";
    // --- The footer path is the fastest answer to "why is every field
    // --- empty?": it is the directory this process resolved, which is
    // --- regularly not the one the terminal is publishing to. Tooltip
    // --- carries the full pair; the visible text stays short.
    const locations = health.locations || {};
    const root = locations.bridge_root || "—";
    const rootEl = $("bridgeRoot");
    rootEl.textContent = root;
    rootEl.title = "common files: " + (locations.common_files || "?") +
      " [" + (locations.common_exists || "?") + "]" +
      " / bridge root [" + (locations.root_exists || "?") + "]";
    rootEl.className = locations.root_exists === "yes"
      ? "text-secondary"
      : "text-secondary underline decoration-dotted";
  } catch {
    /* health is decorative; the status poll is what matters */
    $("bridgeVersion").textContent = "—";
    $("schemaVersion").textContent = "—";
    $("bridgeRoot").textContent = "—";
    $("bridgeRoot").title = "";
  }
}

// --- the order field starts on the chart being shown, so the common case
// --- needs no typing. Kept as its own function because boot() is checked
// --- for only calling declared helpers, and "$" is a const arrow. It routes
// --- through chooseSymbol so the trigger, the hint and the quote are painted
// --- from the very same value that will be sent.
function defaultOrderSymbol() {
  if (!$("orderSymbol").value.trim() && activeSymbol) {
    chooseSymbol(activeSymbol, false);
  }
}

async function boot() {
  wireCommands();
  wireJournal();
  setLastGood(0);
  await loadHealth();
  await loadSymbols();
  startLog();
  await refresh();
  defaultOrderSymbol();
  await loadUnmanaged();
  await loadHistory();
  await loadJournal();
  setInterval(refresh, REFRESH_MS);
  // --- reconciliation costs a terminal round trip, so it polls on its own
  // --- slower cadence rather than riding the 1 s status loop
  setInterval(loadUnmanaged, REFRESH_MS * 3);

  // --- deal history only changes when a position closes, and each read is a
  // --- terminal round trip. A minute is plenty; the status poll carries the
  // --- live numbers.
  setInterval(loadHistory, REFRESH_MS * 60);
  setInterval(loadJournal, REFRESH_MS * 5);

  // --- the health block is static for a process, but a bridge restart
  // --- during a long session should be visible without a page reload
  setInterval(loadHealth, REFRESH_MS * 30);
  // --- charts attach and detach; the picker has to keep up
  setInterval(loadSymbols, REFRESH_MS * 10);
}

boot();


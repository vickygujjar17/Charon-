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
      ? "text-[11px] italic text-shade-400"
      : "text-[11px] font-semibold uppercase tracking-wider text-violet";
    level.textContent = rung.level;

    const px = document.createElement("span");
    px.className = rung.missing
      ? "font-mono text-[11px] text-shade-400"
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
const STATUS_STYLE = {
  live: "bg-live/15 text-live shadow-glow-live",
  stale: "bg-stale/15 text-stale",
  offline: "bg-offline/15 text-offline shadow-glow-offline",
  starting: "bg-accent/15 text-accent shadow-glow-accent",
  error: "bg-offline/15 text-offline shadow-glow-offline",
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
    "font-mono text-[11px] " + (rounded > SLOW_FETCH_MS ? "text-stale" : "text-shade-400");
}

function setLatencyFailed() {
  const label = $("latencyLabel");
  label.textContent = "—";
  label.title = "The last GET /api/status did not complete.";
  label.className = "font-mono text-[11px] text-offline";
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


  $("equity").textContent = money(snap.equity, snap.currency);
  $("balance").textContent = money(snap.balance, snap.currency);
  $("openPl").textContent = money(snap.basket.open_pl, snap.currency);
  $("riskPct").textContent = pct(snap.basket.risk_pct);
  $("riskMoney").textContent = money(snap.basket.risk_money, snap.currency);

  // --- a negative P/L reads red, positive green, flat neutral
  const pl = snap.basket.open_pl;
  $("openPl").className =
    "readout mt-1 text-lg font-semibold " +
    (pl > 0 ? "text-live" : pl < 0 ? "text-offline" : "text-shade-100");

  // --- basket
  const dir = $("basketDir");
  dir.textContent = snap.basket.valid ? snap.basket.direction : "flat";
  dir.className =
    "pill " +
    (snap.basket.direction === "BUY"
      ? "bg-live/15 text-live shadow-glow-live"
      : snap.basket.direction === "SELL"
        ? "bg-offline/15 text-offline shadow-glow-offline"
        : "bg-ink-700 text-shade-300");

  $("basketLegs").textContent = snap.basket.legs;
  $("basketAvg").textContent = price(snap.basket.avg_entry, digits);
  $("basketLot").textContent =
    snap.basket.lot === null ? "—" : Number(snap.basket.lot).toFixed(2);

  renderLadder($("ladder"), snap.basket.ladder);

  // --- working orders. Rendered before the pyramid block so a LIMIT ticket
  // --- is visible even when pyramiding is disabled.
  renderPending(snap.pending, digits, snap.currency);

  // --- pyramid
  const pyr = snap.pyramid;
  const armState = $("armState");
  const countdown = $("armCountdown");

  if (pyr.armed) {
    armState.textContent = "ARMED";
    armState.className = "text-sm font-semibold text-live";
    countdown.textContent =
      pyr.secs_left !== null && pyr.secs_left >= 0 ? pyr.secs_left + "s" : "∞";
    countdown.className = "readout text-lg font-bold text-live";
  } else {
    armState.textContent = pyr.enabled ? "disarmed" : "pyramiding off";
    armState.className = "text-sm font-semibold text-shade-400";
    countdown.textContent = "";
    countdown.className = "readout text-lg font-bold text-shade-300";
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
  tr.className = "border-b border-ink-800 transition-colors hover:bg-ink-800/40";
  tr.dataset.ticket = String(leg.ticket);

  // --- ticket, side, tier, entry, vol, stop, 1R, progress, flags
  for (let index = 0; index < 9; index += 1) {
    const td = document.createElement("td");
    td.className = index === 0 ? "py-2 pl-4 pr-3" : "py-2 pr-3";
    td.dataset.cell = String(index);
    tr.append(td);
  }

  // --- the ladder cell keeps its own small stack of rungs
  const tdLadder = document.createElement("td");
  tdLadder.className = "py-2 pr-3";
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
  tdActions.className = "py-2 pr-4";
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
  button.className = variant + " px-2 py-0.5 text-[10px]";
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
    { text: "#" + leg.ticket, cls: "text-shade-300" },
    {
      text: leg.side,
      cls: leg.side === "BUY" ? "font-semibold text-live" : "font-semibold text-offline",
    },
    { text: "T" + leg.tier, cls: "text-violet" },
    { text: price(leg.entry, digits), cls: "" },
    { text: leg.lot === null ? "—" : Number(leg.lot).toFixed(2), cls: "" },
    // --- a zero stop is not a tight stop, it is an unprotected leg
    { text: price(leg.sl, digits), cls: leg.sl > 0 ? "" : "text-offline" },
    { text: price(leg.risk_price, digits), cls: "text-shade-400" },
    { text: rr(leg.progress_rr), cls: leg.progress_rr > 0 ? "text-live" : "text-shade-300" },
    { text: flags, cls: "text-[10px] uppercase tracking-wider text-shade-400" },
  ];

  const tdNodes = tr.querySelectorAll("td[data-cell]");
  for (let index = 0; index < cells.length; index += 1) {
    const td = tdNodes[index];
    if (!td) continue;
    // --- the first cell keeps the table's left gutter; without it the
    // --- ticket number sits flush against the panel edge
    td.className =
      (index === 0 ? "py-2 pl-4 pr-3 " : "py-2 pr-3 ") + cells[index].cls;
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
  tr.className = "border-b border-ink-800 transition-colors hover:bg-ink-800/40";
  tr.dataset.ticket = String(order.ticket);
  for (let index = 0; index < 7; index += 1) {
    const td = document.createElement("td");
    td.className = index === 0 ? "py-2 pl-4 pr-3" : "py-2 pr-3";
    td.dataset.cell = String(index);
    tr.append(td);
  }
  return tr;
}

function fillPendingRow(tr, order, digits) {
  const cells = [
    { text: "#" + order.ticket, cls: "text-shade-300" },
    {
      text: order.side || "\u2014",
      cls: order.side === "BUY" ? "font-semibold text-live" : "font-semibold text-offline",
    },
    { text: order.order_type || "\u2014", cls: "text-shade-300" },
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
      (index === 0 ? "py-2 pl-4 pr-3 " : "py-2 pr-3 ") + cells[index].cls;
    td.textContent = cells[index].text;
  }

  const tdActions = tdNodes[6];
  if (tdActions) {
    tdActions.className = "py-2 pr-4";
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
  idle: "pill bg-ink-700 text-shade-300",
  pending: "pill bg-stale/15 text-stale",
  accepted: "pill bg-live/15 text-live shadow-glow-live",
  rejected: "pill bg-offline/15 text-offline shadow-glow-offline",
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
    verdict.className = "mt-2 text-[11px] text-shade-400";
    verdict.textContent = "Nothing submitted yet.";
    return;
  }

  if (status === "rejected") {
    verdict.className = "mt-2 text-[11px] text-offline";
    verdict.textContent = "EA refused: " + (order.reason || "no reason given");
    return;
  }

  verdict.className =
    status === "pending" ? "mt-2 text-[11px] text-stale" : "mt-2 text-[11px] text-live";
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
  const trailStart = optionalNumber("orderTrailStart");
  if (trailStart !== null) {
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
  message.className = "mt-2 text-[11px] text-shade-400";
  message.textContent = "sending\u2026";

  const ticket = readTicket();
  // --- a blank box is not zero: catch it here rather than sending NaN,
  // --- which JSON turns into null and the server rejects with a message
  // --- far less useful than this one.
  for (const key of ["block_top", "block_bottom"]) {
    if (!Number.isFinite(ticket[key]) || ticket[key] <= 0) {
      message.className = "mt-2 text-[11px] text-offline";
      message.textContent = key.replace("_", " ") + " must be a positive number.";
      return;
    }
  }
  // --- whichever unit is selected, exactly one risk figure is sent, and it
  // --- has to be positive before it leaves the browser.
  const riskKey = ticket.risk_money !== undefined ? "risk_money" : "risk_pct";
  if (!Number.isFinite(ticket[riskKey]) || ticket[riskKey] <= 0) {
    message.className = "mt-2 text-[11px] text-offline";
    message.textContent =
      riskKey.replace("_", " ") + " must be a positive number.";
    return;
  }
  if (ticket.order_type === "LIMIT" && !Number.isFinite(ticket.entry)) {
    message.className = "mt-2 text-[11px] text-offline";
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
      message.className = "mt-2 text-[11px] text-offline";
      const detail = body.detail;
      message.textContent =
        typeof detail === "string" ? detail : "HTTP " + response.status;
      return;
    }
    // --- queued is not filled: the EA adjudicates on its next tick and the
    // --- verdict arrives in the snapshot, not in this response.
    message.className = "mt-2 text-[11px] text-accent";
    message.textContent =
      label + " queued \u2014 the EA adjudicates it on its next tick.";
    appendLocalLog("command", "order: " + label + " queued from the dashboard");
    refresh();
  } catch (error) {
    message.className = "mt-2 text-[11px] text-offline";
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
    cell.className = "rounded border border-ink-700 bg-ink-800/40 p-2";

    const label = document.createElement("div");
    label.className = "label";
    label.textContent = "Stage " + index;
    cell.append(label);

    const kind = document.createElement("select");
    kind.className = "ticket-field mt-1 text-[11px]";
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
    trigger.className = "ticket-field mt-1 text-[11px]";
    cell.append(trigger);

    const mult = document.createElement("input");
    mult.type = "number";
    mult.step = "0.05";
    mult.min = "0";
    mult.dataset.stage = "mult";
    mult.placeholder = "size x1.0";
    mult.className = "ticket-field mt-1 text-[11px]";
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
    $("orderEntry").disabled = !isLimit;
    $("orderEntry").placeholder = isLimit ? "price inside the block" : "market";
  });
  $("orderEntry").disabled = true;

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

  // --- the symbol field's own hint tracks what is typed, and the filter box
  // --- narrows the browser's suggestion list without ever touching the value
  // --- that will be sent.
  $("orderSymbol").addEventListener("input", paintSymbolHint);
  $("orderSymbolFilter").addEventListener("input", () => {
    const needle = $("orderSymbolFilter").value.trim().toLowerCase();
    if (needle === "") {
      paintSymbolOptions(symbolAll);
      return;
    }
    const filtered = symbolAll.filter((entry) => {
      const haystack =
        (entry.symbol || "") + " " + (entry.label || "") + " " + assetLabel(entry.asset_class);
      return haystack.toLowerCase().indexOf(needle) !== -1;
    });
    paintSymbolOptions(filtered);
  });

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
      $("orderMsg").className = "mt-2 text-[11px] text-offline";
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
      message.className = "mt-2 text-[11px] text-offline";
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
    message.className = "mt-2 text-[11px] text-shade-400";
    message.textContent = "sending\u2026";
  }
  try {
    const response = await fetch(path, { method: "POST" });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (message) {
        message.className = "mt-2 text-[11px] text-offline";
        message.textContent =
          typeof body.detail === "string" ? body.detail : "HTTP " + response.status;
      }
      return false;
    }
    if (message) {
      message.className = "mt-2 text-[11px] text-accent";
      message.textContent =
        "Cancel queued for #" + ticketNumber + " \u2014 the EA applies it on its next tick.";
    }
    appendLocalLog("command", "order: cancel queued for #" + ticketNumber);
    refresh();
    return true;
  } catch (error) {
    if (message) {
      message.className = "mt-2 text-[11px] text-offline";
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
  messageEl.className = "mt-2 text-[11px] text-shade-400";
  messageEl.textContent = "sending…";
  try {
    const response = await fetch(path, { method: "POST" });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      messageEl.className = "mt-2 text-[11px] text-offline";
      messageEl.textContent = body.detail || "HTTP " + response.status;
      return;
    }
    // --- queued is not applied: the EA drains its inbox on its own timer
    messageEl.className = "mt-2 text-[11px] text-accent";
    messageEl.textContent = label + " queued — the EA applies it on its next tick.";
  } catch (error) {
    messageEl.className = "mt-2 text-[11px] text-offline";
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
  // --- order field follows only while it is empty: once the operator has
  // --- typed a different symbol, this must not overwrite it.
  $("symbolSelect").addEventListener("change", (event) => {
    activeSymbol = event.target.value || null;
    if (!$("orderSymbol").value.trim()) {
      $("orderSymbol").value = activeSymbol || "";
    }
    refresh();
  });
}

//+------------------------------------------------------------------+
//| Symbol picker                                                    |
//|                                                                  |
//|  Two independent things are going on and conflating them is how   |
//|  orders reach the wrong chart:                                    |
//|                                                                   |
//|  * which chart's snapshot is on screen  -- the picker;            |
//|  * which symbol an order ticket names   -- its own field.          |
//|                                                                   |
//|  The ticket field defaults to the chart on screen but stays       |
//|  editable, so a trader can queue for another chart without losing  |
//|  the readout they are watching.                                    |
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
  crypto: "Crypto",
  stocks: "Stocks",
  other: "Other",
};

function assetLabel(key) {
  return ASSET_LABELS[key] || key || "Other";
}

// --- the picker offers the known names grouped by asset class. The grouping
// --- is a heading, never a filter: every entry is offered regardless of what
// --- the bridge thinks it is, because a broker names things the textbook does
// --- not.
// ---
// --- Two lists on purpose: ``symbolAll`` is everything the bridge reported and
// --- is what the filter searches; ``symbolCatalogue`` is what is currently on
// --- offer. Collapsing them into one would make a filtered paint overwrite the
// --- source, so clearing the filter could never restore the full list.
let symbolAll = [];
let symbolCatalogue = [];

function paintSymbolOptions(catalogue) {
  // --- a datalist's children are <option> elements, so they are built with
  // --- createElement rather than innerHTML: the latter would insert text,
  // --- and the browser would have nothing to offer as a suggestion.
  const list = $("symbolOptions");
  const options = [];
  for (const entry of catalogue) {
    const option = document.createElement("option");
    option.value = entry.symbol;                  // what gets sent
    // --- the class rides along in the label so the browser's own suggestion
    // --- list reads as a grouped menu even though a datalist cannot nest
    option.label = (entry.label || entry.symbol) + " \u00b7 " + assetLabel(entry.asset_class);
    options.push(option);
  }
  list.replaceChildren(...options);
  symbolCatalogue = catalogue;
  paintSymbolHint();
}

// --- the hint under the field names the exact broker symbol that will be
// --- sent, and what the bridge thinks it is. It never rewrites the field: a
// --- typed name is authoritative.
function paintSymbolHint() {
  const hint = $("orderSymbolHint");
  if (!hint) return;
  const typed = $("orderSymbol").value.trim();
  if (typed === "") {
    hint.textContent = symbolCatalogue.length
      ? symbolCatalogue.length + " instruments known to the bridge"
      : "";
    return;
  }
  const match = symbolCatalogue.find(
    (entry) => (entry.symbol || "").toLowerCase() === typed.toLowerCase()
  );
  if (match) {
    hint.textContent =
      assetLabel(match.asset_class) +
      " \u00b7 " +
      ((match.sources || []).indexOf("chart") !== -1
        ? "live chart, can be driven now"
        : "listed by the terminal, no chart yet");
    return;
  }
  // --- not in the catalogue is not an error: the bridge may simply not be
  // --- able to enumerate this broker's list. Saying so is honest.
  hint.textContent = "not in the bridge's list \u2014 it will be sent exactly as typed";
}

function paintSymbolPicker(catalogue) {
  const charts = catalogue.filter(
    (entry) => (entry.sources || []).indexOf("chart") !== -1
  );
  const picker = $("symbolPicker");

  if (charts.length < 2) {
    picker.className = "hidden";        // one chart needs no chooser
    return;
  }
  picker.className = "ml-1 flex flex-col gap-1 border-l border-ink-700 pl-3";

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
    const catalogue = body.symbols || [];
    symbolAll = catalogue;
    paintSymbolOptions(catalogue);
    paintSymbolPicker(catalogue);
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
  unavailable: "pill bg-ink-700 text-shade-300",
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
    detail.className = "px-4 py-2 text-[11px] text-shade-400";
    detail.textContent = pending.length
      ? pending.length + " further position(s) not yet confirmed."
      : "Open, but no live Charon chart accounts for them.";
  } else if (state === "ok") {
    detail.className = "px-4 py-3 text-[11px] text-shade-400";
    detail.textContent = "Checked every live chart: nothing unmanaged.";
  } else if (state === "unknown") {
    // --- the sentence that must never become "all clear"
    detail.className = "px-4 py-3 text-[11px] text-stale";
    detail.textContent =
      "A chart is stale or still starting, so its positions cannot be " +
      "checked. This is not an all-clear.";
  } else {
    detail.className = "px-4 py-3 text-[11px] text-shade-400";
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
//| Log stream                                                       |
//|                                                                  |
//| The server assigns each entry a monotonic seq and honours        |
//| Last-Event-ID, but a reconnect can still replay the tail -- so    |
//| the client de-duplicates on seq as well. Belt and braces, because |
//| a log that repeats itself on every hiccup is worse than no log.   |
//+------------------------------------------------------------------+
const LOG_COLOUR = {
  info: "text-shade-300",
  warn: "text-stale",
  error: "text-offline",
  command: "text-accent",
};

function appendLogLine(at, level, message) {
  const box = $("logBox");
  const line = document.createElement("div");
  line.className = "log-line " + (LOG_COLOUR[level] || "text-shade-300");

  const stamp = document.createElement("span");
  stamp.className = "text-shade-400/80";
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
      ? "text-shade-300"
      : "text-shade-300 underline decoration-dotted";
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
// --- for only calling declared helpers, and "$" is a const arrow.
function defaultOrderSymbol() {
  if (!$("orderSymbol").value.trim() && activeSymbol) {
    $("orderSymbol").value = activeSymbol;
  }
}

async function boot() {
  wireCommands();
  setLastGood(0);
  await loadHealth();
  await loadSymbols();
  startLog();
  await refresh();
  defaultOrderSymbol();
  await loadUnmanaged();
  setInterval(refresh, REFRESH_MS);
  // --- reconciliation costs a terminal round trip, so it polls on its own
  // --- slower cadence rather than riding the 1 s status loop
  setInterval(loadUnmanaged, REFRESH_MS * 3);

  // --- the health block is static for a process, but a bridge restart
  // --- during a long session should be visible without a page reload
  setInterval(loadHealth, REFRESH_MS * 30);
  // --- charts attach and detach; the picker has to keep up
  setInterval(loadSymbols, REFRESH_MS * 10);
}

boot();


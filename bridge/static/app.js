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
      : "text-[11px] font-semibold text-shade-300";
    level.textContent = rung.level;

    const px = document.createElement("span");
    px.className = rung.missing
      ? "font-mono text-[11px] text-shade-400"
      : "font-mono text-xs font-semibold text-shade-100";
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
  live: "bg-live/15 text-live",
  stale: "bg-stale/15 text-stale",
  offline: "bg-offline/15 text-offline",
  starting: "bg-accent/15 text-accent",
  error: "bg-offline/15 text-offline",
};

let lastGoodAt = 0;   // epoch seconds of the last good payload

// --- the panels dim together, so a stale screen cannot look live in the
// --- corner while the numbers still read as current.
const DIMMABLE_SELECTOR = "main > header, main > section, main .card";
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
    "text-center text-sm font-semibold text-stale",
  offline:
    "sticky top-0 z-50 border-b border-offline/40 bg-offline/15 px-4 py-2 " +
    "text-center text-sm font-semibold text-offline",
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
    "value-lg " + (pl > 0 ? "text-live" : pl < 0 ? "text-offline" : "text-shade-100");

  // --- basket
  const dir = $("basketDir");
  dir.textContent = snap.basket.valid ? snap.basket.direction : "flat";
  dir.className =
    "pill " +
    (snap.basket.direction === "BUY"
      ? "bg-live/15 text-live"
      : snap.basket.direction === "SELL"
        ? "bg-offline/15 text-offline"
        : "bg-ink-700 text-shade-300");

  $("basketLegs").textContent = snap.basket.legs;
  $("basketAvg").textContent = price(snap.basket.avg_entry, digits);
  $("basketLot").textContent =
    snap.basket.lot === null ? "—" : Number(snap.basket.lot).toFixed(2);

  renderLadder($("ladder"), snap.basket.ladder);

  // --- pyramid
  const pyr = snap.pyramid;
  const armState = $("armState");
  const countdown = $("armCountdown");

  if (pyr.armed) {
    armState.textContent = "ARMED";
    armState.className = "text-sm font-semibold text-live";
    countdown.textContent =
      pyr.secs_left !== null && pyr.secs_left >= 0 ? pyr.secs_left + "s" : "∞";
    countdown.className = "font-mono text-lg font-bold text-live";
  } else {
    armState.textContent = pyr.enabled ? "disarmed" : "pyramiding off";
    armState.className = "text-sm font-semibold text-shade-400";
    countdown.textContent = "";
    countdown.className = "font-mono text-lg font-bold text-shade-300";
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
  tr.className = "border-b border-ink-800";
  tr.dataset.ticket = String(leg.ticket);

  // --- ticket, side, tier, entry, vol, stop, 1R, progress, flags
  for (let index = 0; index < 9; index += 1) {
    const td = document.createElement("td");
    td.className = "py-2 pr-3";
    td.dataset.cell = String(index);
    tr.append(td);
  }

  // --- the ladder cell keeps its own small stack of rungs
  const tdLadder = document.createElement("td");
  tdLadder.className = "py-2";
  const box = document.createElement("div");
  box.className = "space-y-1";
  box.dataset.role = "leg-ladder";
  renderLadder(box, null);
  tdLadder.append(box);
  tr.append(tdLadder);

  return tr;
}

function fillLegRow(tr, leg, digits) {
  const flags = [
    leg.trail_active ? "trail" : null,
    leg.be_done ? "BE" : null,
    leg.half_risk_done ? "½R" : null,
  ].filter(Boolean).join(" ") || "—";

  const cells = [
    { text: "#" + leg.ticket, cls: "text-shade-300" },
    { text: leg.side, cls: leg.side === "BUY" ? "text-live" : "text-offline" },
    { text: "T" + leg.tier, cls: "" },
    { text: price(leg.entry, digits), cls: "" },
    { text: leg.lot === null ? "—" : Number(leg.lot).toFixed(2), cls: "" },
    // --- a zero stop is not a tight stop, it is an unprotected leg
    { text: price(leg.sl, digits), cls: leg.sl > 0 ? "" : "text-offline" },
    { text: price(leg.risk_price, digits), cls: "text-shade-400" },
    { text: rr(leg.progress_rr), cls: leg.progress_rr > 0 ? "text-live" : "text-shade-300" },
    { text: flags, cls: "text-shade-400" },
  ];

  const tdNodes = tr.querySelectorAll("td[data-cell]");
  for (let index = 0; index < cells.length; index += 1) {
    const td = tdNodes[index];
    if (!td) continue;
    td.className = "py-2 pr-3 " + cells[index].cls;
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
//| Polling                                                          |
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
    const response = await fetch("/api/status", {
      cache: "no-store",
      signal: controller.signal,
    });
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
  accepted: "pill bg-live/15 text-live",
  rejected: "pill bg-offline/15 text-offline",
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
  const symbol = $("symbolLabel").textContent;
  const entryRaw = $("orderEntry").value.trim();
  const type = $("orderType").value;
  return {
    symbol: symbol && symbol !== "\u2014" ? symbol : null,
    side: $("orderSide").value,
    order_type: type,
    entry: entryRaw === "" ? null : Number(entryRaw),
    block_top: Number($("orderBlockTop").value),
    block_bottom: Number($("orderBlockBottom").value),
    risk_pct: Number($("orderRisk").value),
    trail_trigger: Number($("orderTrail").value),
    rung: Number($("orderRung").value),
  };
}

async function postOrder(path, label) {
  const message = $("orderMsg");
  message.className = "mt-2 text-[11px] text-shade-400";
  message.textContent = "sending\u2026";

  const ticket = readTicket();
  // --- a blank box is not zero: catch it here rather than sending NaN,
  // --- which JSON turns into null and the server rejects with a message
  // --- far less useful than this one.
  for (const key of ["block_top", "block_bottom", "risk_pct"]) {
    if (!Number.isFinite(ticket[key]) || ticket[key] <= 0) {
      message.className = "mt-2 text-[11px] text-offline";
      message.textContent = key.replace("_", " ") + " must be a positive number.";
      return;
    }
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

function wireOrderPanel() {
  $("orderType").addEventListener("change", (event) => {
    const isLimit = event.target.value === "LIMIT";
    $("orderEntry").disabled = !isLimit;
    $("orderEntry").placeholder = isLimit ? "price inside the block" : "market";
  });
  $("orderEntry").disabled = true;

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
    const symbol = $("symbolLabel").textContent;
    const path =
      "/api/order/cancel?position=" +
      encodeURIComponent(ticket.slice(1)) +
      (symbol && symbol !== "\u2014" ? "&symbol=" + encodeURIComponent(symbol) : "");
    message.className = "mt-2 text-[11px] text-shade-400";
    message.textContent = "sending\u2026";
    try {
      const response = await fetch(path, { method: "POST" });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        message.className = "mt-2 text-[11px] text-offline";
        message.textContent =
          typeof body.detail === "string" ? body.detail : "HTTP " + response.status;
        return;
      }
      message.className = "mt-2 text-[11px] text-accent";
      message.textContent = "Cancel queued \u2014 the EA applies it on its next tick.";
      appendLocalLog("command", "order: cancel queued from the dashboard");
      refresh();
    } catch (error) {
      message.className = "mt-2 text-[11px] text-offline";
      message.textContent = "failed: " + error.message;
    }
  });
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

  $("armBtn").addEventListener("click", () =>
    postCommand("/api/pyramid/arm", $("armMsg"), "Arm"));

  $("disarmBtn").addEventListener("click", () =>
    postCommand("/api/pyramid/disarm", $("armMsg"), "Disarm"));

  $("trailSelect").addEventListener("change", (event) => {
    const mode = event.target.value;
    postCommand("/api/trail?mode=" + encodeURIComponent(mode), $("trailMsg"), "Trail mode");
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
  info: "text-shade-300",
  warn: "text-stale",
  error: "text-offline",
  command: "text-accent",
};

function appendLogLine(at, level, message) {
  const box = $("logBox");
  const line = document.createElement("div");
  line.className = LOG_COLOUR[level] || "text-shade-300";

  const stamp = document.createElement("span");
  stamp.className = "text-shade-400";
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
  } catch {
    /* health is decorative; the status poll is what matters */
    $("bridgeVersion").textContent = "—";
    $("schemaVersion").textContent = "—";
  }
}

async function boot() {
  wireCommands();
  setLastGood(0);
  await loadHealth();
  startLog();
  await refresh();
  setInterval(refresh, REFRESH_MS);

  // --- the health block is static for a process, but a bridge restart
  // --- during a long session should be visible without a page reload
  setInterval(loadHealth, REFRESH_MS * 30);
}

boot();


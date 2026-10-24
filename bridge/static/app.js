// Charon dashboard client.
//
// Two rules this file follows without exception:
//
//   1. It never computes a price, a risk figure or an R multiple. Every
//      number comes from /api/status, which comes from the EA.
//   2. It never pretends to be current. If the snapshot is stale or the
//      bridge is down, the banner says so and the values go dim.

const REFRESH_MS = 1000;

const $ = (id) => document.getElementById(id);

let lastGood = null;      // last successful payload, for dimming
let eventSource = null;

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

//+------------------------------------------------------------------+
//| Status pill / banner                                             |
//+------------------------------------------------------------------+
const STATUS_STYLE = {
  live: "bg-live/15 text-live",
  stale: "bg-stale/15 text-stale",
  offline: "bg-offline/15 text-offline",
  starting: "bg-accent/15 text-accent",
  error: "bg-offline/15 text-offline",
};

function setStatus(status, ageSeconds, detail) {
  const pill = $("statusPill");
  pill.className = "pill " + (STATUS_STYLE[status] || STATUS_STYLE.error);
  pill.textContent = status;

  $("ageLabel").textContent = ageText(ageSeconds);

  const banner = $("banner");
  if (status === "stale") {
    banner.className =
      "sticky top-0 z-50 border-b border-stale/40 bg-stale/15 px-4 py-2 " +
      "text-center text-sm font-semibold text-stale";
    banner.textContent =
      "STALE DATA — the last EA snapshot is " + ageText(ageSeconds) +
      ". Do not act on these numbers.";
    banner.classList.remove("hidden");
  } else if (status === "offline") {
    banner.className =
      "sticky top-0 z-50 border-b border-offline/40 bg-offline/15 px-4 py-2 " +
      "text-center text-sm font-semibold text-offline";
    banner.textContent =
      "EA OFFLINE — no snapshot for " + ageText(ageSeconds) +
      ". The chart is not being described by this view.";
    banner.classList.remove("hidden");
  } else if (status === "error") {
    banner.className =
      "sticky top-0 z-50 border-b border-offline/40 bg-offline/15 px-4 py-2 " +
      "text-center text-sm font-semibold text-offline";
    banner.textContent = detail || "BRIDGE ERROR — cannot reach /api/status.";
    banner.classList.remove("hidden");
  } else {

//+------------------------------------------------------------------+
//| Render one payload                                               |
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
  $("pyramidTrigger").textContent = pyr.trigger;

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
  }

  const canArm = pyr.enabled && pyr.trigger === "MANUAL" && snap.basket.valid;
  $("armBtn").disabled = !canArm;
  $("armHint").textContent = !pyr.enabled
    ? "Pyramiding is disabled on this chart."
    : pyr.trigger !== "MANUAL"
      ? "Trigger is AUTO — arming has no effect."
      : !snap.basket.valid
        ? "No basket to scale into yet."
        : "Arming authorises one scale-in. Risk gates still apply.";

  // --- trail selector reflects the EA, but does not fight the user

function renderLegs(legs, digits) {
  const tbody = $("legRows");
  tbody.replaceChildren();

  for (const leg of legs) {
    const tr = document.createElement("tr");
    tr.className = "border-b border-ink-800";

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
      { text: price(leg.sl, digits), cls: leg.sl > 0 ? "" : "text-offline" },
      { text: price(leg.risk_price, digits), cls: "text-shade-400" },
      { text: rr(leg.progress_rr), cls: leg.progress_rr > 0 ? "text-live" : "text-shade-300" },
      { text: flags, cls: "text-shade-400" },
    ];

    for (const cell of cells) {
      const td = document.createElement("td");
      td.className = "py-2 pr-3 " + cell.cls;
      td.textContent = cell.text;
      tr.append(td);
    }

    // --- the ladder cell keeps its own small stack of rungs
    const tdLadder = document.createElement("td");
    tdLadder.className = "py-2";
    const box = document.createElement("div");
    box.className = "space-y-1";
    renderLadder(box, leg.ladder);
    tdLadder.append(box);
    tr.append(tdLadder);

    tbody.append(tr);
  }
}

//+------------------------------------------------------------------+
//| Polling                                                          |
//+------------------------------------------------------------------+
async function refresh() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      setStatus("error", null, body.detail || "HTTP " + response.status);
      return;
    }
    const payload = await response.json();
    render(payload);
    setStatus(payload.status, payload.age_seconds);
  } catch (error) {
    setStatus("error", null, "Cannot reach the bridge: " + error.message);
  }
}

//+------------------------------------------------------------------+
//| Commands                                                         |
//+------------------------------------------------------------------+
async function postCommand(path, messageEl, label) {
  messageEl.textContent = "sending…";
  try {
    const response = await fetch(path, { method: "POST" });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      messageEl.textContent = body.detail || "HTTP " + response.status;
      return;
    }
    // --- queued is not applied: the EA drains its inbox on its own timer
    messageEl.textContent = label + " queued — the EA applies it on its next tick.";
  } catch (error) {
    messageEl.textContent = "failed: " + error.message;
  }
}

$("armBtn").addEventListener("click", () =>
  postCommand("/api/pyramid/arm", $("armMsg"), "Arm"));

$("disarmBtn").addEventListener("click", () =>
  postCommand("/api/pyramid/disarm", $("armMsg"), "Disarm"));

$("trailSelect").addEventListener("change", (event) => {
  const mode = event.target.value;
  postCommand("/api/trail?mode=" + encodeURIComponent(mode), $("trailMsg"), "Trail mode");
});

//+------------------------------------------------------------------+
//| Log stream                                                       |
//+------------------------------------------------------------------+
const LOG_COLOUR = {
  info: "text-shade-300",
  warn: "text-stale",
  error: "text-offline",
  command: "text-accent",
};

function appendLog(entry) {
  const box = $("logBox");
  const line = document.createElement("div");
  line.className = LOG_COLOUR[entry.level] || "text-shade-300";

  const stamp = document.createElement("span");
  stamp.className = "text-shade-400";
  stamp.textContent = new Date(entry.at * 1000).toLocaleTimeString() + "  ";

  line.append(stamp, document.createTextNode(entry.message));
  box.append(line);

  // --- keep the DOM bounded; the ring buffer is capped server-side too
  while (box.childElementCount > 300) {
    box.removeChild(box.firstChild);
  }
  box.scrollTop = box.scrollHeight;
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
  // --- EventSource reconnects on its own; no manual retry ladder needed
}

//+------------------------------------------------------------------+
//| Boot                                                             |
//+------------------------------------------------------------------+
async function boot() {
  try {
    const response = await fetch("/api/health", { cache: "no-store" });
    const health = await response.json();
    $("bridgeVersion").textContent = health.bridge_version || "—";
    $("schemaVersion").textContent = health.schema ?? "—";
  } catch {
    /* health is decorative; the status poll is what matters */
  }

  startLog();
  await refresh();
  setInterval(refresh, REFRESH_MS);
}

boot();

  const select = $("trailSelect");
  if (document.activeElement !== select) {
    select.value = String(snap.trail.mode);
  }

  renderLegs(snap.legs, digits);
  $("noLegs").classList.toggle("hidden", snap.legs.length > 0);
  lastGood = payload;
}

    banner.classList.add("hidden");
  }
}

  }
}

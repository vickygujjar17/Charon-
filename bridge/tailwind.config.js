/** Tailwind config for the vendored dashboard stylesheet.
 *
 * The generated CSS is committed to static/tailwind.css so the dashboard
 * never fetches a CDN at runtime. A trading panel must render identically
 * with no network access.
 */
module.exports = {
  // --- app.js is scanned too: the status pill colours and the .rung
  // --- component are constructed as strings at runtime, so without this
  // --- the build silently omits them and the banner renders colourless.
  content: ["./static/index.html", "./static/app.js"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // --- Charon palette: obsidian surfaces, low-glare, tuned for a
        // --- second monitor sitting next to the terminal. Colour is never
        // --- decoration here -- emerald is live/long/profit, crimson is
        // --- offline/short/loss/refused, cyan is technical (latency,
        // --- commands), violet is rung and tier metadata. If a number is
        // --- coloured, the colour is making a claim about it.
        ink: {
          950: "#030712",   // page
          925: "#0a0f1d",   // panel
          900: "#0b1222",   // well inside a panel
          850: "#0f1728",   // raised row
          800: "#141e33",   // hover
          700: "#1e2a44",   // hairline
          600: "#2c3c5c",   // control border
        },
        shade: {
          400: "#7d8ba3",
          300: "#a3aec2",
          200: "#c8d0de",
          100: "#e6ebf3",
        },
        live: "#10b981",     // live / long / profit
        stale: "#f59e0b",    // stale is warnable, not fatal -- never red
        offline: "#f43f5e",  // offline / short / loss / refused
        accent: "#38bdf8",   // latency and commands
        violet: "#818cf8",   // rungs and tiers
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
        // --- no webfont: the bridge gate forbids CDN references, and a
        // --- panel must render offline. System display faces only, so
        // --- weight and tracking do the typographic work instead.
        display: [
          "Bahnschrift",
          "DIN Alternate",
          "Segoe UI Variable Display",
          "Segoe UI",
          "system-ui",
          "sans-serif",
        ],
      },
      boxShadow: {
        // --- a panel sits above the page rather than on it
        panel: "inset 0 1px 0 0 rgba(255,255,255,0.035), 0 18px 40px -28px rgba(0,0,0,0.95)",
        "glow-live": "0 0 0 1px rgba(16,185,129,0.35), 0 0 22px -6px rgba(16,185,129,0.55)",
        "glow-offline": "0 0 0 1px rgba(244,63,94,0.35), 0 0 22px -6px rgba(244,63,94,0.55)",
        "glow-accent": "0 0 0 1px rgba(56,189,248,0.35), 0 0 22px -6px rgba(56,189,248,0.55)",
        "glow-violet": "0 0 0 1px rgba(129,140,248,0.35), 0 0 22px -6px rgba(129,140,248,0.5)",
      },
    },
  },
  plugins: [],
};

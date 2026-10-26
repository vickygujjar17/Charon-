/** Tailwind config for the vendored dashboard stylesheet.
 *
 * The generated CSS is committed to static/tailwind.css so the dashboard
 * never fetches a CDN at runtime. A trading panel must render identically
 * with no network access.
 *
 * Token naming, and why there are two sets
 * ----------------------------------------
 * v1.6.0 adopts the Apex Institutional Terminal palette. The Apex names
 * (``surface``, ``borderline``, ``text-*``, ``bull``, ``bear``, ``warn``,
 * ``brand``, ``alt``) are the ones new markup should use.
 *
 * The v1.x names (``ink-*``, ``shade-*``, ``live``, ``offline``, ``stale``,
 * ``accent``, ``violet``) are **kept as aliases pointing at the same
 * values**. They are not dead weight: app.js builds runtime class strings
 * from complete literals (``text-live``, ``text-offline``, ``text-stale``,
 * ``bg-ink-700``), and both the CSS gate and the client test suite assert
 * on them. Renaming instead of aliasing would strip the colour from every
 * status pill with no error anywhere -- the exact silent failure the
 * client tests exist to prevent.
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
        /* --- Apex surfaces ------------------------------------------- */
        // --- the page itself, and the deepest well inside a panel
        surface: {
          DEFAULT: "#0f131d",
          dim: "#0f131d",
          bright: "#353944",
          lowest: "#0a0e18",
          low: "#171b26",
          container: "#1c1f2a",
          high: "#262a35",
          highest: "#313540",
        },
        borderline: {
          DEFAULT: "#424754", // --- hairline between rows
          strong: "#8c909f", // --- control outline
        },
        /* --- Apex text ----------------------------------------------- */
        // --- `primary` and `secondary` are the two text weights; the third
        // --- is the dimmest label that is still legible on the page
        primary: "#dfe2f1",
        secondary: "#c2c6d6",
        tertiary: "#8c909f",
        /* --- Apex semantics ------------------------------------------ */
        bull: "#4edea3", // --- long / profit / live
        bear: "#ffb4ab", // --- short / loss / offline / refused
        warn: "#f59e0b", // --- stale: warnable, never fatal
        brand: "#adc6ff", // --- primary action / focus
        alt: "#4cd7f6", // --- technical: latency, commands

        /* --- v1.x aliases (do not remove: app.js emits these) -------- */
        ink: {
          950: "#0f131d", // page        <- surface
          925: "#171b26", // panel       <- surface-low
          900: "#0a0e18", // well        <- surface-lowest
          850: "#1c1f2a", // raised row  <- surface-container
          800: "#262a35", // hover       <- surface-high
          700: "#424754", // hairline    <- borderline
          600: "#313540", // control     <- surface-highest
        },
        shade: {
          400: "#8c909f", // <- tertiary
          300: "#c2c6d6", // <- secondary
          200: "#dfe2f1", // <- primary
          100: "#eef1fa", // --- the one step brighter than body text
        },
        live: "#4edea3", // <- bull
        stale: "#f59e0b", // <- warn
        offline: "#ffb4ab", // <- bear
        accent: "#adc6ff", // <- brand
        violet: "#4cd7f6", // <- alt
      },
      fontFamily: {
        // --- both faces are vendored into static/fonts and declared with
        // --- @font-face in src/input.css. The fallbacks stay in the stack
        // --- so a browser that refuses the woff2 still renders readable
        // --- text rather than waiting on a face that will not arrive.
        display: [
          "Inter",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "sans-serif",
        ],
        mono: [
          "JetBrains Mono",
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "monospace",
        ],
      },
      fontSize: {
        /* --- Apex type ramp, as named utilities --------------------- */
        "headline-xl": ["24px", { lineHeight: "28px", letterSpacing: "-0.02em", fontWeight: "600" }],
        "headline-lg": ["18px", { lineHeight: "22px", letterSpacing: "-0.015em", fontWeight: "600" }],
        "headline-sm": ["14px", { lineHeight: "18px", letterSpacing: "-0.01em", fontWeight: "600" }],
        "body-lg": ["14px", { lineHeight: "20px", letterSpacing: "-0.01em" }],
        "body-md": ["12px", { lineHeight: "16px" }],
        "body-sm": ["11px", { lineHeight: "14px" }],
        "num-lg": ["16px", { lineHeight: "18px", letterSpacing: "-0.01em", fontWeight: "600" }],
        "num-md": ["12px", { lineHeight: "14px", fontWeight: "500" }],
        "num-sm": ["10px", { lineHeight: "12px", letterSpacing: "0.02em", fontWeight: "500" }],
        caps: ["10px", { lineHeight: "12px", letterSpacing: "0.05em", fontWeight: "700" }],
      },
      borderRadius: {
        // --- Apex geometry is architectural: 4px frames, 2px chips.
        // --- `full` is reserved for status pings and avatars only.
        sm: "0.125rem",
        DEFAULT: "0.25rem",
        md: "0.375rem",
        lg: "0.5rem",
        xl: "0.75rem",
      },
      spacing: {
        // --- fixed chrome heights, so the header and rail never reflow
        header: "56px",
        rail: "64px",
        ticker: "32px",
      },
      boxShadow: {
        // --- Apex is flat: depth is a border and a background step, not a
        // --- drop shadow. v1.x's `panel` / `glow-*` are gone; keeping them
        // --- would let old markup reintroduce a look the design rejects.
        inset: "inset 0 1px 0 0 rgba(255,255,255,0.02)",
      },
    },
  },
  plugins: [],
};

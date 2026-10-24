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
        // --- Charon palette: dark, low-glare, tuned for a second monitor
        // --- sitting next to the terminal.
        ink: {
          950: "#07090d",
          900: "#0b0f16",
          850: "#10151f",
          800: "#151b27",
          700: "#1e2635",
          600: "#2b3547",
        },
        shade: {
          400: "#7d8ba3",
          300: "#a3aec2",
          200: "#c8d0de",
          100: "#e6ebf3",
        },
        live: "#22c55e",
        stale: "#f59e0b",
        offline: "#ef4444",
        accent: "#38bdf8",
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};

import servabo from "../brand/tailwind-preset.js";

/**
 * The app's Tailwind config: Servabo's brand preset (brand/tailwind-preset.js, shared with the
 * website) plus what only the app uses (the shell's spacing and its animations).
 *
 * @type {import('tailwindcss').Config}
 */
export default {
  presets: [servabo],
  darkMode: "class",
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      spacing: {
        header: "3.5rem", // 56px top bar
        sidebar: "16.25rem", // 260px expanded sidebar
        "sidebar-collapsed": "4.25rem", // 68px icon rail
      },
      keyframes: {
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "scale-in": {
          from: { opacity: "0", transform: "translateY(4px) scale(0.985)" },
          to: { opacity: "1", transform: "none" },
        },
        "slide-in-left": {
          from: { opacity: "0", transform: "translateX(-8px)" },
          to: { opacity: "1", transform: "none" },
        },
        "slide-up": {
          from: { opacity: "0", transform: "translateY(8px)" },
          to: { opacity: "1", transform: "none" },
        },
        float: {
          "0%, 100%": { transform: "translateY(0)" },
          "50%": { transform: "translateY(-6px)" },
        },
        scan: {
          "0%": { transform: "translateY(0%)", opacity: "0" },
          "10%": { opacity: "1" },
          "90%": { opacity: "1" },
          "100%": { transform: "translateY(2200%)", opacity: "0" },
        },
      },
      animation: {
        "fade-in": "fade-in 160ms cubic-bezier(0.2, 0, 0, 1)",
        "scale-in": "scale-in 180ms cubic-bezier(0.2, 0, 0, 1)",
        "slide-in-left": "slide-in-left 200ms cubic-bezier(0.2, 0, 0, 1)",
        "slide-up": "slide-up 260ms cubic-bezier(0.2, 0, 0, 1)",
        float: "float 6s ease-in-out infinite",
        scan: "scan 4.5s cubic-bezier(0.4, 0, 0.2, 1) infinite",
      },
    },
  },
  plugins: [],
}

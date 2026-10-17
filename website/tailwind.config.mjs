import servabo from "../brand/tailwind-preset.js";

/**
 * The website's Tailwind config: Servabo's brand preset (brand/tailwind-preset.js, the same one
 * the app uses) plus the site's own page widths.
 *
 * @type {import('tailwindcss').Config}
 */
export default {
  presets: [servabo],
  darkMode: "class",
  content: ["./src/**/*.{astro,html,ts,md,mdx}"],
  theme: {
    extend: {
      maxWidth: {
        page: "76rem",
        prose: "44rem",
      },
      letterSpacing: {
        tightest: "-0.035em",
      },
    },
  },
};

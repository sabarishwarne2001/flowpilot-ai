/**
 * Servabo's Tailwind preset: the brand's semantic colours, radii, type and elevation.
 *
 * Shared by the app (frontend/tailwind.config.js) and the website (website/tailwind.config.mjs),
 * so a colour or a radius means the same thing in both. The colours read the CSS variables in
 * brand/tokens.css; each config adds only what is its own (the app's sidebar widths, the site's
 * page widths).
 *
 * @type {import('tailwindcss').Config}
 */
export default {
  darkMode: "class",
  content: [],
  theme: {
    extend: {
      colors: {
        border: "hsl(var(--border))",
        "border-strong": "hsl(var(--border-strong))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        success: {
          DEFAULT: "hsl(var(--success))",
          foreground: "hsl(var(--success-foreground))",
        },
        warning: {
          DEFAULT: "hsl(var(--warning))",
          foreground: "hsl(var(--warning-foreground))",
        },
        info: {
          DEFAULT: "hsl(var(--info))",
          foreground: "hsl(var(--info-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        sidebar: {
          DEFAULT: "hsl(var(--sidebar))",
          foreground: "hsl(var(--sidebar-foreground))",
        },
      },
      borderRadius: {
        xl: "calc(var(--radius) + 4px)",
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      fontFamily: {
        // Self-hosted (@fontsource-variable), so a strict Content-Security-Policy's font-src
        // 'self' allows them in the app and on the website.
        sans: [
          "Inter Variable",
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        mono: [
          "JetBrains Mono Variable",
          "JetBrains Mono",
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "monospace",
        ],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
      },
      boxShadow: {
        "elevation-1": "0 1px 2px 0 hsl(var(--shadow-color) / 0.06), 0 1px 0 0 hsl(var(--highlight) / 0.03) inset",
        "elevation-2":
          "0 4px 12px -4px hsl(var(--shadow-color) / 0.16), 0 1px 3px 0 hsl(var(--shadow-color) / 0.08)",
        "elevation-3":
          "0 24px 64px -16px hsl(var(--shadow-color) / 0.45), 0 8px 20px -8px hsl(var(--shadow-color) / 0.25)",
        glow: "0 0 0 1px hsl(var(--primary) / 0.35), 0 0 24px -4px hsl(var(--primary) / 0.45)",
        "inner-highlight": "inset 0 1px 0 0 hsl(var(--highlight) / 0.06)",
      },
      transitionTimingFunction: {
        "out-expo": "cubic-bezier(0.2, 0, 0, 1)",
      },
    },
  },
  plugins: [],
};

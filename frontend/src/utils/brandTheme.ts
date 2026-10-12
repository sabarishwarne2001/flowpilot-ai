/**
 * Campaign session 3 (F-300, N-065) — an organization's brand colours, applied.
 *
 * `Organization > Branding` saves a primary, accent, background and text colour and a colour
 * scheme. On a verified custom domain with branding enabled, the host-resolved manifest carries
 * them, and they show as follows:
 *
 * * **Primary**: buttons, links and focus rings, on the sign-in pages and in the app served on
 *   that domain (`--primary`, `--ring`, with a readable text colour worked out for it, and a
 *   lighter shade for primary text and links on dark surfaces, `--primary-on-dark`).
 * * **Accent**: the glow behind the sign-in pages (`--brand-accent`).
 * * **Background** and **text**: the sign-in pages' backdrop (AuthShell). The app keeps its own
 *   surfaces, which are designed around the light and dark palettes.
 * * **Scheme**: light or dark for the sign-in pages. Inside the app light or dark stays each
 *   person's own choice (an accessibility setting, like the time zone in N-064).
 *
 * The manifest is unauthenticated input. Only six-digit hex reaches the page, written as numbers
 * into CSS custom properties through the CSSOM: no stylesheet text is built from it.
 */

import type { BrandingManifest } from "@/types/branding";

const HEX = /^#([0-9a-f]{6})$/i;

export const isHexColour = (value: string | null | undefined): value is string =>
  typeof value === "string" && HEX.test(value.trim());

const channels = (hex: string): [number, number, number] => {
  const digits = hex.trim().slice(1);
  return [0, 2, 4].map((at) => parseInt(digits.slice(at, at + 2), 16) / 255) as [number, number, number];
};

const round = (value: number): number => Math.round(value * 10) / 10;

/** "#0f766e" → "175.4 77.4% 26.1%", the form the stylesheet's `hsl(var(--primary))` reads. */
export const hexToHslTriplet = (hex: string): string => {
  const [r, g, b] = channels(hex);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const lightness = (max + min) / 2;
  const delta = max - min;
  let hue = 0;
  let saturation = 0;
  if (delta !== 0) {
    saturation = delta / (1 - Math.abs(2 * lightness - 1));
    if (max === r) {
      hue = 60 * (((g - b) / delta) % 6);
    } else if (max === g) {
      hue = 60 * ((b - r) / delta + 2);
    } else {
      hue = 60 * ((r - g) / delta + 4);
    }
  }
  if (hue < 0) {
    hue += 360;
  }
  return `${round(hue)} ${round(saturation * 100)}% ${round(lightness * 100)}%`;
};

const luminance = (hex: string): number => {
  const linear = channels(hex).map((channel) =>
    channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * linear[0]! + 0.7152 * linear[1]! + 0.0722 * linear[2]!;
};

const contrast = (a: number, b: number): number => (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);

const WHITE = "0 0% 100%";
const INK = "224 32% 11%"; // the light palette's --foreground
const INK_LUMINANCE = luminance("#121826");

/** White or near-black, whichever reads better on `hex` (WCAG contrast ratio). */
export const readableTextOn = (hex: string): string => {
  const surface = luminance(hex);
  return contrast(surface, 1) >= contrast(surface, INK_LUMINANCE) ? WHITE : INK;
};

/** The same hue and saturation at another lightness: a primary that reads on a dark surface. */
const lightened = (triplet: string, lightness: number): string => {
  const [hue, saturation] = triplet.split(" ");
  return `${hue} ${saturation} ${lightness}%`;
};

/** The custom properties the brand sets on the page, for a manifest that carries a brand. */
export const brandProperties = (manifest: BrandingManifest | null): Record<string, string> => {
  if (!manifest?.has_custom_branding) {
    return {};
  }
  const properties: Record<string, string> = {};
  if (isHexColour(manifest.primary_color)) {
    properties["--primary"] = hexToHslTriplet(manifest.primary_color);
    properties["--ring"] = properties["--primary"];
    properties["--primary-foreground"] = readableTextOn(manifest.primary_color);
    properties["--primary-on-dark"] = lightened(properties["--primary"], 72);
  }
  if (isHexColour(manifest.accent_color)) {
    properties["--brand-accent"] = hexToHslTriplet(manifest.accent_color);
  }
  return properties;
};

const MANAGED = ["--primary", "--ring", "--primary-foreground", "--primary-on-dark", "--brand-accent"] as const;

/** Sets the brand's properties on the page (or clears them, for no brand). */
export const applyBrandColours = (manifest: BrandingManifest | null, root: HTMLElement = document.documentElement): void => {
  const properties = brandProperties(manifest);
  for (const name of MANAGED) {
    const value = properties[name];
    if (value) {
      root.style.setProperty(name, value);
    } else {
      root.style.removeProperty(name);
    }
  }
};

/** `npm run check:self`: the colour maths, and that nothing but six-digit hex gets through. */
export const runBrandColourSelfCheck = (): string[] => {
  const failures: string[] = [];
  const expect = (label: string, actual: unknown, wanted: unknown): void => {
    if (JSON.stringify(actual) !== JSON.stringify(wanted)) {
      failures.push(`${label}: got ${JSON.stringify(actual)}, wanted ${JSON.stringify(wanted)}`);
    }
  };
  const brand = (fields: Partial<BrandingManifest>): BrandingManifest => ({
    brand_name: "Acme", primary_color: null, accent_color: null, background_color: null,
    foreground_color: null, color_scheme: "SYSTEM", logo_url: null, favicon_url: null,
    support_email: null, has_custom_branding: true, ...fields,
  });
  expect("FlowPilot's own blue", hexToHslTriplet("#2463eb"), "221 83.3% 53.1%");
  expect("a grey has no hue", hexToHslTriplet("#808080"), "0 0% 50.2%");
  expect("a hue past 300 degrees", hexToHslTriplet("#ff00aa"), "320 100% 50%");
  expect("white text on a dark primary", readableTextOn("#0f766e"), WHITE);
  expect("dark text on a pale primary", readableTextOn("#fde68a"), INK);
  expect("the primary sets buttons, links and focus rings",
    Object.keys(brandProperties(brand({ primary_color: "#0f766e" }))), ["--primary", "--ring", "--primary-foreground", "--primary-on-dark"]);
  expect("primary text on a dark surface keeps the hue, lightened",
    brandProperties(brand({ primary_color: "#0f766e" }))["--primary-on-dark"], "175.3 77.4% 72%");
  expect("no brand, no change", brandProperties(brand({ primary_color: "#0f766e", has_custom_branding: false })), {});
  expect("nothing but six-digit hex", brandProperties(brand({
    primary_color: "red; background: url(x)", accent_color: "#abc",
  })), {});
  return failures;
};

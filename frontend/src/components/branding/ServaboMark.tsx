import React from "react";

import { FOLD, S_STROKE, S_WIDTH, TILE } from "../../../../brand/mark";

interface ServaboMarkProps {
  className?: string;
  /** Accessible name. Empty when the mark sits next to the word "Servabo" (decorative). */
  title?: string;
}

/**
 * The Servabo mark: a document with its corner folded, holding an "S" (brand/mark.ts, the same
 * geometry the website and the favicons use). The tile takes the primary colour and the S the
 * primary foreground, so it follows the theme like every other surface.
 */
export const ServaboMark: React.FC<ServaboMarkProps> = ({ className = "", title = "" }) => (
  <svg
    viewBox="0 0 32 32"
    className={className}
    role={title ? "img" : undefined}
    aria-label={title || undefined}
    aria-hidden={title ? undefined : true}
    focusable="false"
  >
    <path d={TILE} className="fill-primary" />
    <path d={FOLD} className="fill-primary-foreground" fillOpacity={0.35} />
    <path
      d={S_STROKE}
      fill="none"
      className="stroke-primary-foreground"
      strokeWidth={S_WIDTH}
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);

export default ServaboMark;

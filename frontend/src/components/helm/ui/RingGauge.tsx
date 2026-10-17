import React from "react";

export type RingTone = "holding" | "drifting" | "breached" | "unknown" | "accent" | "aqua" | "sky";

/**
 * A ring that encodes a value (docs/helm/VISUAL.md "RingGauge"): the arc is the share, from 0 to 1,
 * swept from zero on first view. Never an empty outline: with no share (null) it draws a full ring
 * in the tone, which for an instrument means "the whole mandate holds" or "breached". A bezel of
 * twelve ticks makes it read as an instrument. A breached ring pulses softly. The ring is
 * decoration; the words next to it (or `label`) say the value.
 */
export const RingGauge: React.FC<{
  readonly value: number | null;
  readonly tone: RingTone;
  readonly size?: number;
  readonly thickness?: number;
  readonly label?: string;
  readonly pulse?: boolean;
  readonly ticks?: boolean;
  readonly children?: React.ReactNode;
  readonly className?: string;
}> = ({ value, tone, size = 56, thickness = 4, label, pulse = false, ticks = true, children, className = "" }) => {
  const centre = size / 2;
  const radius = centre - thickness / 2 - (ticks ? 4 : 1);
  const circumference = 2 * Math.PI * radius;
  const share = value === null || !Number.isFinite(value) ? 1 : Math.max(0, Math.min(1, value));
  const offset = circumference * (1 - share);
  const outer = centre - 1;
  return (
    <span
      className={`relative inline-flex shrink-0 items-center justify-center ${className}`}
      style={{ width: size, height: size }}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : "true"}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true" className="absolute inset-0 overflow-visible">
        {ticks
          ? Array.from({ length: 12 }, (_, index) => {
              const angle = (index / 12) * Math.PI * 2 - Math.PI / 2;
              const inner = outer - (index % 3 === 0 ? 3 : 1.75);
              return (
                <line
                  key={index}
                  x1={centre + Math.cos(angle) * inner}
                  y1={centre + Math.sin(angle) * inner}
                  x2={centre + Math.cos(angle) * outer}
                  y2={centre + Math.sin(angle) * outer}
                  strokeWidth={1}
                  className="helm-ring-track"
                />
              );
            })
          : null}
        <circle cx={centre} cy={centre} r={radius} fill="none" strokeWidth={thickness} className="helm-ring-track" />
        {pulse ? (
          <circle cx={centre} cy={centre} r={radius} fill="none" strokeWidth={thickness * 2.5} className={`helm-ring-${tone} helm-pulse`} />
        ) : null}
        <circle
          cx={centre}
          cy={centre}
          r={radius}
          fill="none"
          strokeWidth={thickness}
          strokeLinecap="round"
          className={`helm-ring-${tone} helm-ring-sweep`}
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          transform={`rotate(-90 ${centre} ${centre})`}
          style={{ ["--helm-ring-c" as string]: circumference }}
        />
      </svg>
      {children ? <span className="relative flex flex-col items-center justify-center text-center">{children}</span> : null}
    </span>
  );
};

export default RingGauge;

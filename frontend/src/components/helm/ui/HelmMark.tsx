import React from "react";

/**
 * The Helm mark: a ship's wheel with eight spokes. Used in the Helm sidebar's header, inside the
 * mode switch and, turning slowly, as Helm's loading indicator. Drawn in currentColor.
 */
export const HelmMark: React.FC<{
  readonly size?: number;
  readonly className?: string;
  readonly spinning?: boolean;
  readonly title?: string;
}> = ({ size = 24, className = "", spinning = false, title }) => {
  const spokes = Array.from({ length: 8 }, (_, index) => (index * Math.PI) / 4);
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      className={`${spinning ? "helm-mark-spin " : ""}${className}`}
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : "true"}
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
    >
      {spokes.map((angle) => (
        <line
          key={angle}
          x1={12 + Math.cos(angle) * 2.6}
          y1={12 + Math.sin(angle) * 2.6}
          x2={12 + Math.cos(angle) * 10.6}
          y2={12 + Math.sin(angle) * 10.6}
          strokeWidth={angle % (Math.PI / 2) === 0 ? 1.9 : 1.5}
        />
      ))}
      <circle cx="12" cy="12" r="7.1" strokeWidth="1.9" />
      <circle cx="12" cy="12" r="2.4" strokeWidth="1.6" fill="currentColor" fillOpacity="0.25" />
    </svg>
  );
};

export default HelmMark;

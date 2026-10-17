import React, { useId } from "react";

/**
 * The certificate's seal (docs/helm/VISUAL.md "CertificateSeal"): a rosette with the Guard's
 * verdict at its heart and the first eight characters of the SHA-256 fingerprint engraved around
 * it. Decoration for a fact the page also states in words, so it is hidden from screen readers.
 */
export const CertificateSeal: React.FC<{
  readonly fingerprint: string;
  readonly passed: boolean;
  readonly verified: boolean;
  readonly size?: number;
}> = ({ fingerprint, passed, verified, size = 132 }) => {
  const id = useId().replace(/:/g, "");
  const tone = passed && verified ? "holding" : "breached";
  const short = fingerprint.slice(0, 8).toUpperCase();
  const engraving = `SHA-256 · ${short} · ${passed ? "GUARD PASSED" : "GUARD REFUSED"} · ${short} · `;
  const petals = Array.from({ length: 24 }, (_, i) => (i * 360) / 24);
  return (
    <svg viewBox="0 0 120 120" width={size} height={size} aria-hidden="true" className={`helm-state-${tone} shrink-0`} data-testid="helm-certificate-seal" data-fingerprint={short}>
      <defs>
        <radialGradient id={`${id}-fill`} cx="50%" cy="40%" r="60%">
          <stop offset="0" stopColor="currentColor" stopOpacity="0.28" />
          <stop offset="1" stopColor="currentColor" stopOpacity="0.04" />
        </radialGradient>
        <path id={`${id}-arc`} d="M60,60 m-41,0 a41,41 0 1,1 82,0 a41,41 0 1,1 -82,0" />
      </defs>
      {petals.map((angle) => (
        <ellipse key={angle} cx="60" cy="8.5" rx="4.2" ry="7" fill="currentColor" fillOpacity="0.16" transform={`rotate(${angle} 60 60)`} />
      ))}
      <circle cx="60" cy="60" r="50" fill={`url(#${id}-fill)`} stroke="currentColor" strokeOpacity="0.7" strokeWidth="1.2" />
      <circle cx="60" cy="60" r="33" fill="none" stroke="currentColor" strokeOpacity="0.55" strokeWidth="0.8" strokeDasharray="1.5 2.5" />
      <text fontSize="6.4" fontWeight="700" letterSpacing="1.4" fill="currentColor" fontFamily="JetBrains Mono Variable, JetBrains Mono, monospace">
        <textPath href={`#${id}-arc`} startOffset="0">
          {engraving}
        </textPath>
      </text>
      {passed ? (
        <path d="M47 61 l9 9 l17 -19" fill="none" stroke="currentColor" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round" />
      ) : (
        <path d="M50 50 l20 20 M70 50 l-20 20" fill="none" stroke="currentColor" strokeWidth="5" strokeLinecap="round" />
      )}
    </svg>
  );
};

export default CertificateSeal;

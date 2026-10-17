import React from "react";

import type { Readback } from "@/types/helm";

/**
 * A mandate read back in plain words, its parameters as chips (brief §5.3): "Never pay an invoice
 * more than [2%] above the agreed price." The sentence is the readback; the chips are its values.
 */
export const ReadbackSentence: React.FC<{ readonly readback: Readback | null | undefined; readonly className?: string }> = ({
  readback,
  className = "",
}) => {
  if (!readback) {
    return null;
  }
  return (
    <p className={`helm-readback leading-relaxed ${className}`}>
      {readback.parts.map((part, index) =>
        part.chip !== undefined ? (
          <span
            key={index}
            className="helm-chip helm-chip-accent helm-figure mx-0.5 align-baseline"
            title={part.chip}
            style={{ ["--i" as string]: index }}
          >
            {part.display ?? String(part.value ?? "")}
          </span>
        ) : (
          <React.Fragment key={index}>{part.text}</React.Fragment>
        ),
      )}
    </p>
  );
};

export default ReadbackSentence;

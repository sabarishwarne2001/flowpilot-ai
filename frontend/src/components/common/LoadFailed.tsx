import React from "react";

/**
 * Campaign session 3 (F-314): what a list says when its request failed.
 *
 * The platform consoles read only "is the list empty", so a failed request showed "No contracts."
 * or "Nothing refused in this window.": an outage read as a fact. This says the list could not
 * be loaded and offers to ask again. Inline, so it fits a table row or a list item.
 */
export const LoadFailed: React.FC<{ readonly what: string; readonly onRetry: () => void }> = ({ what, onRetry }) => (
  <span role="alert" className="inline-flex flex-wrap items-center gap-2 text-sm text-destructive">
    {what} could not be loaded.
    <button type="button" className="underline underline-offset-2" onClick={onRetry}>
      Try again
    </button>
  </span>
);

export default LoadFailed;

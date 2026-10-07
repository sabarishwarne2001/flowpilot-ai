import React from "react";

interface EmptyStateProps {
  /**
   * Strongly typed Lucide SVG vector icon component to display as the graphic header.
   */
  readonly icon: React.ComponentType<{ readonly className?: string }>;
  /**
   * Descriptive main header title.
   */
  readonly title: string;
  /**
   * Support description text explaining why the workspace list is empty.
   */
  readonly description: string;
  /**
   * Optional text label to display inside the primary quick-action CTA button.
   */
  readonly actionText?: string;
  /**
   * Optional closure callback to execute when the CTA button is clicked.
   */
  readonly onAction?: () => void;
  readonly className?: string;
}

/**
 * Universal, stateless Empty State Presenter Card for FlowPilot AI.
 *
 * Provides consistent typography, clean vector alignment grids, and
 * supports optional interactive CTA actions natively.
 */
export const EmptyState: React.FC<EmptyStateProps> = ({
  icon: Icon,
  title,
  description,
  actionText,
  onAction,
  className = "",
}) => {
  return (
    <div
      className={`relative mx-auto flex max-w-sm flex-col items-center justify-center overflow-hidden rounded-xl border border-dashed border-border-strong/70 bg-card/60 px-8 py-10 text-center select-none animate-fade-in ${className}`}
      role="region"
      aria-label={`${title} Empty State`}
    >
      {/* Visual Vector Icon Container */}
      <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-xl border border-border bg-gradient-to-b from-muted to-muted/30 text-muted-foreground shadow-elevation-1">
        <Icon className="h-5 w-5 flex-shrink-0" />
      </div>

      {/* Main Descriptions Labels */}
      <h3 className="mb-1 text-sm font-semibold tracking-tight text-foreground">
        {title}
      </h3>
      <p className="mb-5 max-w-[280px] text-[13px] leading-relaxed text-muted-foreground">
        {description}
      </p>

      {/* Optional CTA Engagement Button */}
      {actionText && onAction && (
        <button
          type="button"
          onClick={onAction}
          className="fp-btn fp-btn-primary text-[13px]"
        >
          {actionText}
        </button>
      )}
    </div>
  );
};

export default EmptyState;

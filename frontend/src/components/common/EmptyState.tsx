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
      className={`flex flex-col items-center justify-center text-center p-8 bg-card border border-border/60 dark:border-border/40 rounded-xl max-w-sm mx-auto shadow-sm select-none animate-fade-in ${className}`}
      role="region"
      aria-label={`${title} Empty State`}
    >
      {/* Visual Vector Icon Container */}
      <div className="p-4 bg-primary/10 text-primary rounded-full mb-4">
        <Icon className="h-7 w-7 flex-shrink-0" />
      </div>

      {/* Main Descriptions Labels */}
      <h3 className="text-sm font-extrabold tracking-tight text-foreground/90 font-sans mb-1.5">
        {title}
      </h3>
      <p className="text-xs text-muted-foreground font-semibold leading-relaxed mb-5 max-w-[280px]">
        {description}
      </p>

      {/* Optional CTA Engagement Button */}
      {actionText && onAction && (
        <button
          type="button"
          onClick={onAction}
          className="inline-flex items-center px-4 py-2 bg-primary text-primary-foreground font-bold text-xs rounded-lg hover:bg-primary/95 transition-all shadow-sm active:scale-[0.98] outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        >
          {actionText}
        </button>
      )}
    </div>
  );
};

export default EmptyState;

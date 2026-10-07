import { Info } from "lucide-react";
import Tooltip from "./Tooltip";

interface InfoTooltipProps {
  title: string;
  description: string;
  recommended?: string | undefined;
}

export default function InfoTooltip({
  title,
  description,
  recommended,
}: InfoTooltipProps) {
  return (
    <Tooltip
      content={
        <div className="space-y-3">
          <div>
            <h4 className="font-semibold text-foreground">{title}</h4>

            <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
              {description}
            </p>
          </div>

          {recommended && (
            <div className="rounded-md border border-primary/25 bg-primary/[0.08] p-2">
              <p className="text-xs font-medium text-primary dark:text-[hsl(213_94%_72%)]">
                Recommended
              </p>

              <p className="mt-1 text-xs text-foreground/80">
                {recommended}
              </p>
            </div>
          )}
        </div>
      }
    >
      <button
        type="button"
        className="ml-2 inline-flex h-6 w-6 items-center justify-center rounded-full text-muted-foreground transition-colors hover:text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
        aria-label={`More information about ${title}`}
      >
        <Info size={15} strokeWidth={2.25} />
      </button>
    </Tooltip>
  );
}

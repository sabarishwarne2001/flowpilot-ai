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
            <h4 className="font-semibold text-white">{title}</h4>

            <p className="mt-1 text-sm leading-relaxed text-slate-300">
              {description}
            </p>
          </div>

          {recommended && (
            <div className="rounded-md border border-blue-700/40 bg-blue-950/40 p-2">
              <p className="text-xs font-medium text-blue-300">
                Recommended
              </p>

              <p className="mt-1 text-xs text-blue-200">
                {recommended}
              </p>
            </div>
          )}
        </div>
      }
    >
      <button
        type="button"
        className="ml-2 inline-flex h-6 w-6 items-center justify-center rounded-full text-slate-400 transition-colors hover:text-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-500"
        aria-label={`More information about ${title}`}
      >
        <Info size={15} strokeWidth={2.25} />
      </button>
    </Tooltip>
  );
}

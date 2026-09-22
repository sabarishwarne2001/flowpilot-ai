/**
 * HARDENING-T2:Phase3 — an (i) help affordance shared by settings forms.
 *
 * Portalled and positioned by floating-ui, so a card's overflow cannot clip
 * it (the D7 lesson). Reachable by keyboard focus as well as hover.
 */
import React, { useState } from "react";
import { Info } from "lucide-react";
import {
  FloatingPortal,
  autoUpdate,
  flip,
  offset,
  shift,
  useFloating,
  useFocus,
  useHover,
  useInteractions,
} from "@floating-ui/react";

interface InfoTooltipProps {
  readonly label: string;
  readonly children: React.ReactNode;
}

export const InfoTooltip: React.FC<InfoTooltipProps> = ({ label, children }) => {
  const [open, setOpen] = useState(false);
  const { refs, floatingStyles, context } = useFloating({
    open,
    onOpenChange: setOpen,
    placement: "top",
    middleware: [offset(8), flip(), shift({ padding: 8 })],
    whileElementsMounted: autoUpdate,
  });
  const { getReferenceProps, getFloatingProps } = useInteractions([
    useHover(context, { move: false }),
    useFocus(context),
  ]);
  return (
    <>
      <button
        type="button"
        ref={refs.setReference}
        {...getReferenceProps()}
        aria-label={`About ${label}`}
        className="inline-flex h-4 w-4 items-center justify-center rounded-full text-muted-foreground transition hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
      >
        <Info className="h-3.5 w-3.5" aria-hidden="true" />
      </button>
      {open && (
        <FloatingPortal>
          <div
            ref={refs.setFloating}
            style={floatingStyles}
            {...getFloatingProps()}
            role="tooltip"
            className="z-[70] max-w-xs rounded-md border border-border bg-popover px-3 py-2 text-xs leading-snug text-popover-foreground shadow-xl"
          >
            {children}
          </div>
        </FloatingPortal>
      )}
    </>
  );
};

export default InfoTooltip;

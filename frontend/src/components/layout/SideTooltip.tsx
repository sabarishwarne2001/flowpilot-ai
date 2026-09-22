/**
 * HARDENING-T1:D7 — tooltips for the collapsed sidebar.
 *
 * The previous tooltip was an absolutely positioned <span> inside the nav,
 * whose scroll container is `overflow-y-auto overflow-x-hidden`. Any
 * overflow other than `visible` clips absolutely positioned descendants, so
 * the tooltip was always cut off and never seen. This one renders in a
 * portal, positioned by floating-ui (already a dependency), so no ancestor's
 * overflow can clip it.
 */
import React, { useState } from "react";
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

interface SideTooltipProps {
  readonly label: string;
  readonly children: React.ReactNode;
}

export const SideTooltip: React.FC<SideTooltipProps> = ({ label, children }) => {
  const [open, setOpen] = useState(false);
  const { refs, floatingStyles, context } = useFloating({
    open,
    onOpenChange: setOpen,
    placement: "right",
    middleware: [offset(10), flip(), shift({ padding: 8 })],
    whileElementsMounted: autoUpdate,
  });
  const hover = useHover(context, { move: false });
  const focus = useFocus(context);
  const { getReferenceProps, getFloatingProps } = useInteractions([hover, focus]);

  return (
    <>
      <span ref={refs.setReference} {...getReferenceProps()} className="block">
        {children}
      </span>
      {open && (
        <FloatingPortal>
          <div
            ref={refs.setFloating}
            style={floatingStyles}
            {...getFloatingProps()}
            role="tooltip"
            className="pointer-events-none z-[70] whitespace-nowrap rounded-md border border-border bg-popover px-2.5 py-1.5 text-xs font-semibold text-popover-foreground shadow-xl"
          >
            {label}
          </div>
        </FloatingPortal>
      )}
    </>
  );
};

export default SideTooltip;

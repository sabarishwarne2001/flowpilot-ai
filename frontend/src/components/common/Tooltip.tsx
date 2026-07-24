import { useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  autoUpdate,
  flip,
  offset,
  shift,
  arrow,
  useFloating,
  useHover,
  useFocus,
  useDismiss,
  useInteractions,
  useRole,
  FloatingArrow,
} from "@floating-ui/react";

interface TooltipProps {
  content: ReactNode;
  children: ReactNode;
}

export default function Tooltip({ content, children }: TooltipProps) {
  const [isVisible, setIsVisible] = useState(false);
  const arrowRef = useRef<SVGSVGElement | null>(null);

  const { refs, floatingStyles, context } = useFloating({
    open: isVisible,
    onOpenChange: setIsVisible,
    placement: "top",
    whileElementsMounted: autoUpdate,
    middleware: [
      offset(10),
      flip(),
      shift({ padding: 8 }),
      arrow({ element: arrowRef }),
    ],
  });

  const hover = useHover(context);
  const focus = useFocus(context);
  const dismiss = useDismiss(context);
  const role = useRole(context, {
    role: "tooltip",
  });

  const { getReferenceProps, getFloatingProps } = useInteractions([
    hover,
    focus,
    dismiss,
    role,
  ]);

  return (
    <div
      ref={refs.setReference}
      className="relative inline-flex"
      {...getReferenceProps()}
    >
      {children}

      {isVisible && (
        <div
          ref={refs.setFloating}
          style={floatingStyles}
          {...getFloatingProps()}
          className="
            z-50
            w-72
            rounded-xl
            border
            border-slate-700/80
            bg-slate-900/95
            backdrop-blur-md
            p-4
            shadow-2xl
            text-sm
            leading-relaxed
            text-slate-200
          "
        >
          <div>{content}</div>

          <FloatingArrow
            ref={arrowRef}
            context={context}
            className="fill-slate-900 stroke-slate-700"
          />
        </div>
      )}
    </div>
  );
}

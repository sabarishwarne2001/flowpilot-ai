/**
 * ARCH-29 Tranche 3 — a menu that escapes its scroll container.
 *
 * THE PROBLEM
 * ===========
 *
 * The conversation menu in `Assistant.tsx` was `absolute right-0 top-6 z-50`
 * inside a `relative` wrapper, nested in the conversation list at line 334:
 *
 *     <div className="flex-1 overflow-y-auto space-y-1.5 pr-1 min-h-0">
 *
 * `overflow-y-auto` establishes a CLIPPING CONTEXT. Absolutely-positioned
 * descendants are clipped to it, and `z-index` cannot help — z-index orders
 * paint within a stacking context, it does not exempt an element from being
 * clipped by an ancestor's overflow. Bumping `z-50` to `z-[9999]` is the
 * intuitive fix and does nothing at all.
 *
 * Raising the menu out of the DOM subtree is the only fix. `createPortal`
 * renders it under `document.body`, where no ancestor clips it, and `position:
 * fixed` coordinates derived from the trigger's bounding rect keep it visually
 * attached.
 *
 * WHY NOT RADIX
 * =============
 *
 * `@radix-ui/react-dropdown-menu` solves this properly and is the right answer
 * for a design-system rollout. It is not in `package.json` — only
 * `react-select` is — and adding a dependency to fix one clipped menu is a
 * larger decision than this tranche should make unilaterally. This component is
 * ~120 lines with no new dependencies and can be replaced wholesale when the
 * design system lands.
 *
 * WHY THE POSITION IS RECOMPUTED ON SCROLL AND RESIZE
 * ===================================================
 *
 * `position: fixed` is relative to the viewport, so a menu anchored to a row
 * inside a scrolling list detaches the moment the list scrolls — it stays put
 * while its trigger moves away. The listeners are registered in the CAPTURE
 * phase because the scroll event that matters is on the inner list, and scroll
 * does not bubble.
 *
 * FLIPPING
 * ========
 *
 * A trigger near the bottom of the viewport would otherwise open a menu that
 * runs off-screen — trading a clipped menu for an unreachable one. When there
 * is not enough room below, it opens upward.
 */

import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

interface PortalMenuProps {
  /** The element the menu is anchored to. */
  readonly anchorRef: React.RefObject<HTMLElement | null>;
  readonly open: boolean;
  readonly onClose: () => void;
  readonly children: React.ReactNode;
  /** Menu width in px. Fixed so the position can be computed before paint. */
  readonly width?: number;
}

interface Position {
  readonly top: number;
  readonly left: number;
}

const GAP = 4;
const ESTIMATED_HEIGHT = 88;

export const PortalMenu: React.FC<PortalMenuProps> = ({
  anchorRef,
  open,
  onClose,
  children,
  width = 144,
}) => {
  const [position, setPosition] = useState<Position | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  const reposition = useCallback((): void => {
    const anchor = anchorRef.current;
    if (!anchor) {
      return;
    }
    const rect = anchor.getBoundingClientRect();

    // Right-align with the trigger, then clamp into the viewport so a menu
    // anchored near the left edge does not open at a negative offset.
    const left = Math.max(GAP, Math.min(rect.right - width, window.innerWidth - width - GAP));

    const spaceBelow = window.innerHeight - rect.bottom;
    const top =
      spaceBelow < ESTIMATED_HEIGHT + GAP
        ? rect.top - ESTIMATED_HEIGHT - GAP
        : rect.bottom + GAP;

    setPosition({ top, left });
  }, [anchorRef, width]);

  // Layout effect, not effect: position must be known before the browser
  // paints, or the menu is visible for one frame at the top-left corner.
  useLayoutEffect(() => {
    if (open) {
      reposition();
    } else {
      setPosition(null);
    }
  }, [open, reposition]);

  useEffect(() => {
    if (!open) {
      return;
    }

    const handleScrollOrResize = (): void => reposition();

    const handlePointerDown = (event: MouseEvent): void => {
      const target = event.target as Node;
      if (menuRef.current?.contains(target)) {
        return;
      }
      if (anchorRef.current?.contains(target)) {
        // The trigger handles its own toggle; closing here too would make the
        // second click reopen the menu it just closed.
        return;
      }
      onClose();
    };

    const handleKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        onClose();
      }
    };

    // Capture phase: scroll does not bubble, and the container that scrolls is
    // an inner div rather than the window.
    window.addEventListener("scroll", handleScrollOrResize, true);
    window.addEventListener("resize", handleScrollOrResize);
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKey);

    return () => {
      window.removeEventListener("scroll", handleScrollOrResize, true);
      window.removeEventListener("resize", handleScrollOrResize);
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKey);
    };
  }, [open, onClose, reposition, anchorRef]);

  if (!open || position === null || typeof document === "undefined") {
    return null;
  }

  return createPortal(
    <div
      ref={menuRef}
      role="menu"
      style={{ top: position.top, left: position.left, width }}
      className="fixed z-[100] rounded-lg border border-border bg-card p-1 shadow-lg"
      onClick={(event) => event.stopPropagation()}
    >
      {children}
    </div>,
    document.body,
  );
};

export default PortalMenu;

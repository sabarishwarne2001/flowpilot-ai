/**
 * The pointer spotlight (docs/helm/VISUAL.md "Glass panel"): one delegated, rAF-throttled listener
 * on the Helm shell sets --mx and --my on the glass panel (or table row) under the pointer, so its
 * spotlight and border sheen follow the cursor. One listener for the whole page, not one per panel;
 * nothing runs while the pointer is still, and nothing on touch.
 */
import { useEffect, type RefObject } from "react";

const TARGETS = ".helm-panel, .helm-table tbody tr";

export const useSpotlight = (root: RefObject<HTMLElement | null>): void => {
  useEffect(() => {
    const host = root.current;
    if (!host) {
      return undefined;
    }
    let frame = 0;
    let last: PointerEvent | null = null;
    const apply = (): void => {
      frame = 0;
      const event = last;
      if (!event || !(event.target instanceof Element)) {
        return;
      }
      const target = event.target.closest<HTMLElement>(TARGETS);
      if (!target || !host.contains(target)) {
        return;
      }
      const box = target.getBoundingClientRect();
      target.style.setProperty("--mx", `${Math.round(event.clientX - box.left)}px`);
      target.style.setProperty("--my", `${Math.round(event.clientY - box.top)}px`);
    };
    const onMove = (event: PointerEvent): void => {
      if (event.pointerType === "touch") {
        return;
      }
      last = event;
      if (!frame) {
        frame = requestAnimationFrame(apply);
      }
    };
    host.addEventListener("pointermove", onMove, { passive: true });
    return () => {
      host.removeEventListener("pointermove", onMove);
      if (frame) {
        cancelAnimationFrame(frame);
      }
    };
  }, [root]);
};

/**
 * Night Bridge motion hooks (docs/helm/VISUAL.md "Motion"): whether the person asked for reduced
 * motion, and a count-up that always ends on the exact value it was given.
 */
import { useEffect, useRef, useState } from "react";

const QUERY = "(prefers-reduced-motion: reduce)";

const prefersReduced = (): boolean => {
  try {
    return typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia(QUERY).matches;
  } catch {
    return false;
  }
};

/** True while the person's system asks for reduced motion; follows changes live. */
export const useReducedMotion = (): boolean => {
  const [reduced, setReduced] = useState(prefersReduced);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") {
      return undefined;
    }
    const media = window.matchMedia(QUERY);
    const onChange = (): void => setReduced(media.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);
  return reduced;
};

/** Reduced motion, read once outside React (navigation decides with it). */
export const reducedMotionNow = prefersReduced;

const easeOutExpo = (t: number): number => (t >= 1 ? 1 : 1 - 2 ** (-10 * t));

/**
 * A number that counts up to `value` over `duration` ms on first view, and glides from the old
 * value to a new one when it changes (a refetch with the same value does nothing). The last frame
 * is always exactly `value`, never a rounding of it. Under reduced motion it is `value` at once.
 */
export const useCountUp = (value: number, duration = 900): number => {
  const reduced = useReducedMotion();
  const [shown, setShown] = useState(reduced ? value : 0);
  const from = useRef(reduced ? value : 0);
  useEffect(() => {
    if (reduced || !Number.isFinite(value)) {
      from.current = value;
      setShown(value);
      return undefined;
    }
    const start = from.current;
    if (start === value) {
      setShown(value);
      return undefined;
    }
    let frame = 0;
    const began = performance.now();
    const tick = (now: number): void => {
      const t = Math.min(1, (now - began) / duration);
      if (t >= 1) {
        from.current = value;
        setShown(value);
        return;
      }
      const next = start + (value - start) * easeOutExpo(t);
      from.current = next;
      setShown(next);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(frame);
    };
  }, [duration, reduced, value]);
  return shown;
};

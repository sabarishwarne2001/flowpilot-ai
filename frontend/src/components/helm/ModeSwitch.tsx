import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Lock } from "lucide-react";

import { CAPABILITY } from "@/constants/capabilities";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { helmPath, isHelmPath } from "@/routes/tenantPaths";
import { readLastPath, writeLastPath, writeMode, type HelmMode } from "@/components/helm/modeMemory";
import { prefetchHelm } from "@/components/helm/helmPrefetch";
import { HelmMark } from "@/components/helm/ui/HelmMark";
import { reducedMotionNow } from "@/components/helm/ui/motion";
import "@/styles/helm.css";

interface ModeSwitchProps {
  readonly mode: HelmMode;
  readonly organizationId: string;
  readonly orgSlug: string;
  /** Where Documents goes when no page is remembered (the workspace root). */
  readonly documentsPath: string;
}

const OPTIONS: readonly { mode: HelmMode; label: string }[] = [
  { mode: "documents", label: "Documents" },
  { mode: "helm", label: "Helm" },
];

/** How long the thumb glides before the page changes (it finishes during the transition). */
const GLIDE_MS = 240;
/** The longest the switch waits for Helm's code before it navigates anyway. */
const PREFETCH_CAP_MS = 600;

const DISCOVERED_KEY = "helm.discovered";
/** Set while the switch hands focus to its twin in the other mode's header. */
const FOCUS_KEY = "helm.switch.focus";

const readFlag = (store: "local" | "session", key: string): boolean => {
  try {
    return (store === "local" ? window.localStorage : window.sessionStorage).getItem(key) === "1";
  } catch {
    return false;
  }
};

const writeFlag = (store: "local" | "session", key: string, on: boolean): void => {
  try {
    const target = store === "local" ? window.localStorage : window.sessionStorage;
    if (on) {
      target.setItem(key, "1");
    } else {
      target.removeItem(key);
    }
  } catch {
    // A convenience: without storage the switch still works.
  }
};

const supportsViewTransitions = (): boolean =>
  typeof document !== "undefined" && typeof (document as Document & { startViewTransition?: unknown }).startViewTransition === "function";

type ViewTransition = { finished: Promise<void>; ready: Promise<void>; updateCallbackDone: Promise<void> };
type TransitionDocument = Document & { startViewTransition: (callback: () => Promise<void> | void) => ViewTransition };

/** The longest the transition holds the old picture while the other mode's page renders. */
const ARRIVAL_CAP_MS = 1_000;

/**
 * The other mode's switch announces it has rendered (the page around it is on screen), so the
 * transition can reveal the real page. One pending arrival at a time.
 */
let arrival: { mode: HelmMode; resolve: () => void } | null = null;

const waitForArrival = (mode: HelmMode): Promise<void> =>
  new Promise<void>((resolve) => {
    const timer = window.setTimeout(() => {
      arrival = null;
      resolve();
    }, ARRIVAL_CAP_MS);
    arrival = {
      mode,
      resolve: () => {
        window.clearTimeout(timer);
        arrival = null;
        resolve();
      },
    };
  });

const clearTransition = (): void => {
  const root = document.documentElement;
  root.removeAttribute("data-helm-transition");
  root.style.removeProperty("--helm-vt-x");
  root.style.removeProperty("--helm-vt-y");
};

/**
 * Documents | Helm (brief §10.1, Night Bridge in docs/helm/VISUAL.md). A radio group to assistive
 * technology: arrow keys, Home and End move the choice, one stop in the tab order. Shown only to
 * owners, admins and the billing role (the caller decides). Without `capability.helm` the Helm option
 * carries a lock and opens Helm preview. Remembers the last mode per browser and the last page in
 * each mode.
 *
 * The look: a glass track with a thumb that glides to the chosen mode (measured with a
 * ResizeObserver); in Helm the thumb is the signal gradient and the Helm mark turns as it arrives.
 * The change of mode is a view transition: Helm is revealed by a circle growing from the switch, and
 * collapses back into it on the way out. The switch starts it itself: the app's pages are descendant
 * <Routes>, where React Router's navigate() drops its viewTransition option. The new picture is
 * taken once the other mode's switch has rendered (at most a second later). The pointer entering the switch (or focus) prefetches
 * Helm's code and overview, and the switch waits up to 600 ms for them, so the reveal shows the
 * real page. Without View Transitions it navigates plainly; under reduced motion it never starts one.
 */
export const ModeSwitch: React.FC<ModeSwitchProps> = ({ mode, organizationId, orgSlug, documentsPath }) => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { pathname, search } = useLocation();
  const { granted, isLoading } = useCapabilityAccess(organizationId, CAPABILITY.helm);
  const refs = useRef<Record<HelmMode, HTMLButtonElement | null>>({ documents: null, helm: null });
  const track = useRef<HTMLDivElement>(null);
  const [shown, setShown] = useState<HelmMode>(mode);
  const [thumb, setThumb] = useState<{ x: number; w: number } | null>(null);
  const [animate, setAnimate] = useState(false);
  const [discovered, setDiscovered] = useState(() => mode === "helm" || readFlag("local", DISCOVERED_KEY));
  const busy = useRef(false);

  useEffect(() => setShown(mode), [mode]);

  // This switch is on screen in its mode: a transition waiting for this page can take its picture.
  useLayoutEffect(() => {
    if (arrival?.mode === mode) {
      arrival.resolve();
    }
  }, [mode]);

  useEffect(() => {
    if (mode === "helm") {
      writeFlag("local", DISCOVERED_KEY, true);
      setDiscovered(true);
    }
  }, [mode]);

  // The twin in the other header takes focus when the person switched from the keyboard or a click.
  useEffect(() => {
    if (readFlag("session", FOCUS_KEY)) {
      writeFlag("session", FOCUS_KEY, false);
      refs.current[mode]?.focus({ preventScroll: true });
    }
  }, [mode]);

  // Remember where this person is in the current mode.
  useEffect(() => {
    writeMode(mode);
    const here = `${pathname}${search}`;
    const ours = pathname === `/${orgSlug}` || pathname.startsWith(`/${orgSlug}/`) ||
      pathname.startsWith(`/${encodeURIComponent(orgSlug)}/`);
    if (ours && (mode === "helm") === isHelmPath(pathname)) {
      writeLastPath(organizationId, mode, here);
    }
  }, [mode, organizationId, orgSlug, pathname, search]);

  // Measure where the thumb goes: the shown option's box inside the track.
  const measure = useCallback(() => {
    const option = refs.current[shown];
    const host = track.current;
    if (!option || !host) {
      return;
    }
    setThumb({ x: option.offsetLeft - 3, w: option.offsetWidth });
  }, [shown]);

  useLayoutEffect(() => {
    measure();
  }, [measure]);

  useEffect(() => {
    const host = track.current;
    if (!host || typeof ResizeObserver === "undefined") {
      return undefined;
    }
    const observer = new ResizeObserver(() => measure());
    observer.observe(host);
    Object.values(refs.current).forEach((node) => node && observer.observe(node));
    return () => observer.disconnect();
  }, [measure]);

  // Glide only after the first placement, so a freshly mounted switch does not slide in from zero.
  useEffect(() => {
    if (thumb && !animate) {
      const frame = requestAnimationFrame(() => setAnimate(true));
      return () => cancelAnimationFrame(frame);
    }
    return undefined;
  }, [thumb, animate]);

  const targetOf = useCallback(
    (next: HelmMode): string => {
      const remembered = readLastPath(organizationId, next);
      const helmRoot = helmPath(orgSlug);
      const valid =
        remembered !== null &&
        (next === "helm" ? remembered.startsWith(helmRoot) : !isHelmPath(remembered.split("?")[0] ?? "") &&
          remembered.startsWith(`/${encodeURIComponent(orgSlug)}/`));
      return valid && remembered ? remembered : next === "helm" ? helmRoot : documentsPath;
    },
    [documentsPath, organizationId, orgSlug],
  );

  const warm = useCallback((): Promise<unknown> => {
    const to = targetOf("helm").split("?")[0] ?? "";
    const page = to.slice(helmPath(orgSlug).length).replace(/^\/+/, "").split("/")[0] ?? "";
    return prefetchHelm(queryClient, organizationId, page);
  }, [organizationId, orgSlug, queryClient, targetOf]);

  const go = useCallback(
    async (next: HelmMode) => {
      if (next === mode || busy.current) {
        return;
      }
      busy.current = true;
      writeMode(next);
      setShown(next);
      const to = targetOf(next);
      const switchHasFocus = track.current?.contains(document.activeElement) ?? false;
      writeFlag("session", FOCUS_KEY, switchHasFocus);
      const wait = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));
      const reduced = reducedMotionNow();
      // Entering Helm: its code and overview first (capped), while the thumb glides.
      await Promise.all([
        next === "helm" ? Promise.race([warm(), wait(PREFETCH_CAP_MS)]) : Promise.resolve(),
        reduced ? Promise.resolve() : wait(GLIDE_MS),
      ]);
      busy.current = false;
      if (reduced || !supportsViewTransitions()) {
        navigate(to);
        return;
      }
      const box = track.current?.getBoundingClientRect();
      const root = document.documentElement;
      if (box) {
        root.style.setProperty("--helm-vt-x", `${Math.round(box.left + box.width / 2)}px`);
        root.style.setProperty("--helm-vt-y", `${Math.round(box.top + box.height / 2)}px`);
      }
      root.setAttribute("data-helm-transition", next === "helm" ? "enter" : "leave");
      const arrived = waitForArrival(next);
      const transition = (document as TransitionDocument).startViewTransition(async () => {
        navigate(to);
        await arrived;
      });
      transition.ready.catch(() => undefined);
      transition.updateCallbackDone.catch(() => undefined);
      void transition.finished.finally(clearTransition);
    },
    [mode, navigate, targetOf, warm],
  );

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>): void => {
    if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) {
      return;
    }
    event.preventDefault();
    const next: HelmMode =
      event.key === "Home" ? "documents" : event.key === "End" ? "helm" : mode === "helm" ? "documents" : "helm";
    refs.current[next]?.focus();
    void go(next);
  };

  const locked = !isLoading && !granted;

  return (
    <div
      ref={track}
      role="radiogroup"
      aria-label="Mode"
      className="helm-mode-switch"
      data-mode={shown}
      data-ready={thumb ? "true" : undefined}
      data-animate={animate ? "true" : undefined}
      style={thumb ? ({ ["--thumb-x" as string]: `${thumb.x}px`, ["--thumb-w" as string]: `${thumb.w}px` } as React.CSSProperties) : undefined}
      onKeyDown={onKeyDown}
      onPointerEnter={() => void warm()}
      onFocus={() => void warm()}
      data-testid="helm-mode-switch"
    >
      <span className="helm-mode-thumb" aria-hidden="true" />
      {OPTIONS.map((option) => {
        const checked = option.mode === mode;
        return (
          <button
            key={option.mode}
            ref={(node) => {
              refs.current[option.mode] = node;
            }}
            type="button"
            role="radio"
            aria-checked={checked}
            tabIndex={checked ? 0 : -1}
            className="helm-mode-option"
            onClick={() => void go(option.mode)}
            data-testid={`mode-${option.mode}`}
          >
            {option.mode === "helm" ? <HelmMark className="helm-mode-mark" size={15} /> : null}
            <span className={option.mode === "helm" && !discovered ? "helm-mode-discover" : undefined}>{option.label}</span>
            {option.mode === "helm" && locked ? (
              <>
                <Lock className="h-3 w-3" aria-hidden="true" />
                <span className="sr-only"> (preview on your plan)</span>
              </>
            ) : null}
          </button>
        );
      })}
    </div>
  );
};

export default ModeSwitch;

/**
 * The guided Helm tour on the sample company (owner addition C): the instruments, the flight path,
 * the plan and its Guard, a certificate, the Time machine, then the way to start on your own
 * documents. For demos and first-time users.
 *
 * The step lives in the address (`?tour=2`), so a demo can be reloaded or shared mid-tour and the
 * browser's back button walks back through it. Each step points at one part of a page (an element
 * marked `data-tour="…"`): it scrolls into view and is outlined. The panel is a non-modal dialog
 * (the page stays usable); Escape or "End the tour" closes it. Only on the sample company's book.
 */
import React, { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { Compass, X } from "lucide-react";

import { useHelm } from "@/components/helm/HelmContext";
import { helmPath, type HelmPage } from "@/routes/tenantPaths";

interface Step {
  readonly page: HelmPage;
  /** The `data-tour` mark of the part of the page the step explains; none for the last step. */
  readonly target: string | null;
  readonly title: string;
  readonly body: string;
}

export const TOUR_STEPS: readonly Step[] = [
  {
    page: "",
    target: "instruments",
    title: "The instruments",
    body:
      "Each instrument is one mandate: an outcome or a limit the company stated, read back in words. Holding, " +
      "drifting or breached comes from the documents Helm has read, never from a guess; where Helm cannot see " +
      "enough it says unknown.",
  },
  {
    page: "",
    target: "flight",
    title: "The flight path",
    body:
      "Cash over the coming weeks if the plan runs, with the floor you set. Look back replays the company's past " +
      "from its own documents.",
  },
  {
    page: "",
    target: "plan",
    title: "The plan, and the Guard",
    body:
      "What Helm proposes: pay, hold, chase, reconcile. Every action passed the Guard's checks before you see it, and " +
      "is checked again when it runs. In Propose mode nothing happens until a person approves. Open \"Why, and what " +
      "the Guard checked\" on any line.",
  },
  {
    page: "certificates",
    target: "certificates",
    title: "The Guard's certificate",
    body:
      "Every action carries a certificate: the facts it relied on, the mandate versions in force and each check the " +
      "Guard made, with its SHA-256 fingerprint. Open one to read it; it can never be changed afterwards.",
  },
  {
    page: "time-machine",
    target: "time-machine",
    title: "The Time machine",
    body:
      "Replay months of the company's documents as if Helm had been on: what it would have held, each amount marked " +
      "confirmed, likely or potential. Nothing is executed in a replay.",
  },
  {
    page: "",
    target: null,
    title: "Your turn",
    body:
      "That is Helm on the sample company. Set it up on your own workspace: it reads the documents you already have " +
      "and starts in Propose mode, so nothing runs without you.",
  },
];

const TOUR_PARAM = "tour";

/** A Helm address with the selected book and this tour step. */
export const useTourPath = (): ((step: number) => string) => {
  const { orgSlug, withBook } = useHelm();
  return (step: number) => {
    const path = withBook(helmPath(orgSlug, TOUR_STEPS[step]?.page ?? ""));
    return `${path}${path.includes("?") ? "&" : "?"}${TOUR_PARAM}=${step}`;
  };
};

/** The button that starts the tour (shown on the sample company's cockpit). */
export const StartTour: React.FC = () => {
  const path = useTourPath();
  return (
    <Link to={path(0)} className="helm-btn helm-btn-hero" data-testid="helm-tour-start">
      <Compass className="h-3.5 w-3.5" aria-hidden="true" />
      Take the guided tour
    </Link>
  );
};

export const HelmTour: React.FC = () => {
  const { book, orgSlug, withBook } = useHelm();
  const [params] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const path = useTourPath();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const raw = params.get(TOUR_PARAM);
  const index = raw !== null && /^\d+$/.test(raw) ? Number(raw) : -1;
  const step = book?.sample ? TOUR_STEPS[index] : undefined;

  const end = (): void => {
    const next = new URLSearchParams(params);
    next.delete(TOUR_PARAM);
    const query = next.toString();
    navigate(`${location.pathname}${query ? `?${query}` : ""}`, { replace: true });
  };

  // The glowing ring: one element that glides from target to target, following scroll and resize.
  const [ring, setRing] = useState<{ x: number; y: number; w: number; h: number } | null>(null);

  // Point at the step's part of the page once it has rendered (data arrives after the route does).
  useEffect(() => {
    if (!step) {
      setRing(null);
      return undefined;
    }
    headingRef.current?.focus();
    if (!step.target) {
      setRing(null);
      return undefined;
    }
    let marked: Element | null = null;
    let tries = 0;
    let frame = 0;
    const place = (): void => {
      frame = 0;
      if (!marked) {
        return;
      }
      const box = marked.getBoundingClientRect();
      setRing({ x: box.left - 6, y: box.top - 6, w: box.width + 12, h: box.height + 12 });
    };
    const follow = (): void => {
      if (!frame) {
        frame = requestAnimationFrame(place);
      }
    };
    const find = (): boolean => {
      const found = document.querySelector(`[data-tour="${step.target}"]`);
      if (!found) {
        return false;
      }
      marked = found;
      found.classList.add("helm-tour-target");
      found.scrollIntoView({ block: "center", behavior: "smooth" });
      follow();
      return true;
    };
    window.addEventListener("scroll", follow, true);
    window.addEventListener("resize", follow);
    const timer = find()
      ? undefined
      : window.setInterval(() => {
          tries += 1;
          if (find() || tries > 40) {
            window.clearInterval(timer);
          }
        }, 150);
    return () => {
      if (timer !== undefined) {
        window.clearInterval(timer);
      }
      if (frame) {
        cancelAnimationFrame(frame);
      }
      window.removeEventListener("scroll", follow, true);
      window.removeEventListener("resize", follow);
      (marked as Element | null)?.classList.remove("helm-tour-target");
    };
  }, [step, location.pathname]);

  useEffect(() => {
    if (!step) {
      return undefined;
    }
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        end();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (!step) {
    return null;
  }
  const last = index === TOUR_STEPS.length - 1;
  return (
    <>
    {ring ? (
      <div
        className="helm-tour-ring"
        aria-hidden="true"
        style={{ left: 0, top: 0, width: ring.w, height: ring.h, transform: `translate(${ring.x}px, ${ring.y}px)` }}
        data-testid="helm-tour-ring"
      />
    ) : null}
    <section
      role="dialog"
      aria-modal="false"
      aria-labelledby="helm-tour-title"
      aria-describedby="helm-tour-body"
      className="helm-tour helm-panel helm-enter fixed inset-x-3 bottom-3 z-40 p-5 sm:inset-x-auto sm:right-6 sm:bottom-6 sm:w-[25rem]"
      data-testid="helm-tour"
    >
      <div className="flex items-start justify-between gap-2">
        <p className="helm-label">
          Guided tour · step <span className="helm-figure">{index + 1}</span> of{" "}
          <span className="helm-figure">{TOUR_STEPS.length}</span>
        </p>
        <button type="button" className="helm-muted rounded p-1 hover:underline" onClick={end} aria-label="End the tour">
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
      <h2 id="helm-tour-title" ref={headingRef} tabIndex={-1} className="mt-1 text-base font-semibold outline-none">
        {step.title}
      </h2>
      <p id="helm-tour-body" className="mt-1 text-sm">
        {step.body}
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {index > 0 ? (
          <Link to={path(index - 1)} className="helm-btn">
            Back
          </Link>
        ) : null}
        {last ? (
          <>
            <Link to={withBook(helmPath(orgSlug, "start"))} className="helm-btn helm-btn-primary">
              Set up Helm on my documents
            </Link>
            <button type="button" className="helm-btn" onClick={end}>
              End the tour
            </button>
          </>
        ) : (
          <Link to={path(index + 1)} className="helm-btn helm-btn-primary">
            Next: {TOUR_STEPS[index + 1]?.title}
          </Link>
        )}
      </div>
    </section>
    </>
  );
};

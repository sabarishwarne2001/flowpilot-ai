import React, { useCallback, useId, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import type { LucideIcon } from "lucide-react";

/**
 * Phase 3 — tabs that behave like tabs.
 *
 * The tabbed consoles (identity, analytics, revenue operations, unit economics, the sovereign
 * console) kept the open tab in component state: a reload, a shared link or the back button
 * always landed on the first tab, and the tab bars ignored the arrow keys a `tablist` promises
 * (WAI-ARIA Authoring Practices, "Tabs"). This is the one implementation they share:
 *
 *   - the open tab lives in the URL (`?tab=`; the first tab is the bare URL), replaced rather
 *     than pushed for each change so Back leaves the page instead of walking every tab;
 *   - Left / Right / Home / End move between tabs and open them, with a roving tabindex so Tab
 *     leaves the bar in one press;
 *   - every tab names the panel it controls, and the panel names its tab.
 */

export interface TabDefinition<T extends string> {
  readonly id: T;
  readonly label: string;
  readonly icon?: LucideIcon;
  /** A count shown after the label (open items, unread, …). Hidden when 0 or undefined. */
  readonly count?: number;
}

/** The open tab, kept in `?{param}=`; an unknown or missing value is the first tab. */
export function useUrlTab<T extends string>(
  ids: readonly T[],
  param = "tab",
): readonly [T, (next: T) => void] {
  const [params, setParams] = useSearchParams();
  const raw = params.get(param);
  const first = ids[0] as T;
  const current = (ids as readonly string[]).includes(raw ?? "") ? (raw as T) : first;
  const select = useCallback(
    (next: T) => {
      setParams(
        (previous) => {
          const updated = new URLSearchParams(previous);
          if (next === first) {
            updated.delete(param);
          } else {
            updated.set(param, next);
          }
          return updated;
        },
        { replace: true },
      );
    },
    [first, param, setParams],
  );
  return [current, select] as const;
}

interface TabListProps<T extends string> {
  readonly tabs: readonly TabDefinition<T>[];
  readonly value: T;
  readonly onChange: (next: T) => void;
  readonly label: string;
  /** Prefix for the tab and panel ids; one per tab list on a page. */
  readonly idBase?: string;
  readonly className?: string;
}

export const tabId = (base: string, id: string): string => `${base}-tab-${id}`;
export const panelId = (base: string, id: string): string => `${base}-panel-${id}`;

export function TabList<T extends string>({
  tabs,
  value,
  onChange,
  label,
  idBase,
  className = "",
}: TabListProps<T>): React.ReactElement {
  const generated = useId().replace(/:/g, "");
  const base = idBase ?? generated;
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const move = (index: number) => {
    const next = (index + tabs.length) % tabs.length;
    const target = tabs[next];
    if (!target) {
      return;
    }
    onChange(target.id);
    refs.current[next]?.focus();
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    switch (event.key) {
      case "ArrowRight":
        event.preventDefault();
        move(index + 1);
        break;
      case "ArrowLeft":
        event.preventDefault();
        move(index - 1);
        break;
      case "Home":
        event.preventDefault();
        move(0);
        break;
      case "End":
        event.preventDefault();
        move(tabs.length - 1);
        break;
      default:
        break;
    }
  };

  return (
    <div
      role="tablist"
      aria-label={label}
      className={`-mx-1 flex gap-1 overflow-x-auto overscroll-x-contain border-b border-border/70 px-1 [scrollbar-width:none] ${className}`}
    >
      {tabs.map((tab, index) => {
        const selected = tab.id === value;
        const Icon = tab.icon;
        return (
          <button
            key={tab.id}
            ref={(element) => {
              refs.current[index] = element;
            }}
            id={tabId(base, tab.id)}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-controls={panelId(base, tab.id)}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(tab.id)}
            onKeyDown={(event) => onKeyDown(event, index)}
            className={`-mb-px inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2.5 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 focus-visible:ring-offset-1 focus-visible:ring-offset-background ${
              selected
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:border-border-strong hover:text-foreground"
            }`}
          >
            {Icon ? <Icon className={`h-4 w-4 ${selected ? "text-primary" : ""}`} aria-hidden="true" /> : null}
            {tab.label}
            {tab.count ? (
              <span className="rounded-full bg-muted px-1.5 text-[11px] font-semibold tabular-nums text-muted-foreground">
                {tab.count}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

interface TabPanelProps {
  readonly idBase: string;
  readonly id: string;
  readonly children: React.ReactNode;
  readonly className?: string;
}

export const TabPanel: React.FC<TabPanelProps> = ({ idBase, id, children, className = "" }) => (
  <div
    role="tabpanel"
    id={panelId(idBase, id)}
    aria-labelledby={tabId(idBase, id)}
    tabIndex={0}
    className={`focus-visible:outline-none ${className}`}
  >
    {children}
  </div>
);

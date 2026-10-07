/**
 * The signed-in user's card at the foot of the sidebar, and its menu.
 *
 * The card shows who is signed in (avatar, name, role) instead of a truncated
 * email; the menu, opened upward in a portal so no sidebar overflow can clip
 * it, holds the account destinations, the theme and signing out.
 *
 * "Sign Out" also stays one click away as its own button beside the card. A
 * session ends from the sidebar in one step, as it always has; the browser
 * suite (and anyone in a hurry) relies on that.
 */
import React, { useState } from "react";
import { Link } from "react-router-dom";
import {
  FloatingFocusManager,
  FloatingPortal,
  autoUpdate,
  flip,
  offset,
  shift,
  useClick,
  useDismiss,
  useFloating,
  useInteractions,
  useRole,
} from "@floating-ui/react";
import {
  ChevronsUpDown,
  LogOut,
  Monitor,
  MonitorSmartphone,
  Moon,
  Sun,
  UserRound,
} from "lucide-react";

import { Avatar } from "@/components/common/Avatar";
import { SideTooltip } from "@/components/layout/SideTooltip";
import { useAuthStore } from "@/store/useAuthStore";
import { useUIStore, type ThemeMode } from "@/store/useUIStore";

interface UserMenuProps {
  readonly onLogout: () => void;
  readonly collapsed?: boolean;
  /** The user's role in the current organization, e.g. "OWNER". */
  readonly role?: string | null | undefined;
  /** Account settings. Absent outside a workspace (organization console). */
  readonly profileHref?: string | undefined;
  readonly sessionsHref?: string | undefined;
}

/** "sabarish.warne@acme.com" -> "Sabarish Warne". */
export function nameFromEmail(email: string | null | undefined): string {
  const local = (email ?? "").split("@")[0] ?? "";
  const words = local.split(/[._\-+]+/).filter(Boolean);
  if (words.length === 0) {
    return "Your account";
  }
  return words.map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join(" ");
}

function roleLabel(role: string | null | undefined): string | null {
  if (!role) {
    return null;
  }
  const lower = String(role).toLowerCase();
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}

const THEMES: ReadonlyArray<{ value: ThemeMode; label: string; icon: React.ElementType }> = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "system", label: "System", icon: Monitor },
];

const ITEM =
  "flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-sm text-foreground/90 outline-none hover:bg-accent hover:text-foreground focus-visible:bg-accent focus-visible:ring-0 focus-visible:ring-offset-0";

export const UserMenu: React.FC<UserMenuProps> = ({
  onLogout,
  collapsed = false,
  role,
  profileHref,
  sessionsHref,
}) => {
  const user = useAuthStore((state) => state.user);
  const theme = useUIStore((state) => state.theme);
  const setTheme = useUIStore((state) => state.setTheme);
  const [open, setOpen] = useState(false);

  const { refs, floatingStyles, context } = useFloating({
    open,
    onOpenChange: setOpen,
    placement: collapsed ? "right-end" : "top-start",
    middleware: [offset(8), flip({ padding: 8 }), shift({ padding: 8 })],
    whileElementsMounted: autoUpdate,
  });
  const { getReferenceProps, getFloatingProps } = useInteractions([
    useClick(context),
    useDismiss(context),
    useRole(context, { role: "menu" }),
  ]);

  const name = nameFromEmail(user?.email);
  const roleText = roleLabel(role);
  const close = () => setOpen(false);

  const signOutButton = (
    <button
      type="button"
      onClick={onLogout}
      aria-label="Sign Out"
      title={collapsed ? undefined : "Sign Out"}
      className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
    >
      <LogOut className="h-4 w-4" aria-hidden="true" />
    </button>
  );

  return (
    <div className={`flex items-center gap-1 ${collapsed ? "flex-col" : ""}`}>
      <button
        type="button"
        ref={refs.setReference}
        {...getReferenceProps()}
        aria-label={`Account menu for ${user?.email ?? "your account"}`}
        className={`group flex min-w-0 items-center gap-2.5 rounded-lg text-left hover:bg-accent/70 ${
          open ? "bg-accent/70" : ""
        } ${collapsed ? "justify-center p-1" : "flex-1 px-2 py-1.5"}`}
      >
        <Avatar
          userId={user?.id}
          hasAvatar={user?.has_avatar}
          email={user?.email}
          size="sm"
          className="ring-1 ring-border-strong/60"
        />
        {!collapsed && (
          <>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[13px] font-medium leading-5 text-foreground">
                {name}
              </span>
              {roleText ? (
                <span className="mt-0.5 inline-flex items-center rounded border border-border-strong/60 bg-muted/60 px-1.5 text-[10px] font-medium uppercase leading-4 tracking-wide text-muted-foreground">
                  {roleText}
                </span>
              ) : (
                <span className="block truncate text-[11px] text-muted-foreground">{user?.email}</span>
              )}
            </span>
            <ChevronsUpDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground/70 group-hover:text-muted-foreground" aria-hidden="true" />
          </>
        )}
      </button>

      {collapsed ? <SideTooltip label="Sign Out">{signOutButton}</SideTooltip> : signOutButton}

      {open && (
        <FloatingPortal>
          <FloatingFocusManager context={context} modal={false} initialFocus={-1}>
            <div
              ref={refs.setFloating}
              style={floatingStyles}
              {...getFloatingProps()}
              className="fp-popover z-[60] w-64 p-1.5"
            >
              <div className="flex items-center gap-2.5 px-2 pb-2.5 pt-1.5">
                <Avatar userId={user?.id} hasAvatar={user?.has_avatar} email={user?.email} size="md" />
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-foreground">{name}</p>
                  <p className="truncate text-xs text-muted-foreground">{user?.email}</p>
                </div>
              </div>
              <div className="my-1 h-px bg-border" role="separator" />

              {profileHref && (
                <Link to={profileHref} role="menuitem" onClick={close} className={ITEM}>
                  <UserRound className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                  Profile settings
                </Link>
              )}
              {sessionsHref && (
                <Link to={sessionsHref} role="menuitem" onClick={close} className={ITEM}>
                  <MonitorSmartphone className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                  Active sessions
                </Link>
              )}

              <div className="px-2 pb-1.5 pt-2">
                <p className="fp-eyebrow mb-1.5" id="user-menu-theme">
                  Theme
                </p>
                <div
                  role="radiogroup"
                  aria-labelledby="user-menu-theme"
                  className="grid grid-cols-3 gap-0.5 rounded-lg border border-border bg-muted/50 p-0.5"
                >
                  {THEMES.map(({ value, label, icon: Icon }) => {
                    const selected = theme === value;
                    return (
                      <button
                        key={value}
                        type="button"
                        role="radio"
                        aria-checked={selected}
                        onClick={() => setTheme(value)}
                        className={`flex items-center justify-center gap-1 rounded-md px-1.5 py-1 text-xs font-medium ${
                          selected
                            ? "bg-card text-foreground shadow-elevation-1 ring-1 ring-border-strong/60"
                            : "text-muted-foreground hover:text-foreground"
                        }`}
                      >
                        <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                        {label}
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="my-1 h-px bg-border" role="separator" />
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  close();
                  onLogout();
                }}
                className={`${ITEM} text-destructive hover:bg-destructive/10 hover:text-destructive focus-visible:bg-destructive/10`}
              >
                <LogOut className="h-4 w-4" aria-hidden="true" />
                Sign out
              </button>
            </div>
          </FloatingFocusManager>
        </FloatingPortal>
      )}
    </div>
  );
};

export default UserMenu;

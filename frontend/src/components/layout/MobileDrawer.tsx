import React, { useEffect } from "react";

interface MobileDrawerProps {
  readonly children: React.ReactNode;
  readonly open: boolean;
  readonly onClose: () => void;
}

const MobileDrawer: React.FC<MobileDrawerProps> = ({
  children,
  open,
  onClose,
}) => {
  // Lock body scrolling when drawer is open
  useEffect(() => {
    if (open) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [open]);

  // Handle Escape key
  useEffect(() => {
    if (!open) {return;}
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {onClose();}
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  return (
    <>
      {/* Backdrop with touch blur */}
      <div
        onClick={onClose}
        aria-hidden="true"
        className={`
          fixed inset-0 z-40 bg-black/60 backdrop-blur-sm transition-opacity duration-200 lg:hidden
          ${
            open
              ? "opacity-100 pointer-events-auto"
              : "opacity-0 pointer-events-none"
          }
        `}
      />

      {/* Slide-out Drawer */}
      {/* F-165: closed, the drawer is inert (out of the tab order and the accessibility tree) and casts
          no shadow; it used to keep both, so Tab walked into hidden links and a grey strip ran down
          the left edge of every page on a phone. */}
      <aside
        role="dialog"
        aria-modal={open ? "true" : undefined}
        aria-hidden={open ? undefined : "true"}
        inert={!open}
        aria-label="Navigation Menu"
        className={`
          fixed inset-y-0 left-0 z-50
          w-[280px] max-w-[85vw]
          bg-sidebar
          border-r
          border-border
          transition-[transform,box-shadow]
          duration-200
          ease-out-expo
          lg:hidden
          ${
            open
              ? "translate-x-0 shadow-elevation-3"
              : "-translate-x-full shadow-none"
          }
        `}
      >
        {children}
      </aside>
    </>
  );
};

export default React.memo(MobileDrawer);

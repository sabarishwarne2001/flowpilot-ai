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
          fixed inset-0 z-40 bg-black/60 backdrop-blur-sm transition-opacity duration-300 lg:hidden
          ${
            open
              ? "opacity-100 pointer-events-auto"
              : "opacity-0 pointer-events-none"
          }
        `}
      />

      {/* Slide-out Drawer */}
      <aside
        role="dialog"
        aria-modal="true"
        aria-label="Navigation Menu"
        className={`
          fixed inset-y-0 left-0 z-50
          w-72 max-w-[85vw]
          bg-card
          border-r
          border-border
          shadow-2xl
          transition-transform
          duration-300
          ease-in-out
          lg:hidden
          ${
            open
              ? "translate-x-0"
              : "-translate-x-full"
          }
        `}
      >
        {children}
      </aside>
    </>
  );
};

export default React.memo(MobileDrawer);

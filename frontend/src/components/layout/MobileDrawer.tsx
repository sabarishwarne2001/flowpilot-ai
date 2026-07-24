import React from "react";

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
  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        className={`
          fixed inset-0 z-40 bg-black/40 transition-opacity lg:hidden
          ${
            open
              ? "opacity-100 pointer-events-auto"
              : "opacity-0 pointer-events-none"
          }
        `}
      />

      {/* Drawer */}
      <aside
        className={`
          fixed inset-y-0 left-0 z-50
          w-64
          bg-card
          border-r
          border-border
          transition-transform
          duration-300
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

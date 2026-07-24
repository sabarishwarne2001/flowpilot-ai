import React from "react";

import { useUIStore } from "@/store/useUIStore";

import DesktopSidebar from "./DesktopSidebar";
import MobileDrawer from "./MobileDrawer";
import MobileSidebarContent from "./MobileSidebarContent";

interface SidebarProps {
  readonly onLogout: () => void;
  readonly className?: string;
}

const Sidebar: React.FC<SidebarProps> = ({
  onLogout,
  className,
}) => {
  const {
    isMobileSidebarOpen,
    closeMobileSidebar,
  } = useUIStore();

  return (
    <>
      {/* Desktop */}
      <div className="hidden lg:block">
        <DesktopSidebar
          onLogout={onLogout}
          className={className}
        />
      </div>

      {/* Mobile */}
      <MobileDrawer
        open={isMobileSidebarOpen}
        onClose={closeMobileSidebar}
      >
        <MobileSidebarContent
          onLogout={onLogout}
        />
      </MobileDrawer>
    </>
  );
};

export default React.memo(Sidebar);

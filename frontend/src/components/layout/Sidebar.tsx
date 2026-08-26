import React from "react";

import { useUIStore } from "@/store/useUIStore";

import MobileDrawer from "./MobileDrawer";
import MobileSidebarContent from "./MobileSidebarContent";

interface SidebarProps {
  readonly onLogout: () => void;
}

const Sidebar: React.FC<SidebarProps> = ({ onLogout }) => {
  const { isMobileSidebarOpen, closeMobileSidebar } = useUIStore();

  return (
    <MobileDrawer
      open={isMobileSidebarOpen}
      onClose={closeMobileSidebar}
    >
      <MobileSidebarContent onLogout={onLogout} />
    </MobileDrawer>
  );
};

export default React.memo(Sidebar);

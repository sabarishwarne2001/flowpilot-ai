import React from "react";
import { X } from "lucide-react";

import { Brand } from "@/components/branding/Brand";
import { useUIStore } from "@/store/useUIStore";

import SidebarNavigation from "./SidebarNavigation";

interface MobileSidebarContentProps {
  readonly onLogout: () => void;
}

const MobileSidebarContent: React.FC<MobileSidebarContentProps> = ({
  onLogout,
}) => {
  const { closeMobileSidebar } = useUIStore();

  return (
    <div className="flex h-full flex-col bg-card">
      <div className="flex h-16 items-center justify-between border-b border-border/40 px-4">
        <Brand
          variant="sidebar"
          className="min-w-0 flex-1"
        />

        <button
          type="button"
          onClick={closeMobileSidebar}
          className="ml-2 flex h-8 w-8 items-center justify-center rounded-md border border-border bg-background text-muted-foreground hover:bg-muted/50"
          aria-label="Close Sidebar"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        <SidebarNavigation
          collapsed={false}
          onNavigate={closeMobileSidebar}
        />
      </div>

      <div className="border-t border-border/40 p-4">
        <button
          type="button"
          onClick={() => {
            closeMobileSidebar();
            onLogout();
          }}
          className="
            flex
            w-full
            items-center
            justify-center
            rounded-lg
            border
            border-border
            py-2.5
            text-sm
            font-semibold
            transition-colors
            hover:bg-destructive/10
            hover:text-destructive
          "
        >
          Sign Out
        </button>
      </div>
    </div>
  );
};

export default React.memo(MobileSidebarContent);

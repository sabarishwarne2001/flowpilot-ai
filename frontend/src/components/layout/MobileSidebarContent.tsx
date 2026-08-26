import React from "react";
import { X, LogOut } from "lucide-react";

import { Brand } from "@/components/branding/Brand";
import { useUIStore } from "@/store/useUIStore";
import { useAuthStore } from "@/store/useAuthStore";
import OrgWorkspaceSwitcher from "./OrgWorkspaceSwitcher";
import SidebarNavigation from "./SidebarNavigation";

interface MobileSidebarContentProps {
  readonly onLogout: () => void;
}

const MobileSidebarContent: React.FC<MobileSidebarContentProps> = ({
  onLogout,
}) => {
  const { closeMobileSidebar } = useUIStore();
  const { user } = useAuthStore();

  return (
    <div className="flex h-full flex-col bg-card">
      {/* Brand Header & Close Button */}
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

      {/* Tenant Switcher on Mobile */}
      <div className="border-b border-border/40">
        <OrgWorkspaceSwitcher collapsed={false} />
      </div>

      {/* Navigation */}
      <div className="flex-1 overflow-y-auto min-h-0">
        <SidebarNavigation
          collapsed={false}
          onNavigate={closeMobileSidebar}
        />
      </div>

      {/* Bottom Profile & Sign Out Section */}
      <div className="border-t border-border/40 bg-muted/20 p-4">
        <div className="mb-3 truncate text-xs">
          <span className="block font-semibold text-muted-foreground">Signed in as</span>
          <span className="font-bold text-foreground">{user?.email ?? "User Profile"}</span>
        </div>

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
            gap-2
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
          <LogOut className="h-4 w-4" />
          Sign Out
        </button>
      </div>
    </div>
  );
};

export default React.memo(MobileSidebarContent);

import React from "react";
import { X, LogOut } from "lucide-react";

import { Avatar } from "@/components/common/Avatar";
import { useUIStore } from "@/store/useUIStore";
import { useAuthStore } from "@/store/useAuthStore";
import OrgWorkspaceSwitcher from "./OrgWorkspaceSwitcher";
import SidebarNavigation from "./SidebarNavigation";
import { nameFromEmail } from "./UserMenu";

interface MobileSidebarContentProps {
  readonly onLogout: () => void;
}

const MobileSidebarContent: React.FC<MobileSidebarContentProps> = ({
  onLogout,
}) => {
  const { closeMobileSidebar } = useUIStore();
  const { user } = useAuthStore();

  return (
    <div className="flex h-full flex-col bg-sidebar text-sidebar-foreground">
      {/* Workspace identity & close */}
      <div className="flex h-14 shrink-0 items-center gap-1 border-b border-border/70 px-2.5">
        <div className="min-w-0 flex-1">
          <OrgWorkspaceSwitcher collapsed={false} />
        </div>

        <button
          type="button"
          onClick={closeMobileSidebar}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
          aria-label="Close Sidebar"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Navigation */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        <SidebarNavigation collapsed={false} onNavigate={closeMobileSidebar} />
      </div>

      {/* The signed-in user */}
      <div className="shrink-0 border-t border-border/70 p-3">
        <div className="mb-2.5 flex min-w-0 items-center gap-2.5 px-1">
          <Avatar userId={user?.id} hasAvatar={user?.has_avatar} email={user?.email} size="sm" />
          <div className="min-w-0">
            <span className="block truncate text-[13px] font-medium text-foreground">
              {nameFromEmail(user?.email)}
            </span>
            <span className="block truncate text-[11px] text-muted-foreground">
              {user?.email ?? ""}
            </span>
          </div>
        </div>

        <button
          type="button"
          onClick={() => {
            closeMobileSidebar();
            onLogout();
          }}
          className="fp-btn fp-btn-secondary w-full hover:text-destructive"
        >
          <LogOut className="h-4 w-4" />
          Sign Out
        </button>
      </div>
    </div>
  );
};

export default React.memo(MobileSidebarContent);

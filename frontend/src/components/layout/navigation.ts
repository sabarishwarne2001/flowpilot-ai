import type { LucideIcon } from "lucide-react";
import {
  LayoutDashboard,
  FileText,
  MessageSquare,
  Settings,
  Sliders,
} from "lucide-react";

import { ROUTES } from "@/constants/routes";

export interface NavigationItem {
  readonly name: string;
  readonly path: string;
  readonly icon: LucideIcon;
}

export const NAVIGATION_ITEMS: readonly NavigationItem[] = [
  {
    name: "Overview",
    path: ROUTES.DASHBOARD,
    icon: LayoutDashboard,
  },
  {
    name: "Documents",
    path: ROUTES.WORK_ITEMS,
    icon: FileText,
  },
  {
    name: "AI Assistant",
    path: ROUTES.ASSISTANT,
    icon: MessageSquare,
  },
  {
    name: "Workflows",
    path: ROUTES.AUTOMATION,
    icon: Sliders,
  },
  {
    name: "Settings",
    path: ROUTES.SETTINGS,
    icon: Settings,
  },
] as const;

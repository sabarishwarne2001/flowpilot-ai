import React from "react";

import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import {
  UNSAVED_CHANGES_LEAVE,
  UNSAVED_CHANGES_MESSAGE,
  UNSAVED_CHANGES_STAY,
  UNSAVED_CHANGES_TITLE,
  useNavigationGuardStore,
} from "@/hooks/useUnsavedChangesGuard";

/**
 * HM-S1:unsaved-changes-dialog — the one place a blocked in-app navigation is
 * answered. Mounted once in App.tsx; every page's `useUnsavedChangesGuard`
 * publishes to the same store. "Stay on Page" has focus by default, so Enter
 * never discards work, and Escape stays too.
 */
export const UnsavedChangesDialogHost: React.FC = () => {
  const pending = useNavigationGuardStore((state) => state.pending);
  return (
    <ConfirmDialog
      open={pending !== null}
      title={UNSAVED_CHANGES_TITLE}
      message={UNSAVED_CHANGES_MESSAGE}
      confirmText={UNSAVED_CHANGES_LEAVE}
      cancelText={UNSAVED_CHANGES_STAY}
      tone="danger"
      initialFocus="cancel"
      onConfirm={() => pending?.proceed()}
      onCancel={() => pending?.reset()}
    />
  );
};

export default UnsavedChangesDialogHost;

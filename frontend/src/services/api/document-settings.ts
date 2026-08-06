/**
 * Document processing settings API service for FlowPilot AI.
 *
 * Workspace-addressed since backend ARCH-01 Step 9c-2. See aiSettings.ts for
 * why the identifier is an explicit parameter.
 */

import api from "./client";
import { SETTINGS_ENDPOINTS } from "./endpoints";

import type {
    DocumentSettings,
    UpdateDocumentSettings,
} from "../../types/document-settings";

export async function getDocumentSettings(
    workspaceId: string,
): Promise<DocumentSettings> {
    const response = await api.get<DocumentSettings>(
        SETTINGS_ENDPOINTS.documentSettings(workspaceId),
    );

    return response.data;
}

export async function updateDocumentSettings(
    workspaceId: string,
    data: UpdateDocumentSettings,
): Promise<DocumentSettings> {
    const response = await api.put<DocumentSettings>(
        SETTINGS_ENDPOINTS.documentSettings(workspaceId),
        data,
    );

    return response.data;
}

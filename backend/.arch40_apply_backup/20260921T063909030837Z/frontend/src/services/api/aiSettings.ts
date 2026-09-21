/**
 * AI settings API service for FlowPilot AI.
 *
 * Every function takes the workspace identifier explicitly. Backend ARCH-01
 * Step 9c-2 moved this router under /workspaces/{workspace_id}/ai-settings, so
 * the previous flat paths 404.
 *
 * The identifier is a required parameter rather than something the service
 * resolves for itself. A module-level "current workspace" would be the same
 * implicit-tenant pattern ARCH-01 removed from the backend, where hiding the
 * identifier is exactly what made a second membership crash an account. It
 * also means the compiler enumerates every call site when this contract
 * changes, instead of a runtime 404 doing it one bug report at a time.
 */

import api from "./client";
import { SETTINGS_ENDPOINTS } from "./endpoints";

import type {
    AISettings,
    UpdateAISettingsRequest,
} from "../../types/aiSettings";

import type { AISettingsFormData } from "@/schemas/aiSettings";

import type { AIConnectionTestResponse } from "@/types/aiConnectionTest";

export interface AvailableProvidersResponse {
    providers: string[];
}

export async function getAISettings(
    workspaceId: string,
): Promise<AISettings> {
    const response = await api.get<AISettings>(
        SETTINGS_ENDPOINTS.aiSettings(workspaceId),
    );

    return response.data;
}

export async function updateAISettings(
    workspaceId: string,
    data: UpdateAISettingsRequest,
): Promise<AISettings> {
    const response = await api.put<AISettings>(
        SETTINGS_ENDPOINTS.aiSettings(workspaceId),
        data,
    );

    return response.data;
}

export async function getSupportedModels(
    workspaceId: string,
): Promise<Record<string, string[]>> {
    const response = await api.get<Record<string, string[]>>(
        SETTINGS_ENDPOINTS.aiSettingsModels(workspaceId),
    );

    return response.data;
}

export async function testAIConnection(
    workspaceId: string,
    data: AISettingsFormData,
): Promise<AIConnectionTestResponse> {
    const response = await api.post<AIConnectionTestResponse>(
        SETTINGS_ENDPOINTS.aiSettingsTest(workspaceId),
        data,
    );

    return response.data;
}

export async function getAvailableProviders(
    workspaceId: string,
): Promise<AvailableProvidersResponse> {
    const response = await api.get<AvailableProvidersResponse>(
        SETTINGS_ENDPOINTS.aiSettingsProviders(workspaceId),
    );

    return response.data;
}

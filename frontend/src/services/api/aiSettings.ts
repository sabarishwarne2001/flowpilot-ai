import api from "./client";

import type{
    AISettings,
    UpdateAISettingsRequest,
} from "../../types/aiSettings";

import type { AISettingsFormData } from "@/schemas/aiSettings";

import type { AIConnectionTestResponse } from "@/types/aiConnectionTest";

export interface AvailableProvidersResponse {
    providers: string[];
}

const API_URL = "/ai-settings";

export async function getAISettings(): Promise<AISettings> {
    const response = await api.get<AISettings>(
        API_URL,
    );

    return response.data;
}

export async function updateAISettings(
    data: UpdateAISettingsRequest,
): Promise<AISettings> {
    const response = await api.put<AISettings>(
        API_URL,
        data,
    );

    return response.data;
}

export async function getSupportedModels(): Promise<
    Record<string, string[]>
> {
    const response = await api.get<Record<string, string[]>>(
        "/ai-settings/models"
    );

    return response.data;
}

export async function testAIConnection(
    data: AISettingsFormData
): Promise<AIConnectionTestResponse> {
    const response =
        await api.post<AIConnectionTestResponse>(
            "/ai-settings/test",
            data
        );

    return response.data;
}

export async function getAvailableProviders(): Promise<AvailableProvidersResponse> {
    const response =
        await api.get<AvailableProvidersResponse>(
            "/ai-settings/providers"
        );

    return response.data;
}

import api from "./client";

import type{
    AISettings,
    UpdateAISettingsRequest,
} from "../../types/aiSettings";

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

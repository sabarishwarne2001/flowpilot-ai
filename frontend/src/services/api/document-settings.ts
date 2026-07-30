import api from "./client";

import type {
    DocumentSettings,
    UpdateDocumentSettings,
} from "../../types/document-settings";

const API_URL = "/document-settings";

export async function getDocumentSettings(): Promise<DocumentSettings> {
    const response = await api.get<DocumentSettings>(
        API_URL,
    );

    return response.data;
}

export async function updateDocumentSettings(
    data: UpdateDocumentSettings,
): Promise<DocumentSettings> {
    const response = await api.put<DocumentSettings>(
        API_URL,
        data,
    );

    return response.data;
}

import { apiClient } from "./client";

export interface UploadLogoResponse {
  logo_url: string;
}

export async function uploadLogo(
  workspaceId: string,
  file: File,
): Promise<UploadLogoResponse> {
  const formData = new FormData();
  formData.append("file", file);

  const { data } = await apiClient.post<UploadLogoResponse>(
    `/workspaces/${encodeURIComponent(workspaceId)}/upload/logo`,
    formData,
    {
      headers: {
        "Content-Type": "multipart/form-data",
      },
    },
  );

  return data;
}

export async function deleteLogo(
  workspaceId: string,
): Promise<void> {
  await apiClient.delete(
    `/workspaces/${encodeURIComponent(workspaceId)}/upload/logo`,
  );
}

import { apiClient } from "./client";

export interface UploadLogoResponse {
  logo_url: string;
}

export async function uploadLogo(
  file: File,
): Promise<UploadLogoResponse> {
  const formData = new FormData();

  formData.append("file", file);

  const { data } = await apiClient.post<UploadLogoResponse>(
    "/upload/logo",
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
  logoUrl: string
) {
  await apiClient.delete("/upload/logo", {
    data: {
      logo_url: logoUrl,
    },
  });
}

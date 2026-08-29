import apiClient from "@/services/api/client";
import { PROFILE_ENDPOINTS } from "@/services/api/endpoints";
import type {
  AvatarUploadResult,
  UserProfile,
  UserProfileUpdateRequest,
} from "@/types/profile";

export const getMyProfile = async (): Promise<UserProfile> => {
  const response = await apiClient.get<UserProfile>(PROFILE_ENDPOINTS.profile);
  return response.data;
};

export const updateMyProfile = async (
  data: UserProfileUpdateRequest,
): Promise<UserProfile> => {
  const response = await apiClient.patch<UserProfile>(
    PROFILE_ENDPOINTS.profile,
    data,
  );
  return response.data;
};

export const uploadAvatar = async (file: File): Promise<AvatarUploadResult> => {
  const form = new FormData();
  form.append("file", file);
  const response = await apiClient.post<AvatarUploadResult>(
    PROFILE_ENDPOINTS.avatar,
    form,
  );
  return response.data;
};

export const deleteAvatar = async (): Promise<void> => {
  await apiClient.delete(PROFILE_ENDPOINTS.avatar);
};

export const profileApi = {
  getMyProfile,
  updateMyProfile,
  uploadAvatar,
  deleteAvatar,
} as const;

export default profileApi;

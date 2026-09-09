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

/*
 * The profileApi namespace wrapper was removed here, with its default export.
 *
 * It was an object literal re-exporting the named functions above, and it had
 * exactly two references in the whole repository: its own declaration and its
 * own `export default`. Every consumer imports the standalone functions
 * directly, which is why it could sit here for months looking like API
 * surface while being reachable from nothing.
 *
 * Safe to delete precisely because of that count -- anything importing it
 * would have appeared in the same grep that found it dead.
 */

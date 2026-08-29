export interface UserProfile {
  readonly id: string;
  readonly email: string;
  readonly display_name: string | null;
  readonly timezone: string;
  readonly locale: string;
}

export interface UserProfileUpdateRequest {
  readonly display_name?: string | null;
  readonly timezone?: string | null;
  readonly locale?: string | null;
}

export interface AvatarUploadResult {
  readonly file_id: string;
  readonly mime_type: string;
  readonly file_size: number;
}

export interface EmailChangeRequestPayload {
  readonly current_password: string;
  readonly new_email: string;
}

export interface EmailChangeRequestResult {
  readonly new_email: string;
  readonly expires_at: string;
}

export interface EmailChangeConfirmResult {
  readonly email: string;
  readonly sessions_revoked: boolean;
  readonly detail: string;
}

/** Mirrors backend avatar_service.py */
export const AVATAR_MAX_BYTES = 2 * 1024 * 1024;
export const AVATAR_MIN_DIMENSION = 32;
export const AVATAR_MAX_DIMENSION = 1024;

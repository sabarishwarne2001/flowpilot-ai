import apiClient from "@/services/api/client";

/**
 * N-017 — the signed-in user's two-factor sign-in settings (`/me/mfa`).
 * See backend/app/api/v1/mfa.py.
 */

export interface MfaStatus {
  readonly enabled: boolean;
  readonly pending: boolean;
  readonly confirmed_at: string | null;
  readonly recovery_codes_remaining: number;
}

export interface MfaSetup {
  readonly secret: string;
  readonly otpauth_uri: string;
}

export interface MfaRecoveryCodes {
  readonly recovery_codes: string[];
}

export const getMfaStatus = async (): Promise<MfaStatus> =>
  (await apiClient.get<MfaStatus>("/me/mfa")).data;

export const startMfaSetup = async (password: string): Promise<MfaSetup> =>
  (await apiClient.post<MfaSetup>("/me/mfa/setup", { password })).data;

export const confirmMfa = async (code: string): Promise<MfaRecoveryCodes> =>
  (await apiClient.post<MfaRecoveryCodes>("/me/mfa/confirm", { code: code.trim() })).data;

export const regenerateRecoveryCodes = async (code: string): Promise<MfaRecoveryCodes> =>
  (await apiClient.post<MfaRecoveryCodes>("/me/mfa/recovery-codes", { code: code.trim() })).data;

export const disableMfa = async (password: string, code: string): Promise<void> => {
  await apiClient.post("/me/mfa/disable", { password, code: code.trim() });
};

export const MFA_STATUS_KEY = ["me", "mfa"] as const;

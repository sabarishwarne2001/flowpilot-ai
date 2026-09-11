import React, { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, ShieldAlert, Trash2, Upload, UserRound } from "lucide-react";

import {
  deleteAvatar,
  getMyProfile,
  updateMyProfile,
  uploadAvatar,
} from "@/services/api/profile";
import { profileKeys } from "@/services/api/queryKeys";
import { PROFILE_ENDPOINTS } from "@/services/api/endpoints";
import { useAuthenticatedImage } from "@/hooks/useAuthenticatedImage";
import EmailChangePanel from "@/pages/Settings/EmailChangePanel";
import PasswordChangePanel from "@/pages/Settings/PasswordChangePanel";
import MyWorkspaceGrantsPanel from "@/pages/Settings/MyWorkspaceGrantsPanel";
import {
  AVATAR_MAX_BYTES,
  AVATAR_MAX_DIMENSION,
  AVATAR_MIN_DIMENSION,
} from "@/types/profile";
import { bumpAvatarVersion } from "@/store/useAvatarVersionStore";

function detailOf(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })
    ?.response?.data?.detail;
  if (typeof detail === "string") { return detail; }
  if (Array.isArray(detail) && detail[0]?.msg) { return String(detail[0].msg); }
  return fallback;
}

function readDimensions(file: File): Promise<{ w: number; h: number } | null> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      URL.revokeObjectURL(url);
      resolve({ w: image.naturalWidth, h: image.naturalHeight });
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      resolve(null);
    };
    image.src = url;
  });
}

export const ProfileSettings: React.FC = () => {
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);

  const [displayName, setDisplayName] = useState("");
  const [timezone, setTimezone] = useState("");
  const [locale, setLocale] = useState("");
  const [dirty, setDirty] = useState(false);

  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [avatarError, setAvatarError] = useState<string | null>(null);
  const [avatarVersion, setAvatarVersion] = useState(0);

  const { data: profile, isLoading, isError, refetch } = useQuery({
    queryKey: profileKeys.me(),
    queryFn: getMyProfile,
    staleTime: 60_000,
  });

  // ARCH-29 Tranche 3. The local counter above only repaints THIS page's
  // preview. `bumpAvatarVersion` is what reaches the three sidebar avatars,
  // which otherwise keep the browser-cached copy until a full reload — and,
  // after a delete, keep showing a photo the server no longer has.
  const broadcastAvatarChange = useCallback((): void => {
    setAvatarVersion((v) => v + 1);
    bumpAvatarVersion(profile?.id);
  }, [profile?.id]);


  useEffect(() => {
    if (!profile || dirty) { return; }
    setDisplayName(profile.display_name ?? "");
    setTimezone(profile.timezone);
    setLocale(profile.locale);
  }, [profile, dirty]);

  const avatarUrl = profile
    ? `${PROFILE_ENDPOINTS.userAvatar(profile.id)}?v=${avatarVersion}`
    : null;

  const avatarSrc = useAuthenticatedImage(avatarUrl);

  const save = useMutation({
    mutationFn: () =>
      updateMyProfile({
        display_name: displayName.trim() || null,
        timezone: timezone.trim() || null,
        locale: locale.trim() || null,
      }),
    onSuccess: (updated) => {
      setError(null);
      setDirty(false);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2500);
      queryClient.setQueryData(profileKeys.me(), updated);
    },
    onError: (err) =>
      setError(detailOf(err, "Your profile couldn't be saved. Please try again.")),
  });

  const upload = useMutation({
    mutationFn: (file: File) => uploadAvatar(file),
    onSuccess: () => {
      setAvatarError(null);
      broadcastAvatarChange();
      queryClient.invalidateQueries({ queryKey: profileKeys.me() });
    },
    onError: (err) =>
      setAvatarError(
        detailOf(
          err,
          "That image couldn't be uploaded. Try a PNG or JPEG under 2 MB.",
        ),
      ),
  });

  const remove = useMutation({
    mutationFn: deleteAvatar,
    onSuccess: () => {
      setAvatarError(null);
      broadcastAvatarChange();
      queryClient.invalidateQueries({ queryKey: profileKeys.me() });
    },
    onError: (err) =>
      setAvatarError(detailOf(err, "That avatar couldn't be removed.")),
  });

  const pickFile = async (file: File | undefined) => {
    if (!file) { return; }
    setAvatarError(null);

    if (file.size > AVATAR_MAX_BYTES) {
      setAvatarError(
        `That image is ${(file.size / 1024 / 1024).toFixed(1)} MB. The limit is 2 MB.`,
      );
      return;
    }

    const dimensions = await readDimensions(file);
    if (dimensions) {
      const smallest = Math.min(dimensions.w, dimensions.h);
      if (smallest < AVATAR_MIN_DIMENSION) {
        setAvatarError(
          `That image is ${dimensions.w}×${dimensions.h}. It needs to be at ` +
            `least ${AVATAR_MIN_DIMENSION}px on every side.`,
        );
        return;
      }
    }

    upload.mutate(file);
  };

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground p-6">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading profile…
      </div>
    );
  }

  if (isError || !profile) {
    return (
      <div className="p-6">
        <p role="alert" className="text-sm text-destructive">
          Your profile couldn't be loaded.
        </p>
        <button
          type="button"
          onClick={() => void refetch()}
          className="mt-2 rounded-lg border border-border px-3 py-1.5 text-sm font-semibold hover:bg-muted"
        >
          Try again
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-bold tracking-tight text-foreground">Profile</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          How you appear across FlowPilot — in the member directory, on audit
          entries, and in notifications.
        </p>
      </div>

      {/* Avatar */}
      <div className="flex flex-wrap items-center gap-4 rounded-xl border border-border bg-card p-4">
        <div className="flex h-16 w-16 flex-shrink-0 items-center justify-center overflow-hidden rounded-full bg-muted border border-border">
          {avatarSrc ? (
            <img
              src={avatarSrc}
              alt="Avatar"
              onError={() => setAvatarVersion((v) => v + 1)}
              className="h-full w-full object-cover"
            />
          ) : (
            <UserRound className="h-7 w-7 text-muted-foreground" />
          )}
        </div>

        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-foreground">Profile picture</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            PNG or JPEG up to 2 MB, at least {AVATAR_MIN_DIMENSION}px on every
            side. Larger images are automatically resized to{" "}
            {AVATAR_MAX_DIMENSION}px and converted to PNG.
          </p>
          {avatarError && (
            <p role="alert" className="mt-1 text-xs text-destructive">
              {avatarError}
            </p>
          )}
        </div>

        <div className="flex flex-shrink-0 flex-wrap gap-2">
          <input
            ref={fileInput}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            onChange={(event) => {
              void pickFile(event.target.files?.[0]);
              event.target.value = "";
            }}
            className="hidden"
          />
          <button
            type="button"
            onClick={() => fileInput.current?.click()}
            disabled={upload.isPending}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-background px-3 py-1.5 text-xs font-semibold text-foreground hover:bg-muted disabled:opacity-60"
          >
            {upload.isPending ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Upload className="h-3 w-3" />
            )}
            Upload
          </button>
          {avatarSrc && (
            <button
              type="button"
              onClick={() => remove.mutate()}
              disabled={remove.isPending}
              className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-background px-3 py-1.5 text-xs font-semibold text-foreground hover:bg-muted disabled:opacity-60"
            >
              {remove.isPending ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Trash2 className="h-3 w-3" />
              )}
              Remove
            </button>
          )}
        </div>
      </div>

      {/* Profile fields */}
      <div className="space-y-4 rounded-xl border border-border bg-card p-4">
        {error && (
          <p
            role="alert"
            className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive"
          >
            <ShieldAlert className="mt-0.5 h-4 w-4 flex-shrink-0" />
            {error}
          </p>
        )}

        <div>
          <label htmlFor="profile-name" className="text-sm font-semibold text-foreground">
            Display name
          </label>
          <input
            id="profile-name"
            value={displayName}
            onChange={(event) => {
              setDisplayName(event.target.value);
              setDirty(true);
            }}
            maxLength={100}
            placeholder={profile.email}
            className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-1.5 text-sm text-foreground focus:border-primary focus:outline-none"
          />
          <p className="mt-1 text-xs text-muted-foreground">
            Leave blank to be shown by your email address instead.
          </p>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label htmlFor="profile-tz" className="text-sm font-semibold text-foreground">
              Display timezone
            </label>
            <input
              id="profile-tz"
              value={timezone}
              onChange={(event) => {
                setTimezone(event.target.value);
                setDirty(true);
              }}
              maxLength={100}
              placeholder="America/New_York"
              className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-1.5 font-mono text-sm text-foreground focus:border-primary focus:outline-none"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              Timestamps are shown to you in this timezone. Schedules follow the workspace
              timezone. IANA key. Current:{" "}
              <button
                type="button"
                onClick={() => {
                  setTimezone(Intl.DateTimeFormat().resolvedOptions().timeZone);
                  setDirty(true);
                }}
                className="underline underline-offset-2"
              >
                {Intl.DateTimeFormat().resolvedOptions().timeZone}
              </button>
            </p>
          </div>

          <div>
            <label htmlFor="profile-locale" className="text-sm font-semibold text-foreground">
              Locale
            </label>
            <input
              id="profile-locale"
              value={locale}
              onChange={(event) => {
                setLocale(event.target.value);
                setDirty(true);
              }}
              maxLength={20}
              placeholder="en-US"
              className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-1.5 font-mono text-sm text-foreground focus:border-primary focus:outline-none"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              Used for formatting dates and numbers.
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3 border-t border-border pt-3">
          <button
            type="button"
            onClick={() => save.mutate()}
            disabled={save.isPending || !dirty}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground disabled:opacity-60"
          >
            {save.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Save changes
          </button>
          {saved && (
            <span role="status" className="text-xs text-muted-foreground">
              Saved successfully.
            </span>
          )}
        </div>
      </div>

      <EmailChangePanel currentEmail={profile.email} />

      <PasswordChangePanel />

      <MyWorkspaceGrantsPanel />
    </div>
  );
};

export default ProfileSettings;

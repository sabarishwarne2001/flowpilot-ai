import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Loader2, ShieldCheck, Smartphone } from "lucide-react";
import { create as createQr } from "qrcode";

import { errorMessage } from "@/services/api/errors";
import {
  MFA_STATUS_KEY,
  confirmMfa,
  disableMfa,
  getMfaStatus,
  regenerateRecoveryCodes,
  startMfaSetup,
  type MfaSetup,
} from "@/services/api/mfa";

/**
 * N-017 — two-factor sign-in with an authenticator app.
 *
 * Off → "Turn on" asks for the password, shows a QR code (and the key, for
 * typing in), and switches on only after a code from the app is accepted.
 * The ten recovery codes are shown once, right then. On → the user can get
 * new recovery codes (with a current code) or turn it off (password + code).
 *
 * The QR code is drawn here as SVG from the `otpauth://` URI: no image request,
 * nothing for the Content-Security-Policy to allow, and the secret never
 * leaves the page.
 */

const INPUT =
  "mt-1 w-full rounded-lg border border-border bg-background px-3 py-1.5 text-sm text-foreground focus:border-primary focus:outline-none";
const PRIMARY =
  "inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground hover:opacity-90 disabled:opacity-50";
const SECONDARY =
  "inline-flex items-center gap-2 rounded-lg border border-border px-3 py-1.5 text-sm font-semibold text-foreground hover:bg-muted disabled:opacity-50";

const QrCode: React.FC<{ value: string }> = ({ value }) => {
  const { size, path } = useMemo(() => {
    const matrix = createQr(value, { errorCorrectionLevel: "M" }).modules;
    const quiet = 4;
    let d = "";
    for (let row = 0; row < matrix.size; row += 1) {
      for (let col = 0; col < matrix.size; col += 1) {
        if (matrix.get(row, col)) {
          d += `M${col + quiet},${row + quiet}h1v1h-1z`;
        }
      }
    }
    return { size: matrix.size + quiet * 2, path: d };
  }, [value]);

  return (
    <svg
      role="img"
      aria-label="QR code to scan with your authenticator app"
      viewBox={`0 0 ${size} ${size}`}
      className="h-44 w-44 rounded-md bg-white"
      shapeRendering="crispEdges"
    >
      <rect width={size} height={size} fill="#ffffff" />
      <path d={path} fill="#000000" />
    </svg>
  );
};

const RecoveryCodes: React.FC<{ codes: string[]; onDone: () => void }> = ({ codes, onDone }) => {
  const [copied, setCopied] = useState(false);
  return (
    <div className="mt-4 space-y-3 border-t border-border pt-4">
      <p className="text-sm text-foreground">
        Save these recovery codes somewhere safe, such as a password manager. Each one signs you in
        once if you lose your phone. They are shown only now.
      </p>
      <ul
        aria-label="Recovery codes"
        className="grid grid-cols-2 gap-2 rounded-lg border border-border bg-muted/40 p-3 font-mono text-sm"
      >
        {codes.map((code) => (
          <li key={code}>{code}</li>
        ))}
      </ul>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          className={SECONDARY}
          onClick={() => {
            void navigator.clipboard?.writeText(codes.join("\n")).then(
              () => setCopied(true),
              () => setCopied(false),
            );
          }}
        >
          <Copy className="h-3.5 w-3.5" aria-hidden /> {copied ? "Copied" : "Copy codes"}
        </button>
        <button type="button" className={PRIMARY} onClick={onDone}>
          I have saved them
        </button>
      </div>
    </div>
  );
};

type Step = "idle" | "password" | "scan" | "codes" | "regenerate" | "disable";

export const TwoFactorPanel: React.FC = () => {
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: MFA_STATUS_KEY, queryFn: getMfaStatus });

  const [step, setStep] = useState<Step>("idle");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [setup, setSetup] = useState<MfaSetup | null>(null);
  const [codes, setCodes] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const reset = (next: Step = "idle") => {
    setStep(next);
    setPassword("");
    setCode("");
    setError(null);
    if (next === "idle") {
      setSetup(null);
    }
  };
  const refresh = () => queryClient.invalidateQueries({ queryKey: MFA_STATUS_KEY });

  const start = useMutation({
    mutationFn: () => startMfaSetup(password),
    onSuccess: (result) => {
      setSetup(result);
      setPassword("");
      setError(null);
      setStep("scan");
    },
    onError: (err) => {
      setPassword("");
      setError(errorMessage(err, "That password isn't right."));
    },
  });

  const confirm = useMutation({
    mutationFn: () => confirmMfa(code),
    onSuccess: (result) => {
      setCodes(result.recovery_codes);
      setSetup(null);
      setCode("");
      setError(null);
      setStep("codes");
      setNotice("Two-factor sign-in is on. You'll be asked for a code each time you sign in.");
      void refresh();
    },
    onError: (err) => {
      setCode("");
      setError(errorMessage(err, "That code isn't right. Try the next one the app shows."));
    },
  });

  const regenerate = useMutation({
    mutationFn: () => regenerateRecoveryCodes(code),
    onSuccess: (result) => {
      setCodes(result.recovery_codes);
      setCode("");
      setError(null);
      setStep("codes");
      setNotice("New recovery codes are ready. The old ones no longer work.");
      void refresh();
    },
    onError: (err) => {
      setCode("");
      setError(errorMessage(err, "That code isn't right."));
    },
  });

  const disable = useMutation({
    mutationFn: () => disableMfa(password, code),
    onSuccess: () => {
      reset();
      setNotice("Two-factor sign-in is off. Your password alone signs you in.");
      void refresh();
    },
    onError: (err) => {
      setPassword("");
      setCode("");
      setError(errorMessage(err, "That didn't work. Check your password and code."));
    },
  });

  const enabled = status.data?.enabled ?? false;
  const busy = start.isPending || confirm.isPending || regenerate.isPending || disable.isPending;

  const codeField = (id: string, label: string) => (
    <div>
      <label htmlFor={id} className="text-sm font-semibold text-foreground">
        {label}
      </label>
      <input
        id={id}
        inputMode="text"
        autoComplete="one-time-code"
        value={code}
        onChange={(event) => setCode(event.target.value)}
        className={INPUT}
      />
    </div>
  );

  const passwordField = (
    <div>
      <label htmlFor="mfa-password" className="text-sm font-semibold text-foreground">
        Current password
      </label>
      <input
        id="mfa-password"
        type="password"
        autoComplete="current-password"
        value={password}
        onChange={(event) => setPassword(event.target.value)}
        className={INPUT}
      />
    </div>
  );

  return (
    <section aria-labelledby="mfa-title" className="fp-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <Smartphone className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
          <div>
            <h2 id="mfa-title" className="text-lg font-semibold tracking-tight text-foreground">
              Two-factor sign-in
            </h2>
            <p className="mt-0.5 text-sm text-muted-foreground" data-testid="mfa-state">
              {status.isLoading
                ? "Checking…"
                : enabled
                  ? `On. ${status.data?.recovery_codes_remaining ?? 0} recovery codes left.`
                  : "Off. Add a code from an authenticator app (Google Authenticator, Microsoft Authenticator, 1Password, Authy) to every sign-in."}
            </p>
          </div>
        </div>
        {step === "idle" && !status.isLoading && (
          <div className="flex flex-wrap gap-2">
            {enabled ? (
              <>
                <button type="button" className={SECONDARY} onClick={() => reset("regenerate")}>
                  New recovery codes
                </button>
                <button type="button" className={SECONDARY} onClick={() => reset("disable")}>
                  Turn off
                </button>
              </>
            ) : (
              <button
                type="button"
                className={PRIMARY}
                onClick={() => {
                  setNotice(null);
                  reset("password");
                }}
              >
                Turn on two-factor sign-in
              </button>
            )}
          </div>
        )}
      </div>

      {notice && step !== "codes" && (
        <p
          role="status"
          className="mt-3 flex items-start gap-2 rounded-lg border border-border bg-muted/40 p-3 text-sm text-foreground"
        >
          <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
          <span>{notice}</span>
        </p>
      )}

      {error && (
        <p role="alert" className="mt-3 text-sm text-destructive">
          {error}
        </p>
      )}

      {step === "password" && (
        <form
          className="mt-4 space-y-3 border-t border-border pt-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (password) {
              start.mutate();
            }
          }}
        >
          {passwordField}
          <div className="flex gap-2">
            <button type="submit" className={PRIMARY} disabled={busy || !password}>
              {start.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
              Continue
            </button>
            <button type="button" className={SECONDARY} onClick={() => reset()}>
              Cancel
            </button>
          </div>
        </form>
      )}

      {step === "scan" && setup && (
        <form
          className="mt-4 space-y-3 border-t border-border pt-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (code) {
              confirm.mutate();
            }
          }}
        >
          <p className="text-sm text-foreground">1. Scan this code with your authenticator app.</p>
          <QrCode value={setup.otpauth_uri} />
          <p className="text-xs text-muted-foreground">
            Can&apos;t scan it? Add an account by key and type:{" "}
            <code
              data-testid="mfa-secret"
              className="break-all rounded bg-muted px-1 py-0.5 font-mono text-foreground"
            >
              {setup.secret.match(/.{1,4}/g)?.join(" ")}
            </code>
          </p>
          <p className="text-sm text-foreground">2. Enter the 6-digit code the app shows.</p>
          {codeField("mfa-confirm-code", "6-digit code from the app")}
          <div className="flex gap-2">
            <button type="submit" className={PRIMARY} disabled={busy || !code}>
              {confirm.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
              Turn on
            </button>
            <button type="button" className={SECONDARY} onClick={() => reset()}>
              Cancel
            </button>
          </div>
        </form>
      )}

      {step === "codes" && (
        <>
          {notice && (
            <p role="status" className="mt-3 text-sm text-foreground">
              {notice}
            </p>
          )}
          <RecoveryCodes
            codes={codes}
            onDone={() => {
              setCodes([]);
              setNotice(null);
              reset();
            }}
          />
        </>
      )}

      {step === "regenerate" && (
        <form
          className="mt-4 space-y-3 border-t border-border pt-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (code) {
              regenerate.mutate();
            }
          }}
        >
          <p className="text-sm text-foreground">
            New codes replace every unused one you have now.
          </p>
          {codeField("mfa-regenerate-code", "6-digit code from the app")}
          <div className="flex gap-2">
            <button type="submit" className={PRIMARY} disabled={busy || !code}>
              Get new codes
            </button>
            <button type="button" className={SECONDARY} onClick={() => reset()}>
              Cancel
            </button>
          </div>
        </form>
      )}

      {step === "disable" && (
        <form
          className="mt-4 space-y-3 border-t border-border pt-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (password && code) {
              disable.mutate();
            }
          }}
        >
          {passwordField}
          {codeField("mfa-disable-code", "Code from the app, or a recovery code")}
          <div className="flex gap-2">
            <button type="submit" className={PRIMARY} disabled={busy || !password || !code}>
              Turn off two-factor sign-in
            </button>
            <button type="button" className={SECONDARY} onClick={() => reset()}>
              Cancel
            </button>
          </div>
        </form>
      )}
    </section>
  );
};

export default TwoFactorPanel;

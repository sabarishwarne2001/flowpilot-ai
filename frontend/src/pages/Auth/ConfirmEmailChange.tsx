import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";

import { confirmEmailChange } from "@/services/api/emailChange";
import { useAuthStore } from "@/store/useAuthStore";

export const ConfirmEmailChange: React.FC = () => {
  const clearAuth = useAuthStore((state) => state.clearAuth);
  const [state, setState] = useState<"working" | "done" | "failed">("working");
  const [message, setMessage] = useState("");
  const [email, setEmail] = useState("");
  const fired = useRef(false);

  useEffect(() => {
    if (fired.current) {return;}
    fired.current = true;

    const token = new URLSearchParams(
      window.location.hash.replace(/^#/, ""),
    ).get("token");

    window.history.replaceState(null, "", window.location.pathname);

    if (!token) {
      setState("failed");
      setMessage(
        "This link is missing its confirmation token. Open the link from your email directly rather than copying part of it.",
      );
      return;
    }

    confirmEmailChange(token)
      .then((result) => {
        setEmail(result.email);
        setMessage(result.detail);
        setState("done");
        clearAuth();
      })
      .catch((error: unknown) => {
        const detail = (error as { response?: { data?: { detail?: unknown } } })
          ?.response?.data?.detail;
        setMessage(
          typeof detail === "string"
            ? detail
            : "This confirmation link is invalid or has expired. Request a new email change from your profile settings.",
        );
        setState("failed");
      });
  }, [clearAuth]);

  return (
    <div className="mx-auto max-w-md p-6">
      <div className="rounded-xl border border-border bg-card p-6 text-center shadow-sm">
        {state === "working" && (
          <>
            <Loader2 className="mx-auto h-8 w-8 animate-spin text-muted-foreground" />
            <p className="mt-3 text-sm text-muted-foreground">
              Confirming your new email address…
            </p>
          </>
        )}

        {state === "done" && (
          <>
            <CheckCircle2 className="mx-auto h-10 w-10 text-primary" />
            <h1 className="mt-3 text-lg font-bold text-foreground">Email address updated</h1>
            <p className="mt-1.5 text-sm text-muted-foreground">
              You&apos;ll sign in with <strong>{email}</strong> from now on.{" "}
              {message || "Every device was signed out, so you'll need to sign in again."}
            </p>
            <Link
              to="/login"
              className="mt-5 inline-block rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:opacity-90"
            >
              Sign in
            </Link>
          </>
        )}

        {state === "failed" && (
          <>
            <XCircle className="mx-auto h-10 w-10 text-muted-foreground" />
            <h1 className="mt-3 text-lg font-bold text-foreground">
              That link didn&apos;t work
            </h1>
            <p role="alert" className="mt-1.5 text-sm text-muted-foreground">
              {message}
            </p>
            <Link
              to="/login"
              className="mt-5 inline-block rounded-lg border border-border bg-background px-4 py-2 text-sm font-semibold text-foreground hover:bg-muted"
            >
              Back to sign in
            </Link>
          </>
        )}
      </div>
    </div>
  );
};

export default ConfirmEmailChange;

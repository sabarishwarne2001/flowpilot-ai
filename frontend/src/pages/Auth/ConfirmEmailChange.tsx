import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";

import { confirmEmailChange } from "@/services/api/emailChange";
import { useAuthStore } from "@/store/useAuthStore";
import { errorMessage } from "@/services/api/errors";
import { AuthShell } from "@/components/auth/AuthShell";

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
        setMessage(errorMessage(error, "This confirmation link is invalid or has expired. Request a new email change from your profile settings."));
        setState("failed");
      });
  }, [clearAuth]);

  return (
    <AuthShell>
      <div className="py-2 text-center animate-fade-in">
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
            <h1 className="mt-3 text-[22px] font-semibold tracking-tight text-foreground">Email address updated</h1>
            <p className="mt-1.5 text-sm text-muted-foreground">
              You&apos;ll sign in with <strong>{email}</strong> from now on.{" "}
              {message || "Every device was signed out, so you'll need to sign in again."}
            </p>
            <Link
              to="/login"
              className="fp-btn fp-btn-primary mt-5 h-10 px-5"
            >
              Sign in
            </Link>
          </>
        )}

        {state === "failed" && (
          <>
            <XCircle className="mx-auto h-10 w-10 text-muted-foreground" />
            <h1 className="mt-3 text-[22px] font-semibold tracking-tight text-foreground">
              That link didn&apos;t work
            </h1>
            <p role="alert" className="mt-1.5 text-sm text-muted-foreground">
              {message}
            </p>
            <Link
              to="/login"
              className="fp-btn fp-btn-secondary mt-5 h-10 px-5"
            >
              Back to sign in
            </Link>
          </>
        )}
      </div>
    </AuthShell>
  );
};

export default ConfirmEmailChange;

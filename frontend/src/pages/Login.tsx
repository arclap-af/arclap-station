import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "../lib/api";
import { auth } from "../lib/bridge/auth";

export function Login() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const next = params.get("next") || "/home";
  const queryClient = useQueryClient();
  const { data: session } = useQuery({ queryKey: ["auth.session"], queryFn: auth.session });
  const [pin, setPin] = useState<string[]>(["", "", "", "", "", ""]);
  const [error, setError] = useState<string | null>(null);
  const refs = useRef<Array<HTMLInputElement | null>>([]);

  useEffect(() => {
    refs.current[0]?.focus();
  }, []);

  useEffect(() => {
    if (session?.logged_in) {
      navigate(next, { replace: true });
    }
  }, [session, navigate, next]);

  // Live lockout countdown — backend exposes lockout_seconds_remaining
  // on /auth/status. When >0 we disable input and show a timer.
  const [lockoutLeft, setLockoutLeft] = useState<number>(
    session?.lockout_seconds_remaining ?? 0,
  );
  useEffect(() => {
    setLockoutLeft(session?.lockout_seconds_remaining ?? 0);
  }, [session?.lockout_seconds_remaining]);
  useEffect(() => {
    if (lockoutLeft <= 0) return;
    const t = setInterval(() => {
      setLockoutLeft((v) => (v > 0 ? v - 1 : 0));
    }, 1000);
    return () => clearInterval(t);
  }, [lockoutLeft]);

  const login = useMutation({
    mutationFn: (code: string) => auth.login(code),
    onSuccess: (s) => {
      queryClient.setQueryData(["auth.session"], s);
      navigate(next, { replace: true });
    },
    onError: (err) => {
      let msg = "Login failed · try again";
      if (err instanceof ApiError) {
        if (err.status === 401) msg = "Wrong PIN · try again";
        else if (err.status === 429) {
          // Body may carry "locked out; retry in Ns" — parse N.
          const detail =
            err.body && typeof err.body === "object" && "detail" in err.body
              ? String((err.body as { detail?: unknown }).detail ?? "")
              : "";
          const m = /retry in (\d+)/.exec(detail);
          if (m) setLockoutLeft(parseInt(m[1], 10));
          msg = "Too many wrong PIN attempts · locked out";
        }
      }
      setError(msg);
      setPin(["", "", "", "", "", ""]);
      refs.current[0]?.focus();
    },
  });

  const submit = (digits: string[]) => {
    const code = digits.join("");
    if (code.length === 6) login.mutate(code);
  };

  const setDigit = (i: number, raw: string) => {
    const v = raw.replace(/\D/g, "").slice(-1);
    const next = [...pin];
    next[i] = v;
    setPin(next);
    setError(null);
    if (v && i < 5) refs.current[i + 1]?.focus();
    if (i === 5 && v) submit(next);
  };

  const onKey = (i: number, e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Backspace" && !pin[i] && i > 0) refs.current[i - 1]?.focus();
  };

  return (
    <div className="as-login">
      <div className="as-login-card">
        <div className="as-login-brand" aria-hidden>A</div>
        <div className="as-login-eyebrow">Arclap Station</div>
        <h2 className="as-login-title">Enter PIN</h2>
        <div style={{ display: "flex", justifyContent: "center" }}>
          <div className="as-login-host">
            <span className="dot" /> {window.location.host}
          </div>
        </div>
        <div className="as-pin-row">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <input
              key={i}
              ref={(el) => {
                refs.current[i] = el;
              }}
              className={`as-pin-input${error && lockoutLeft === 0 ? " err" : ""}`}
              maxLength={1}
              type="password"
              inputMode="numeric"
              autoComplete="off"
              value={pin[i]}
              aria-label={`PIN digit ${i + 1}`}
              onChange={(e) => setDigit(i, e.target.value)}
              onKeyDown={(e) => onKey(i, e)}
              disabled={login.isPending || lockoutLeft > 0}
            />
          ))}
        </div>
        {lockoutLeft > 0 ? (
          <div className="as-login-msg warn" role="alert" style={{ fontVariantNumeric: "tabular-nums" }}>
            Locked out · retry in {Math.floor(lockoutLeft / 60)}m {lockoutLeft % 60}s
          </div>
        ) : error ? (
          <div className="as-login-msg err">{error}</div>
        ) : login.isPending ? (
          <div className="as-login-msg dim">Verifying…</div>
        ) : (
          <div className="as-login-msg dim">6-digit numeric PIN</div>
        )}
      </div>
    </div>
  );
}

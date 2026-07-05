"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Activity, Zap } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { login, register } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "register") await register(email, password);
      await login(email, password);
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const demoLogin = async () => {
    setError(null);
    setBusy(true);
    try {
      await login("demo@example.com", "demo12345");
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <div className="mb-1 flex items-center gap-2">
            <Activity className="h-5 w-5 text-accent" aria-hidden />
            <CardTitle className="text-base">Chronos</CardTitle>
          </div>
          <p className="text-xs text-secondary">
            {mode === "login"
              ? "Sign in to manage your jobs."
              : "Create an account to start scheduling jobs."}
          </p>
        </CardHeader>
        <CardContent>
          {/* --- Demo Login --- */}
          <Button
            type="button"
            variant="outline"
            className="mb-4 w-full gap-2 border-accent/30 bg-accent/5 text-accent hover:bg-accent/10 hover:text-accent"
            disabled={busy}
            onClick={demoLogin}
          >
            <Zap className="h-4 w-4" />
            {busy ? "Signing in…" : "Demo Login"}
          </Button>
          <div className="relative mb-4">
            <div className="absolute inset-0 flex items-center">
              <span className="w-full border-t" />
            </div>
            <div className="relative flex justify-center text-xs uppercase">
              <span className="bg-card px-2 text-muted">or use credentials</span>
            </div>
          </div>

          <form onSubmit={submit} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                required
                placeholder="you@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                required
                minLength={8}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
            {error && (
              <p className="rounded-md bg-[var(--wash-failed)] px-3 py-2 text-xs">
                {error}
              </p>
            )}
            <Button type="submit" className="w-full" disabled={busy}>
              {busy ? "Working…" : mode === "login" ? "Sign in" : "Create account"}
            </Button>
          </form>
          <button
            className="mt-4 w-full text-center text-xs text-secondary hover:text-foreground"
            onClick={() => setMode(mode === "login" ? "register" : "login")}
          >
            {mode === "login"
              ? "No account? Register instead"
              : "Have an account? Sign in"}
          </button>
        </CardContent>
      </Card>
    </main>
  );
}

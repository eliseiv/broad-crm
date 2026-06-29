import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, ArrowRight, Pencil } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { ApiError } from '@/lib/api';
import { useLogin } from '@/features/auth/hooks';
import { useAuthStore } from '@/store/auth';

type Step = 'username' | 'password';
const GENERIC_ERROR = 'Неверный логин или пароль';

export function LoginPage() {
  const navigate = useNavigate();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const loginMutation = useLogin();

  const [step, setStep] = useState<Step>('username');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [usernameError, setUsernameError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [shake, setShake] = useState(false);
  const passwordRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isAuthenticated) navigate('/servers', { replace: true });
  }, [isAuthenticated, navigate]);

  useEffect(() => {
    if (step === 'password') passwordRef.current?.focus();
  }, [step]);

  const triggerShake = () => {
    setShake(true);
    window.setTimeout(() => setShake(false), 450);
  };

  const handleNext = (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim()) {
      setUsernameError('Введите логин');
      return;
    }
    setUsernameError(null);
    setStep('password');
  };

  const handleBack = () => {
    setStep('username');
    setPassword('');
    setFormError(null);
  };

  const handleLogin = (e: React.FormEvent) => {
    e.preventDefault();
    if (!password) {
      setFormError(GENERIC_ERROR);
      triggerShake();
      return;
    }
    setFormError(null);
    loginMutation.mutate(
      { username: username.trim(), password },
      {
        onSuccess: () => navigate('/servers', { replace: true }),
        onError: (err) => {
          if (err instanceof ApiError && err.status === 429) {
            setFormError('Слишком много попыток входа. Попробуйте позже.');
          } else {
            setFormError(GENERIC_ERROR);
          }
          triggerShake();
        },
      },
    );
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-bg-base px-4">
      <div className="w-full max-w-sm">
        <div className="rounded-card border border-border-subtle bg-surface-1 p-6 shadow-card">
          {step === 'username' ? (
            <form onSubmit={handleNext} className="flex flex-col gap-4" noValidate>
              <Input
                label="Логин"
                placeholder="admin"
                value={username}
                error={usernameError}
                autoFocus
                autoComplete="username"
                onChange={(e) => {
                  setUsername(e.target.value);
                  if (usernameError) setUsernameError(null);
                }}
              />
              <Button type="submit" fullWidth>
                Далее
                <ArrowRight className="h-4 w-4" />
              </Button>
            </form>
          ) : (
            <form
              onSubmit={handleLogin}
              className={`flex flex-col gap-4 ${shake ? 'animate-shake' : ''}`}
              noValidate
            >
              <div className="flex items-center justify-between rounded-[10px] border border-border-subtle bg-surface-2 px-3 py-2">
                <span className="flex flex-col">
                  <span className="text-[11px] uppercase tracking-wide text-text-tertiary">
                    Логин
                  </span>
                  <span className="font-mono text-sm text-text-primary">{username}</span>
                </span>
                <button
                  type="button"
                  onClick={handleBack}
                  disabled={loginMutation.isPending}
                  className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[12px] text-text-secondary transition-colors hover:bg-surface-3 hover:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50"
                >
                  <Pencil className="h-3 w-3" />
                  Сменить
                </button>
              </div>

              <Input
                ref={passwordRef}
                label="Пароль"
                type="password"
                placeholder="••••••••"
                value={password}
                autoComplete="current-password"
                onChange={(e) => {
                  setPassword(e.target.value);
                  if (formError) setFormError(null);
                }}
              />

              {formError && (
                <p role="alert" className="text-[13px] text-status-red">
                  {formError}
                </p>
              )}

              <div className="flex gap-2">
                <Button
                  type="button"
                  variant="ghost"
                  onClick={handleBack}
                  disabled={loginMutation.isPending}
                >
                  <ArrowLeft className="h-4 w-4" />
                  Назад
                </Button>
                <Button type="submit" fullWidth loading={loginMutation.isPending}>
                  Войти
                </Button>
              </div>
            </form>
          )}
        </div>
      </div>
    </main>
  );
}

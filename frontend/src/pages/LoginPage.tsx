import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, ArrowRight, Eye, EyeOff, Pencil } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { ApiError } from '@/lib/api';
import { useLogin, useSetPassword } from '@/features/auth/hooks';
import { useAuthStore } from '@/store/auth';

type Step = 'username' | 'password';
const GENERIC_ERROR = 'Неверный логин или пароль';

/**
 * Двухшаговый вход (08-design-system.md «Экран входа», ADR-029). Идентификатор =
 * логин ИЛИ телеграм-ник (ADR-025). На шаге «Логин» кнопка «Далее» делает пробный
 * `login({username, password:''})`: если ответ `password_setup_required` —
 * беспарольный пользователь сразу видит окно «Придумайте пароль» (БЕЗ показа поля
 * пароля), где сам придумывает пароль ≥8 → POST /api/auth/set-password → залогинен.
 * Иначе (у пользователя есть пароль → сервер отклоняет пустой пароль) —
 * показывается шаг «Пароль», где пользователь вводит пароль и жмёт «Войти».
 */
export function LoginPage() {
  const navigate = useNavigate();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const loginMutation = useLogin();
  const setPasswordMutation = useSetPassword();

  const [step, setStep] = useState<Step>('username');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [usernameError, setUsernameError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [shake, setShake] = useState(false);
  const passwordRef = useRef<HTMLInputElement>(null);

  // Кросс-шаговое уведомление (напр. «Сессия установки истекла» после закрытия окна).
  const [banner, setBanner] = useState<string | null>(null);

  // Окно «Придумайте пароль» (открыто ⇔ setupToken !== null, ADR-029).
  const [setupToken, setSetupToken] = useState<string | null>(null);
  const [newPassword, setNewPassword] = useState('');
  const [newPasswordError, setNewPasswordError] = useState<string | null>(null);
  const [setupError, setSetupError] = useState<string | null>(null);
  const [showNewPassword, setShowNewPassword] = useState(false);

  useEffect(() => {
    // Permission-aware дефолт: index `/` резолвит целевой раздел по правам
    // (08-design-system.md «Дефолтный маршрут после логина»).
    if (isAuthenticated) navigate('/', { replace: true });
  }, [isAuthenticated, navigate]);

  useEffect(() => {
    if (step === 'password') passwordRef.current?.focus();
  }, [step]);

  const triggerShake = () => {
    setShake(true);
    window.setTimeout(() => setShake(false), 450);
  };

  const openSetup = (token: string) => {
    setNewPassword('');
    setNewPasswordError(null);
    setSetupError(null);
    setSetupToken(token);
  };

  const handleNext = (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim()) {
      setUsernameError('Введите логин или телеграм');
      return;
    }
    setUsernameError(null);
    setFormError(null);
    setBanner(null);
    // Пробный вход с пустым паролем (ADR-029): беспарольный пользователь →
    // `password_setup_required` → сразу окно «Придумайте пароль» (поле пароля не
    // показываем); пользователь с паролем → сервер отклоняет пустой пароль →
    // переходим на шаг «Пароль», где он вводит свой пароль.
    loginMutation.mutate(
      { username: username.trim(), password: '' },
      {
        onSuccess: (data) => {
          if (data.password_setup_required) openSetup(data.setup_token);
          else navigate('/', { replace: true });
        },
        onError: (err) => {
          if (err instanceof ApiError && err.status === 429) {
            setUsernameError('Слишком много попыток входа. Попробуйте позже.');
            return;
          }
          // Пустой пароль отклонён (401) / прочая ошибка → у пользователя есть
          // пароль: показываем шаг «Пароль» для ручного ввода.
          setStep('password');
        },
      },
    );
  };

  const handleBack = () => {
    setStep('username');
    setPassword('');
    setFormError(null);
    setBanner(null);
  };

  const handleLogin = (e: React.FormEvent) => {
    e.preventDefault();
    // Пароль на клиенте НЕ обязателен (ADR-025): беспарольный пользователь
    // оставляет поле пустым → сервер вернёт password_setup_required.
    setFormError(null);
    setBanner(null);
    loginMutation.mutate(
      { username: username.trim(), password },
      {
        onSuccess: (data) => {
          // Резервный путь: если беспарольный пользователь дошёл до шага «Пароль»
          // и отправил пустой пароль — открываем окно «Придумайте пароль».
          if (data.password_setup_required) openSetup(data.setup_token);
          else navigate('/', { replace: true });
        },
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

  const closeSetup = () => {
    if (setPasswordMutation.isPending) return;
    setSetupToken(null);
  };

  const handleSetPassword = (e: React.FormEvent) => {
    e.preventDefault();
    if (newPassword.length < 8) {
      setNewPasswordError('Не менее 8 символов');
      return;
    }
    if (!setupToken) return;
    setNewPasswordError(null);
    setSetupError(null);
    setPasswordMutation.mutate(
      { payload: { password: newPassword }, setupToken, username: username.trim() },
      {
        onSuccess: () => {
          setSetupToken(null);
          navigate('/', { replace: true });
        },
        onError: (err) => {
          if (err instanceof ApiError && err.status === 422) {
            setNewPasswordError('Не менее 8 символов');
            return;
          }
          if (err instanceof ApiError && err.code === 'password_already_set') {
            // Пароль уже задан (гонка/повтор) — вернуться к вводу пароля.
            setSetupToken(null);
            setPassword('');
            setBanner('Пароль уже задан, войдите с паролем');
            return;
          }
          if (err instanceof ApiError && err.status === 401) {
            // Setup-токен просрочен — начать вход заново.
            setSetupToken(null);
            setPassword('');
            setStep('username');
            setBanner('Сессия установки истекла, начните заново');
            return;
          }
          setSetupError('Не удалось сохранить пароль. Повторите попытку.');
        },
      },
    );
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-bg-base px-4">
      <div className="w-full max-w-sm">
        <div className="rounded-card border border-border-subtle bg-surface-1 p-6 shadow-card">
          {banner && (
            <p role="alert" className="mb-4 text-[13px] text-status-red">
              {banner}
            </p>
          )}
          {step === 'username' ? (
            <form onSubmit={handleNext} className="flex flex-col gap-4" noValidate>
              <Input
                label="Логин или Телеграм"
                placeholder="admin или @username"
                value={username}
                error={usernameError}
                autoFocus
                autoComplete="username"
                disabled={loginMutation.isPending}
                onChange={(e) => {
                  setUsername(e.target.value);
                  if (usernameError) setUsernameError(null);
                  if (banner) setBanner(null);
                }}
              />
              <Button type="submit" fullWidth loading={loginMutation.isPending}>
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
                <span className="flex min-w-0 flex-col">
                  <span className="text-[11px] uppercase tracking-wide text-text-tertiary">
                    Логин или Телеграм
                  </span>
                  <span className="truncate font-mono text-sm text-text-primary">{username}</span>
                </span>
                <button
                  type="button"
                  onClick={handleBack}
                  disabled={loginMutation.isPending}
                  className="inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-[12px] text-text-secondary transition-colors hover:bg-surface-3 hover:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50"
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

      <Modal
        open={setupToken !== null}
        onOpenChange={(next) => !next && closeSetup()}
        title="Придумайте пароль"
        description="У вашей учётной записи ещё нет пароля. Придумайте его для входа."
        dismissible={!setPasswordMutation.isPending}
        footer={
          <>
            <Button variant="ghost" onClick={closeSetup} disabled={setPasswordMutation.isPending}>
              Отмена
            </Button>
            <Button type="submit" form="set-password-form" loading={setPasswordMutation.isPending}>
              Сохранить
            </Button>
          </>
        }
      >
        <form
          id="set-password-form"
          onSubmit={handleSetPassword}
          className="flex flex-col gap-4"
          noValidate
        >
          <Input
            label="Новый пароль"
            type={showNewPassword ? 'text' : 'password'}
            placeholder="Не менее 8 символов"
            value={newPassword}
            error={newPasswordError}
            autoFocus
            maxLength={128}
            autoComplete="new-password"
            onChange={(e) => {
              setNewPassword(e.target.value);
              if (newPasswordError) setNewPasswordError(null);
              if (setupError) setSetupError(null);
            }}
            trailing={
              <button
                type="button"
                onClick={() => setShowNewPassword((v) => !v)}
                aria-label={showNewPassword ? 'Скрыть пароль' : 'Показать пароль'}
                className="flex h-7 w-7 items-center justify-center rounded-md text-text-tertiary transition-colors hover:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              >
                {showNewPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            }
          />
          {setupError && (
            <p role="alert" className="text-[13px] text-status-red">
              {setupError}
            </p>
          )}
        </form>
      </Modal>
    </main>
  );
}

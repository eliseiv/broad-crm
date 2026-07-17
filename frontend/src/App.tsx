import { lazy, Suspense } from 'react';
import { Route, Routes } from 'react-router-dom';
import { AppLayout } from '@/components/AppLayout';
import { Spinner } from '@/components/ui/Spinner';
import { AiKeysPage } from '@/pages/AiKeysPage';
import { BackendsPage } from '@/pages/BackendsPage';
import { DashboardPage } from '@/pages/DashboardPage';
import { LoginPage } from '@/pages/LoginPage';
import { MailMiniAppPage } from '@/pages/MailMiniAppPage';
import { MailPage } from '@/pages/MailPage';
import { ProxiesPage } from '@/pages/ProxiesPage';
import { RolesPage } from '@/pages/RolesPage';
import { ServersPage } from '@/pages/ServersPage';
import { SmsMiniAppPage } from '@/pages/SmsMiniAppPage';
import { SmsPage } from '@/pages/SmsPage';
import { TeamsPage } from '@/pages/TeamsPage';
import { UsersPage } from '@/pages/UsersPage';
import { AdminRoute } from '@/routes/AdminRoute';
import { DefaultRoute } from '@/routes/DefaultRoute';
import { ProtectedRoute } from '@/routes/ProtectedRoute';

/**
 * «Документы» — lazy-route (ADR-062 §Последствия): WYSIWYG-редактор (TipTap/ProseMirror)
 * увеличивает бандл, поэтому его чанк грузится только при заходе на `/documents`, а не в
 * основном бандле.
 */
const DocumentsPage = lazy(() =>
  import('@/pages/DocumentsPage').then((m) => ({ default: m.DocumentsPage })),
);

/** Fallback загрузки lazy-маршрута (по центру доступной области). */
function RouteFallback() {
  return (
    <div className="flex h-full min-h-[40vh] items-center justify-center">
      <Spinner className="text-text-secondary" />
    </div>
  );
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      {/* Операторская Telegram Mini App (ADR-031): ПУБЛИЧНЫЙ маршрут вне
          `AppLayout` и вне `ProtectedRoute`/RBAC-guard — без redirect на /login,
          без nav-shell. Вход — только по кнопке Telegram-бота; в меню/DefaultRoute
          не участвует. Беспарольный SSO + изолированный auth-стор (miniAppAuth). */}
      <Route path="/tg/sms" element={<SmsMiniAppPage />} />
      {/* Telegram Mini App почты (ADR-044 §7): ПУБЛИЧНЫЙ маршрут вне `AppLayout`/
          `ProtectedRoute` — без экрана логина и redirect на /login. Вход — по кнопке
          Telegram-бота; беспарольный SSO (`initData`) + изолированный auth-стор. */}
      <Route path="/tg/mail" element={<MailMiniAppPage />} />
      <Route element={<ProtectedRoute />}>
        <Route element={<AppLayout />}>
          {/* Index и fallback — permission-aware дефолт (08-design-system.md «Гейтинг»). */}
          <Route index element={<DefaultRoute />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/servers" element={<ServersPage />} />
          <Route path="/ai-keys" element={<AiKeysPage />} />
          <Route path="/proxies" element={<ProxiesPage />} />
          <Route path="/backends" element={<BackendsPage />} />
          <Route path="/mail" element={<MailPage />} />
          {/* «СМС» — категория «Агрегатор», не-full-bleed; page-level view-guard
              `sms:view` внутри страницы (ADR-030, 08-design-system.md «Страница СМС»). */}
          <Route path="/sms" element={<SmsPage />} />
          {/* «Роли»/«Команды» — page-level view-guard roles:view/teams:view внутри
              страниц (ADR-022, 08-design-system.md). */}
          <Route path="/roles" element={<RolesPage />} />
          <Route path="/teams" element={<TeamsPage />} />
          {/* «Документы» — второй full-bleed маршрут (ADR-061); двухпанельный
              сайдбар-shell внутри страницы. Page-level view-guard documents:view.
              Lazy-route (ADR-062): чанк редактора вне основного бандла. */}
          <Route
            path="/documents"
            element={
              <Suspense fallback={<RouteFallback />}>
                <DocumentsPage />
              </Suspense>
            }
          />
          {/* Страница «Пользователи» — admin-only (ADR-021, 08-design-system.md). */}
          <Route element={<AdminRoute />}>
            <Route path="/users" element={<UsersPage />} />
          </Route>
          <Route path="*" element={<DefaultRoute />} />
        </Route>
      </Route>
    </Routes>
  );
}

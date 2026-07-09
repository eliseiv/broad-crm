import { Route, Routes } from 'react-router-dom';
import { AppLayout } from '@/components/AppLayout';
import { AiKeysPage } from '@/pages/AiKeysPage';
import { BackendsPage } from '@/pages/BackendsPage';
import { DashboardPage } from '@/pages/DashboardPage';
import { LoginPage } from '@/pages/LoginPage';
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

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      {/* Операторская Telegram Mini App (ADR-031): ПУБЛИЧНЫЙ маршрут вне
          `AppLayout` и вне `ProtectedRoute`/RBAC-guard — без redirect на /login,
          без nav-shell. Вход — только по кнопке Telegram-бота; в меню/DefaultRoute
          не участвует. Беспарольный SSO + изолированный auth-стор (miniAppAuth). */}
      <Route path="/tg/sms" element={<SmsMiniAppPage />} />
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

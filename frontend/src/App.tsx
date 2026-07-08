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
import { TeamsPage } from '@/pages/TeamsPage';
import { UsersPage } from '@/pages/UsersPage';
import { AdminRoute } from '@/routes/AdminRoute';
import { DefaultRoute } from '@/routes/DefaultRoute';
import { ProtectedRoute } from '@/routes/ProtectedRoute';

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
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

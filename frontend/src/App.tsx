import { Navigate, Route, Routes } from 'react-router-dom';
import { AppLayout } from '@/components/AppLayout';
import { AiKeysPage } from '@/pages/AiKeysPage';
import { LoginPage } from '@/pages/LoginPage';
import { ServersPage } from '@/pages/ServersPage';
import { ProtectedRoute } from '@/routes/ProtectedRoute';

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedRoute />}>
        <Route element={<AppLayout />}>
          <Route path="/servers" element={<ServersPage />} />
          <Route path="/ai-keys" element={<AiKeysPage />} />
        </Route>
      </Route>
      <Route path="/" element={<Navigate to="/servers" replace />} />
      <Route path="*" element={<Navigate to="/servers" replace />} />
    </Routes>
  );
}

import { Navigate, Route, Routes } from 'react-router-dom';
import { LoginPage } from '@/pages/LoginPage';
import { ServersPage } from '@/pages/ServersPage';
import { ProtectedRoute } from '@/routes/ProtectedRoute';

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedRoute />}>
        <Route path="/servers" element={<ServersPage />} />
      </Route>
      <Route path="/" element={<Navigate to="/servers" replace />} />
      <Route path="*" element={<Navigate to="/servers" replace />} />
    </Routes>
  );
}

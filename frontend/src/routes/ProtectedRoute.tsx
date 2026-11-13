import { Navigate, Outlet } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

interface Props { requiredRole?: 'buyer' | 'seller'; }

export function ProtectedRoute({ requiredRole }: Props) {
  const { user, loading } = useAuth();
  if (loading) return <div className="min-h-screen flex items-center justify-center"><span className="text-slate-400">Loading…</span></div>;
  if (!user) return <Navigate to="/login" replace />;
  if (requiredRole && user.primary_role !== requiredRole) return <Navigate to={user.primary_role === 'buyer' ? '/buyer' : '/seller'} replace />;
  return <Outlet />;
}

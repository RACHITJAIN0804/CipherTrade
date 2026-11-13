import { Outlet, NavLink, useNavigate } from 'react-router-dom';
import { LayoutDashboard, FileText, ArrowLeftRight, MessageSquare, ShoppingBag, LogOut, Menu, X } from 'lucide-react';
import { useState } from 'react';
import { Logo } from '../components/Logo';
import { useAuth } from '../context/AuthContext';

const navItems = [
  { to: '/seller', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/seller/marketplace', label: 'Browse Listings', icon: ShoppingBag },
  { to: '/seller/offers', label: 'My Offers', icon: FileText },
  { to: '/seller/transactions', label: 'Transactions', icon: ArrowLeftRight },
  { to: '/seller/conversations', label: 'Messages', icon: MessageSquare },
];

export function SellerLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  function handleLogout() { logout(); navigate('/'); }

  const sidebar = (
    <div className="flex flex-col h-full">
      <div className="p-5 border-b" style={{ borderColor: 'rgba(59,130,246,0.15)' }}>
        <Logo size="sm" />
      </div>
      <nav className="flex-1 p-3 overflow-y-auto">
        {navItems.map(({ to, label, icon: Icon }) => (
          <NavLink key={to} to={to} end={to === '/seller'}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2.5 rounded-lg mb-1 text-sm font-medium transition-all ${isActive ? 'bg-blue-600/20 text-blue-300 border border-blue-500/30' : 'text-slate-400 hover:text-slate-200 hover:bg-white/5 border border-transparent'}`
            }
          >
            <Icon className="w-4 h-4 flex-shrink-0" />{label}
          </NavLink>
        ))}
      </nav>
      <div className="p-4 border-t" style={{ borderColor: 'rgba(59,130,246,0.15)' }}>
        <div className="flex items-center gap-3 mb-3 px-1">
          <div className="w-8 h-8 rounded-full bg-cyan-600 flex items-center justify-center text-white text-sm font-bold">
            {user?.display_name[0]?.toUpperCase()}
          </div>
          <div className="min-w-0">
            <p className="text-sm font-medium text-slate-200 truncate">{user?.display_name}</p>
            <p className="text-xs text-slate-500 capitalize">{user?.primary_role}</p>
          </div>
        </div>
        <button onClick={handleLogout} className="flex items-center gap-2 w-full px-3 py-2 text-sm text-slate-500 hover:text-red-400 hover:bg-red-500/10 rounded-lg transition-all">
          <LogOut className="w-4 h-4" />Sign out
        </button>
      </div>
    </div>
  );

  return (
    <div className="flex h-screen overflow-hidden">
      <aside className="hidden md:flex w-60 flex-shrink-0 flex-col border-r" style={{ background: '#0a1628', borderColor: 'rgba(59,130,246,0.15)' }}>
        {sidebar}
      </aside>
      {sidebarOpen && (
        <div className="fixed inset-0 z-50 md:hidden">
          <div className="absolute inset-0 bg-black/60" onClick={() => setSidebarOpen(false)} />
          <aside className="relative w-60 h-full flex flex-col border-r" style={{ background: '#0a1628', borderColor: 'rgba(59,130,246,0.15)' }}>
            <button className="absolute top-4 right-4 text-slate-400 hover:text-white" onClick={() => setSidebarOpen(false)}><X className="w-5 h-5" /></button>
            {sidebar}
          </aside>
        </div>
      )}
      <div className="flex-1 flex flex-col overflow-hidden">
        <header className="md:hidden h-14 flex items-center px-4 border-b" style={{ background: '#0a1628', borderColor: 'rgba(59,130,246,0.15)' }}>
          <button onClick={() => setSidebarOpen(true)} className="text-slate-400 hover:text-white mr-3"><Menu className="w-5 h-5" /></button>
          <Logo size="sm" />
        </header>
        <main className="flex-1 overflow-y-auto p-6" style={{ background: '#050c1a' }}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}

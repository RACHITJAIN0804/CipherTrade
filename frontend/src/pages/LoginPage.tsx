import { useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Eye, EyeOff, Mail, Lock } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { Logo } from '../components/Logo';

export function LoginPage() {
  const { login, loading, error } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [localError, setLocalError] = useState('');

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setLocalError('');
    if (!email || !password) { setLocalError('Please fill in all fields.'); return; }
    try {
      await login(email, password);
      // Navigate based on role - getMe is called inside login
      // We need to get role from auth context after login
      navigate('/buyer'); // Will be corrected by router redirect based on role
    } catch {
      // error handled by AuthContext
    }
  }

  // After login, redirect to correct dashboard
  // We'll improve by checking user role after context updates
  const { user } = useAuth();
  if (user) {
    // Already logged in - navigate away
    if (typeof window !== 'undefined') {
      const dest = user.primary_role === 'seller' ? '/seller' : '/buyer';
      navigate(dest, { replace: true });
    }
  }

  const displayError = localError || error;

  return (
    <div className="min-h-screen flex items-center justify-center px-4" style={{ background: '#050c1a' }}>
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <div className="flex justify-center mb-4"><Logo size="lg" /></div>
          <h1 className="text-2xl font-bold text-white mb-1">Welcome back</h1>
          <p className="text-slate-400 text-sm">Sign in to your CipherTrade account</p>
        </div>
        <form onSubmit={handleSubmit} className="rounded-xl border p-6 space-y-4" style={{ background: 'rgba(15,32,68,0.6)', borderColor: 'rgba(59,130,246,0.2)' }}>
          {displayError && <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">{displayError}</div>}
          <div>
            <label className="block text-sm font-medium text-slate-300 mb-1.5">Email</label>
            <div className="relative">
              <Mail className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
              <input type="email" value={email} onChange={e => setEmail(e.target.value)} placeholder="you@example.com" required
                className="w-full pl-9 pr-3 py-2.5 rounded-lg text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-2 transition-all"
                style={{ background: 'rgba(15,32,68,0.8)', border: '1px solid rgba(59,130,246,0.2)', focusRingColor: 'rgba(37,99,235,0.4)' } as React.CSSProperties} />
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-300 mb-1.5">Password</label>
            <div className="relative">
              <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
              <input type={showPw ? 'text' : 'password'} value={password} onChange={e => setPassword(e.target.value)} placeholder="••••••••" required
                className="w-full pl-9 pr-10 py-2.5 rounded-lg text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-2 transition-all"
                style={{ background: 'rgba(15,32,68,0.8)', border: '1px solid rgba(59,130,246,0.2)' }} />
              <button type="button" onClick={() => setShowPw(p => !p)} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300">
                {showPw ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </div>
          <button type="submit" disabled={loading}
            className="w-full py-2.5 rounded-lg font-semibold text-white transition-all hover:opacity-90 disabled:opacity-50"
            style={{ background: 'linear-gradient(135deg, #2563eb, #00d4ff)' }}>
            {loading ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
        <p className="text-center text-sm text-slate-500 mt-6">
          Don't have an account?{' '}
          <Link to="/register" className="font-medium hover:text-white transition-colors" style={{ color: '#00d4ff' }}>Create one</Link>
        </p>
      </div>
    </div>
  );
}

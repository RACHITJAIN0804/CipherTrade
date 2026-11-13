import { useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Eye, EyeOff, Mail, Lock, User, ShoppingBag, Store } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { Logo } from '../components/Logo';
import type { UserRole } from '../types';

export function RegisterPage() {
  const { register, loading, error } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ email: '', display_name: '', password: '', primary_role: 'buyer' as UserRole });
  const [showPw, setShowPw] = useState(false);
  const [localError, setLocalError] = useState('');

  function set(field: string, value: string) { setForm(f => ({ ...f, [field]: value })); }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setLocalError('');
    if (form.password.length < 8) { setLocalError('Password must be at least 8 characters.'); return; }
    if (form.display_name.length < 2) { setLocalError('Display name must be at least 2 characters.'); return; }
    try {
      await register(form);
      // After register, redirect to login
      navigate('/login', { state: { registered: true } });
    } catch {
      // error from context
    }
  }

  const displayError = localError || error;

  return (
    <div className="min-h-screen flex items-center justify-center px-4 py-8" style={{ background: '#050c1a' }}>
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <div className="flex justify-center mb-4"><Logo size="lg" /></div>
          <h1 className="text-2xl font-bold text-white mb-1">Create your account</h1>
          <p className="text-slate-400 text-sm">Join the CipherTrade marketplace</p>
        </div>
        <form onSubmit={handleSubmit} className="rounded-xl border p-6 space-y-4" style={{ background: 'rgba(15,32,68,0.6)', borderColor: 'rgba(59,130,246,0.2)' }}>
          {displayError && <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">{displayError}</div>}
          
          {/* Role selection */}
          <div>
            <label className="block text-sm font-medium text-slate-300 mb-2">I want to</label>
            <div className="grid grid-cols-2 gap-2">
              {(['buyer', 'seller'] as UserRole[]).map(role => (
                <button key={role} type="button" onClick={() => set('primary_role', role)}
                  className={`flex flex-col items-center gap-1.5 p-3 rounded-lg border transition-all ${form.primary_role === role ? 'border-blue-500/60 bg-blue-600/20 text-blue-300' : 'border-slate-700 text-slate-500 hover:border-slate-500 hover:text-slate-300'}`}>
                  {role === 'buyer' ? <ShoppingBag className="w-5 h-5" /> : <Store className="w-5 h-5" />}
                  <span className="text-xs font-medium capitalize">{role === 'buyer' ? 'Buy / Request' : 'Sell / Offer'}</span>
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-300 mb-1.5">Display name</label>
            <div className="relative">
              <User className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
              <input type="text" value={form.display_name} onChange={e => set('display_name', e.target.value)} placeholder="Your name" required minLength={2} maxLength={64}
                className="w-full pl-9 pr-3 py-2.5 rounded-lg text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-2 transition-all"
                style={{ background: 'rgba(15,32,68,0.8)', border: '1px solid rgba(59,130,246,0.2)' }} />
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-300 mb-1.5">Email</label>
            <div className="relative">
              <Mail className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
              <input type="email" value={form.email} onChange={e => set('email', e.target.value)} placeholder="you@example.com" required
                className="w-full pl-9 pr-3 py-2.5 rounded-lg text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-2 transition-all"
                style={{ background: 'rgba(15,32,68,0.8)', border: '1px solid rgba(59,130,246,0.2)' }} />
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-300 mb-1.5">Password</label>
            <div className="relative">
              <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
              <input type={showPw ? 'text' : 'password'} value={form.password} onChange={e => set('password', e.target.value)} placeholder="Min 8 characters" required minLength={8}
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
            {loading ? 'Creating account…' : 'Create account'}
          </button>
        </form>
        <p className="text-center text-sm text-slate-500 mt-6">
          Already have an account?{' '}
          <Link to="/login" className="font-medium hover:text-white transition-colors" style={{ color: '#00d4ff' }}>Sign in</Link>
        </p>
      </div>
    </div>
  );
}

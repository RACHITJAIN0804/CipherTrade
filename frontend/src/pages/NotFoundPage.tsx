import { Link } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';

export function NotFoundPage() {
  return (
    <div className="min-h-screen flex items-center justify-center px-4" style={{ background: '#050c1a' }}>
      <div className="text-center">
        <p className="text-7xl font-bold mb-4" style={{ color: 'rgba(37,99,235,0.3)' }}>404</p>
        <h1 className="text-2xl font-bold text-white mb-2">Page not found</h1>
        <p className="text-slate-400 mb-8">The page you're looking for doesn't exist.</p>
        <Link to="/" className="inline-flex items-center gap-2 px-5 py-2.5 rounded-lg font-medium text-white transition-all hover:opacity-90" style={{ background: '#2563eb' }}>
          <ArrowLeft className="w-4 h-4" />Go home
        </Link>
      </div>
    </div>
  );
}

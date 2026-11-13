import { Link } from 'react-router-dom';
import { Logo } from './Logo';
import { Button } from './ui';

export function Navbar() {
  return (
    <nav className="fixed top-0 left-0 right-0 z-40 border-b" style={{ background: 'rgba(5,12,26,0.85)', borderColor: 'rgba(59,130,246,0.15)', backdropFilter: 'blur(12px)' }}>
      <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
        <Link to="/"><Logo /></Link>
        <div className="flex items-center gap-3">
          <Link to="/login"><Button variant="ghost" size="sm">Sign in</Button></Link>
          <Link to="/register"><Button size="sm">Get started</Button></Link>
        </div>
      </div>
    </nav>
  );
}

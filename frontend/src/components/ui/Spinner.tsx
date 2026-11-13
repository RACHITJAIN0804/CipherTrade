import { Loader2 } from 'lucide-react';
export function Spinner({ size = 'md' }: { size?: 'sm' | 'md' | 'lg' }) {
  const sizes = { sm: 'w-4 h-4', md: 'w-6 h-6', lg: 'w-8 h-8' };
  return <Loader2 className={`${sizes[size]} animate-spin text-blue-400`} />;
}
export function LoadingScreen() {
  return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="flex flex-col items-center gap-3">
        <Spinner size="lg" />
        <p className="text-sm text-slate-500">Loading…</p>
      </div>
    </div>
  );
}

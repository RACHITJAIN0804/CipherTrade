interface BadgeProps { children: React.ReactNode; variant?: 'default' | 'success' | 'warning' | 'danger' | 'info' | 'electric'; className?: string; }

const variants = {
  default: 'bg-slate-800 text-slate-300 border-slate-700',
  success: 'bg-green-500/15 text-green-400 border-green-500/30',
  warning: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/30',
  danger: 'bg-red-500/15 text-red-400 border-red-500/30',
  info: 'bg-blue-500/15 text-blue-300 border-blue-500/30',
  electric: 'bg-cyan-400/10 text-cyan-400 border-cyan-400/30',
};

export function Badge({ children, variant = 'default', className = '' }: BadgeProps) {
  return <span className={`inline-flex items-center px-2.5 py-0.5 text-xs font-medium rounded-full border ${variants[variant]} ${className}`}>{children}</span>;
}

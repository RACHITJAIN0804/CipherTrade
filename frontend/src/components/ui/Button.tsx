import React from 'react';
import { Loader2 } from 'lucide-react';

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  size?: 'sm' | 'md' | 'lg';
  loading?: boolean;
  leftIcon?: React.ReactNode;
}

const variants = {
  primary: 'bg-blue-600 hover:bg-blue-500 text-white border border-blue-500/30',
  secondary: 'bg-transparent hover:bg-white/5 text-slate-200 border border-slate-700',
  ghost: 'bg-transparent hover:bg-white/5 text-slate-400 hover:text-slate-200 border border-transparent',
  danger: 'bg-red-600/20 hover:bg-red-600/30 text-red-400 border border-red-500/30',
};
const sizes = {
  sm: 'px-3 py-1.5 text-sm',
  md: 'px-4 py-2 text-sm',
  lg: 'px-6 py-3 text-base',
};

export function Button({ variant = 'primary', size = 'md', loading, leftIcon, children, className = '', disabled, ...props }: ButtonProps) {
  return (
    <button
      {...props}
      disabled={disabled || loading}
      className={`inline-flex items-center gap-2 font-medium rounded-lg transition-all duration-150 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed ${variants[variant]} ${sizes[size]} ${className}`}
    >
      {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : leftIcon}
      {children}
    </button>
  );
}

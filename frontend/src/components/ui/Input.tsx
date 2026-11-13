import React from 'react';

interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  hint?: string;
  leftElement?: React.ReactNode;
  rightElement?: React.ReactNode;
}

export function Input({ label, error, hint, leftElement, rightElement, className = '', id, ...props }: InputProps) {
  const inputId = id ?? label?.toLowerCase().replace(/\s+/g, '-');
  return (
    <div className="flex flex-col gap-1.5">
      {label && <label htmlFor={inputId} className="text-sm font-medium text-slate-300">{label}</label>}
      <div className="relative">
        {leftElement && <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500">{leftElement}</span>}
        <input
          id={inputId}
          {...props}
          className={`w-full bg-[rgba(15,32,68,0.6)] border rounded-lg px-3 py-2.5 text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-2 transition-all ${error ? 'border-red-500/60 focus:ring-red-500/30' : 'border-[rgba(59,130,246,0.2)] focus:ring-blue-500/30 focus:border-blue-500/50'} ${leftElement ? 'pl-9' : ''} ${rightElement ? 'pr-10' : ''} ${className}`}
        />
        {rightElement && <span className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500">{rightElement}</span>}
      </div>
      {error && <p className="text-xs text-red-400">{error}</p>}
      {hint && !error && <p className="text-xs text-slate-500">{hint}</p>}
    </div>
  );
}

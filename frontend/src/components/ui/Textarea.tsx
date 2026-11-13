import React from 'react';

interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  error?: string;
  hint?: string;
}

export function Textarea({ label, error, hint, className = '', id, ...props }: TextareaProps) {
  const inputId = id ?? label?.toLowerCase().replace(/\s+/g, '-');
  return (
    <div className="flex flex-col gap-1.5">
      {label && <label htmlFor={inputId} className="text-sm font-medium text-slate-300">{label}</label>}
      <textarea
        id={inputId}
        {...props}
        className={`w-full bg-[rgba(15,32,68,0.6)] border rounded-lg px-3 py-2.5 text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-2 transition-all resize-y min-h-[100px] ${error ? 'border-red-500/60 focus:ring-red-500/30' : 'border-[rgba(59,130,246,0.2)] focus:ring-blue-500/30 focus:border-blue-500/50'} ${className}`}
      />
      {error && <p className="text-xs text-red-400">{error}</p>}
      {hint && !error && <p className="text-xs text-slate-500">{hint}</p>}
    </div>
  );
}

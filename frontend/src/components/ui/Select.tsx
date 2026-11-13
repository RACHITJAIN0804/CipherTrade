import React from 'react';
import { ChevronDown } from 'lucide-react';

interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  error?: string;
  options: { value: string; label: string }[];
}

export function Select({ label, error, options, className = '', id, ...props }: SelectProps) {
  const inputId = id ?? label?.toLowerCase().replace(/\s+/g, '-');
  return (
    <div className="flex flex-col gap-1.5">
      {label && <label htmlFor={inputId} className="text-sm font-medium text-slate-300">{label}</label>}
      <div className="relative">
        <select
          id={inputId}
          {...props}
          className={`w-full appearance-none bg-[rgba(15,32,68,0.6)] border rounded-lg px-3 py-2.5 pr-9 text-slate-100 focus:outline-none focus:ring-2 transition-all ${error ? 'border-red-500/60 focus:ring-red-500/30' : 'border-[rgba(59,130,246,0.2)] focus:ring-blue-500/30 focus:border-blue-500/50'} ${className}`}
        >
          {options.map((o) => <option key={o.value} value={o.value} className="bg-[#0f2044]">{o.label}</option>)}
        </select>
        <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500 pointer-events-none" />
      </div>
      {error && <p className="text-xs text-red-400">{error}</p>}
    </div>
  );
}

import React, { useEffect } from 'react';
import { X } from 'lucide-react';

interface ModalProps { open: boolean; onClose: () => void; title?: string; children: React.ReactNode; size?: 'sm' | 'md' | 'lg'; }

export function Modal({ open, onClose, title, children, size = 'md' }: ModalProps) {
  useEffect(() => {
    if (open) document.body.style.overflow = 'hidden';
    else document.body.style.overflow = '';
    return () => { document.body.style.overflow = ''; };
  }, [open]);

  if (!open) return null;
  const widths = { sm: 'max-w-sm', md: 'max-w-lg', lg: 'max-w-2xl' };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <div
        className={`relative w-full ${widths[size]} rounded-xl border shadow-2xl`}
        style={{ background: '#0a1628', borderColor: 'rgba(59,130,246,0.25)' }}
        onClick={(e) => e.stopPropagation()}
      >
        {title && (
          <div className="flex items-center justify-between p-5 border-b" style={{ borderColor: 'rgba(59,130,246,0.15)' }}>
            <h2 className="text-lg font-semibold text-white">{title}</h2>
            <button onClick={onClose} className="text-slate-500 hover:text-slate-200 transition-colors p-1 rounded"><X className="w-5 h-5" /></button>
          </div>
        )}
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

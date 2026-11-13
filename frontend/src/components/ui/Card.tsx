import React from 'react';

interface CardProps { children: React.ReactNode; className?: string; style?: React.CSSProperties; onClick?: () => void; hover?: boolean; }

export function Card({ children, className = '', style, onClick, hover }: CardProps) {
  return (
    <div
      onClick={onClick}
      className={`rounded-xl border p-5 backdrop-blur-sm transition-all ${hover ? 'cursor-pointer hover:border-blue-500/40 hover:bg-[rgba(15,32,68,0.8)]' : ''} ${className}`}
      style={{ background: 'rgba(15, 32, 68, 0.6)', borderColor: 'rgba(59, 130, 246, 0.2)', ...style }}
    >
      {children}
    </div>
  );
}

export function CardHeader({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <div className={`mb-4 ${className}`}>{children}</div>;
}

export function CardTitle({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <h3 className={`text-lg font-semibold text-white ${className}`}>{children}</h3>;
}

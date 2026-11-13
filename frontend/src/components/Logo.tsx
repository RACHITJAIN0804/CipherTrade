interface LogoProps { size?: 'sm' | 'md' | 'lg'; className?: string; }

export function Logo({ size = 'md', className = '' }: LogoProps) {
  const sizes = { sm: { icon: 'w-7 h-7', text: 'text-lg' }, md: { icon: 'w-9 h-9', text: 'text-xl' }, lg: { icon: 'w-11 h-11', text: 'text-2xl' } };
  return (
    <div className={`flex items-center gap-2.5 ${className}`}>
      <div className={`${sizes[size].icon} rounded-lg flex items-center justify-center`} style={{ background: 'linear-gradient(135deg, #2563eb, #00d4ff)' }}>
        <svg viewBox="0 0 24 24" fill="none" className="w-5 h-5 text-white" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
        </svg>
      </div>
      <span className={`${sizes[size].text} font-bold tracking-tight text-white`}>
        Cipher<span style={{ color: '#00d4ff' }}>Trade</span>
      </span>
    </div>
  );
}

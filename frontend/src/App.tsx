import './index.css'

export default function App() {
  return (
    <main className="min-h-screen flex flex-col items-center justify-center gap-6 px-4">
      <div className="flex items-center gap-3">
        <div
          className="w-10 h-10 rounded-lg flex items-center justify-center"
          style={{ background: 'linear-gradient(135deg, #2563eb, #00d4ff)' }}
          aria-hidden="true"
        >
          <svg viewBox="0 0 24 24" fill="none" className="w-6 h-6 text-white" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
          </svg>
        </div>
        <span className="text-2xl font-bold tracking-tight text-white">
          Cipher<span style={{ color: '#00d4ff' }}>Trade</span>
        </span>
      </div>

      <div
        className="text-center max-w-md rounded-xl p-8 border"
        style={{
          background: 'rgba(15, 32, 68, 0.6)',
          borderColor: 'rgba(59, 130, 246, 0.2)',
          backdropFilter: 'blur(12px)',
        }}
      >
        <h1 className="text-3xl font-bold text-white mb-3">
          Blockchain P2P Marketplace
        </h1>
        <p className="text-slate-400 leading-relaxed">
          Secure peer-to-peer trading with end-to-end encrypted messaging and
          smart-contract escrow. Coming soon.
        </p>
        <div
          className="mt-6 inline-flex items-center gap-2 text-sm font-medium px-4 py-2 rounded-full"
          style={{ background: 'rgba(0, 212, 255, 0.1)', color: '#00d4ff', border: '1px solid rgba(0,212,255,0.3)' }}
        >
          <span className="w-2 h-2 rounded-full bg-current animate-pulse" />
          Phase 1 — Initialization complete
        </div>
      </div>
    </main>
  )
}

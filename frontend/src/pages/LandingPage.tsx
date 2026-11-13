import { Link } from 'react-router-dom';
import { Shield, Zap, Users, ArrowRight, Lock, ChevronRight, Globe } from 'lucide-react';
import { Navbar } from '../components/Navbar';

const features = [
  { icon: Shield, title: 'Smart-Contract Escrow', desc: 'Funds are held by an audited on-chain escrow contract — no intermediary holds your money.' },
  { icon: Lock, title: 'Private Messaging', desc: 'One-to-one negotiation channel between buyer and seller. Future E2EE layer in development.' },
  { icon: Zap, title: 'Instant Settlements', desc: 'On confirmation, funds release to the seller automatically — no waiting for bank clearance.' },
  { icon: Users, title: 'Verified Profiles', desc: 'Buyer and seller identities are separated by role to keep the marketplace clean and trustworthy.' },
  { icon: Globe, title: 'Open Marketplace', desc: 'Sellers browse buyer requirements and submit competitive offers. Buyers compare and choose.' },
  { icon: ChevronRight, title: 'Dispute Resolution', desc: 'Built-in reporting and block system. On-chain dispute mechanism for unresolved conflicts.' },
];

export function LandingPage() {
  return (
    <div style={{ background: '#050c1a', color: '#e2e8f0' }} className="min-h-screen">
      <Navbar />
      {/* Hero */}
      <section className="pt-32 pb-20 px-6 relative overflow-hidden">
        <div className="absolute inset-0 pointer-events-none">
          <div className="absolute top-20 left-1/4 w-96 h-96 rounded-full blur-3xl opacity-10" style={{ background: '#2563eb' }} />
          <div className="absolute top-40 right-1/4 w-64 h-64 rounded-full blur-3xl opacity-10" style={{ background: '#00d4ff' }} />
        </div>
        <div className="max-w-4xl mx-auto text-center relative">
          <div className="inline-flex items-center gap-2 text-xs font-medium px-3 py-1.5 rounded-full mb-6" style={{ background: 'rgba(0,212,255,0.08)', color: '#00d4ff', border: '1px solid rgba(0,212,255,0.25)' }}>
            <span className="w-1.5 h-1.5 rounded-full bg-current animate-pulse" />
            Blockchain-secured P2P marketplace
          </div>
          <h1 className="text-5xl md:text-6xl font-bold text-white mb-6 leading-tight">
            Trade with confidence.<br />
            <span style={{ color: '#00d4ff' }}>Settle on-chain.</span>
          </h1>
          <p className="text-lg text-slate-400 max-w-2xl mx-auto mb-10 leading-relaxed">
            CipherTrade connects buyers and sellers through a transparent, escrow-protected marketplace. Post requirements, receive offers, negotiate privately, and settle with smart contracts.
          </p>
          <div className="flex flex-col sm:flex-row gap-4 justify-center">
            <Link to="/register" className="inline-flex items-center gap-2 px-7 py-3.5 rounded-xl font-semibold text-white transition-all hover:opacity-90" style={{ background: 'linear-gradient(135deg, #2563eb, #00d4ff)' }}>
              Start as Buyer <ArrowRight className="w-4 h-4" />
            </Link>
            <Link to="/register" className="inline-flex items-center gap-2 px-7 py-3.5 rounded-xl font-semibold transition-all border hover:bg-white/5" style={{ color: '#00d4ff', borderColor: 'rgba(0,212,255,0.3)' }}>
              Join as Seller <ArrowRight className="w-4 h-4" />
            </Link>
          </div>
        </div>
      </section>

      {/* Stats */}
      <section className="py-10 border-y" style={{ borderColor: 'rgba(59,130,246,0.1)', background: 'rgba(10,22,40,0.5)' }}>
        <div className="max-w-5xl mx-auto px-6 grid grid-cols-3 gap-8 text-center">
          {[['Smart-Contract Escrow', 'On-chain settlements'], ['Zero Custody Risk', 'Non-custodial design'], ['P2P Native', 'Direct buyer↔seller']].map(([v, l]) => (
            <div key={v}><p className="text-2xl font-bold text-white mb-1">{v}</p><p className="text-sm text-slate-500">{l}</p></div>
          ))}
        </div>
      </section>

      {/* Features */}
      <section className="py-20 px-6">
        <div className="max-w-6xl mx-auto">
          <h2 className="text-3xl font-bold text-white text-center mb-3">Everything you need to trade safely</h2>
          <p className="text-slate-400 text-center mb-14 max-w-xl mx-auto">From discovery to settlement, every step is designed to protect both parties.</p>
          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-5">
            {features.map(({ icon: Icon, title, desc }) => (
              <div key={title} className="rounded-xl p-6 border transition-all hover:border-blue-500/40" style={{ background: 'rgba(15,32,68,0.5)', borderColor: 'rgba(59,130,246,0.15)' }}>
                <div className="w-10 h-10 rounded-lg flex items-center justify-center mb-4" style={{ background: 'rgba(37,99,235,0.15)' }}>
                  <Icon className="w-5 h-5" style={{ color: '#60a5fa' }} />
                </div>
                <h3 className="text-white font-semibold mb-2">{title}</h3>
                <p className="text-sm text-slate-400 leading-relaxed">{desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="py-20 px-6">
        <div className="max-w-3xl mx-auto rounded-2xl p-10 text-center border" style={{ background: 'rgba(15,32,68,0.6)', borderColor: 'rgba(59,130,246,0.2)' }}>
          <h2 className="text-3xl font-bold text-white mb-4">Ready to start trading?</h2>
          <p className="text-slate-400 mb-8">Create your account in seconds. No wallet connection required to browse.</p>
          <Link to="/register" className="inline-flex items-center gap-2 px-8 py-3.5 rounded-xl font-semibold text-white transition-all hover:opacity-90" style={{ background: 'linear-gradient(135deg, #2563eb, #00d4ff)' }}>
            Create free account <ArrowRight className="w-4 h-4" />
          </Link>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t py-8 px-6" style={{ borderColor: 'rgba(59,130,246,0.1)' }}>
        <div className="max-w-6xl mx-auto flex flex-col md:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <span className="font-bold text-slate-400">Cipher<span style={{ color: '#00d4ff' }}>Trade</span></span>
            <span>· Blockchain P2P Marketplace</span>
          </div>
          <div className="flex gap-6 text-sm text-slate-500">
            <Link to="/register" className="hover:text-slate-300 transition-colors">Get Started</Link>
            <Link to="/login" className="hover:text-slate-300 transition-colors">Sign In</Link>
          </div>
          <p className="text-xs text-slate-600">© 2026 CipherTrade. Smart-contract escrow is in development.</p>
        </div>
      </footer>
    </div>
  );
}

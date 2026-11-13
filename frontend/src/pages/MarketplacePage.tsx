import { useEffect, useState } from 'react';
import { Search, Filter, SlidersHorizontal } from 'lucide-react';
import { listRequirements } from '../services/requirementService';
import { RequirementCard } from '../components/RequirementCard';
import { Spinner } from '../components/ui/Spinner';
import { EmptyState } from '../components/ui/EmptyState';
import { useAuth } from '../context/AuthContext';
import type { Requirement } from '../types';

const CATEGORIES = ['All', 'Software', 'Design', 'Marketing', 'Consulting', 'Writing', 'Engineering', 'Other'];

export function MarketplacePage() {
  const { user } = useAuth();
  const [items, setItems] = useState<Requirement[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('');
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const pageSize = 12;
  const linkPrefix = user?.primary_role === 'seller' ? '/seller' : '/buyer';

  async function load() {
    setLoading(true);
    try {
      const data = await listRequirements({ q: search || undefined, category: category || undefined, page, page_size: pageSize });
      setItems(data.items);
      setTotal(data.total);
    } finally { setLoading(false); }
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [search, category, page]);

  function handleSearch(v: string) { setSearch(v); setPage(1); }
  function handleCategory(c: string) { setCategory(c === 'All' ? '' : c); setPage(1); }

  const totalPages = Math.ceil(total / pageSize);

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">Marketplace</h1>
        <p className="text-slate-400 text-sm mt-0.5">Browse buyer requirements and find opportunities.</p>
      </div>

      {/* Search & Filter */}
      <div className="flex flex-col sm:flex-row gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
          <input value={search} onChange={e => handleSearch(e.target.value)} placeholder="Search requirements, keywords…"
            className="w-full pl-9 pr-3 py-2.5 rounded-lg text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-2 transition-all"
            style={{ background: 'rgba(15,32,68,0.6)', border: '1px solid rgba(59,130,246,0.2)' }} />
        </div>
        <div className="relative">
          <SlidersHorizontal className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
          <select value={category || 'All'} onChange={e => handleCategory(e.target.value)}
            className="pl-9 pr-8 py-2.5 rounded-lg text-slate-100 appearance-none focus:outline-none focus:ring-2 transition-all"
            style={{ background: 'rgba(15,32,68,0.6)', border: '1px solid rgba(59,130,246,0.2)' }}>
            {CATEGORIES.map(c => <option key={c} value={c} className="bg-[#0f2044]">{c}</option>)}
          </select>
        </div>
      </div>

      {/* Results count */}
      {!loading && <p className="text-sm text-slate-500">{total} requirement{total !== 1 ? 's' : ''} found</p>}

      {/* Grid */}
      {loading ? (
        <div className="flex justify-center py-20"><Spinner size="lg" /></div>
      ) : items.length === 0 ? (
        <EmptyState icon={<Filter className="w-10 h-10" />} title="No requirements found" description={search || category ? 'Try adjusting your filters.' : 'No buyer requirements have been posted yet.'} />
      ) : (
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
          {items.map(r => <RequirementCard key={r.id} requirement={r} linkPrefix={linkPrefix} />)}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex justify-center gap-2 pt-4">
          <button disabled={page <= 1} onClick={() => setPage(p => p - 1)} className="px-4 py-2 rounded-lg text-sm font-medium transition-all disabled:opacity-30" style={{ background: 'rgba(15,32,68,0.6)', border: '1px solid rgba(59,130,246,0.2)', color: '#e2e8f0' }}>Previous</button>
          <span className="px-4 py-2 text-sm text-slate-400">Page {page} of {totalPages}</span>
          <button disabled={page >= totalPages} onClick={() => setPage(p => p + 1)} className="px-4 py-2 rounded-lg text-sm font-medium transition-all disabled:opacity-30" style={{ background: 'rgba(15,32,68,0.6)', border: '1px solid rgba(59,130,246,0.2)', color: '#e2e8f0' }}>Next</button>
        </div>
      )}
    </div>
  );
}

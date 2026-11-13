import { Link } from 'react-router-dom';
import { Card } from './ui/Card';
import { Badge } from './ui/Badge';
import { Calendar, DollarSign, Tag } from 'lucide-react';
import type { Requirement } from '../types';

function statusVariant(s: string) { return s === 'active' ? 'success' : s === 'closed' ? 'default' : 'warning'; }

interface Props { requirement: Requirement; linkPrefix?: string; }

export function RequirementCard({ requirement: r, linkPrefix = '' }: Props) {
  return (
    <Card hover className="flex flex-col gap-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <Link to={`${linkPrefix}/marketplace/${r.id}`} className="text-white font-semibold hover:text-cyan-400 transition-colors line-clamp-2">{r.title}</Link>
        </div>
        <Badge variant={statusVariant(r.status) as 'success' | 'default' | 'warning'}>{r.status}</Badge>
      </div>
      <p className="text-sm text-slate-400 line-clamp-2">{r.description}</p>
      <div className="flex flex-wrap items-center gap-3 text-xs text-slate-500">
        <span className="flex items-center gap-1"><Tag className="w-3 h-3" />{r.category}</span>
        {(r.budget_min != null || r.budget_max != null) && (
          <span className="flex items-center gap-1">
            <DollarSign className="w-3 h-3" />
            {r.budget_min != null && r.budget_max != null ? `${r.budget_min}–${r.budget_max} ETH` : r.budget_min != null ? `From ${r.budget_min} ETH` : `Up to ${r.budget_max} ETH`}
          </span>
        )}
        <span className="flex items-center gap-1"><Calendar className="w-3 h-3" />{new Date(r.created_at).toLocaleDateString()}</span>
      </div>
    </Card>
  );
}

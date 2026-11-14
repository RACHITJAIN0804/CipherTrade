import { Card } from './ui/Card';
import { Badge } from './ui/Badge';
import { Button } from './ui/Button';
import { Clock, Coins, ShieldCheck } from 'lucide-react';
import type { Offer } from '../types';

type OfferBadgeVariant = 'default' | 'success' | 'warning' | 'danger' | 'info' | 'electric';

function offerStatusVariant(s: string): OfferBadgeVariant {
  const map: Record<string, OfferBadgeVariant> = {
    pending: 'warning',
    accepted: 'success',
    rejected: 'danger',
    withdrawn: 'default',
  };
  return map[s] ?? 'default';
}

interface Props {
  offer: Offer;
  viewerRole: 'buyer' | 'seller';
  onAccept?: (id: string) => void;
  onReject?: (id: string) => void;
  onWithdraw?: (id: string) => void;
  onFund?: (offer: Offer) => void;
  loading?: boolean;
}

export function OfferCard({
  offer: o,
  viewerRole,
  onAccept,
  onReject,
  onWithdraw,
  onFund,
  loading,
}: Props) {
  const deadline = new Date(o.delivery_deadline * 1000).toLocaleDateString();
  return (
    <Card className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-2 text-white font-semibold">
          <Coins className="w-4 h-4 text-cyan-400" />
          {o.price_eth} ETH
        </span>
        <Badge variant={offerStatusVariant(o.status)}>{o.status}</Badge>
      </div>
      <p className="text-sm text-slate-400 line-clamp-3">{o.terms}</p>
      <div className="flex items-center gap-2 text-xs text-slate-500">
        <Clock className="w-3 h-3" />
        Deadline: {deadline}
      </div>

      {o.status === 'pending' && (
        <div className="flex gap-2 pt-1">
          {viewerRole === 'buyer' && onAccept && (
            <Button size="sm" onClick={() => onAccept(o.id)} loading={loading}>
              Accept
            </Button>
          )}
          {viewerRole === 'buyer' && onReject && (
            <Button size="sm" variant="danger" onClick={() => onReject(o.id)} loading={loading}>
              Reject
            </Button>
          )}
          {viewerRole === 'seller' && onWithdraw && (
            <Button size="sm" variant="secondary" onClick={() => onWithdraw(o.id)} loading={loading}>
              Withdraw
            </Button>
          )}
        </div>
      )}

      {o.status === 'accepted' && viewerRole === 'buyer' && onFund && (
        <div className="flex gap-2 pt-1">
          <Button
            size="sm"
            variant="primary"
            onClick={() => onFund(o)}
            leftIcon={<ShieldCheck className="w-4 h-4" />}
          >
            Fund Escrow
          </Button>
        </div>
      )}
    </Card>
  );
}

import { useState } from 'react';
import { Search, Shield, Clock, Coins, User, Hash, AlertCircle } from 'lucide-react';
import { useWallet } from '../../context/WalletContext';
import {
  getOnChainDeal,
  getNextDealId,
  type OnChainDeal,
  formatContractError,
} from '../../services/contractService';
import { Card } from '../ui/Card';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { Badge } from '../ui/Badge';
import { Spinner } from '../ui/Spinner';

export function OnChainDealCard() {
  const { provider, formatAddress } = useWallet();
  const [dealIdInput, setDealIdInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [deal, setDeal] = useState<OnChainDeal | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nextId, setNextId] = useState<bigint | null>(null);

  const handleLookup = async () => {
    if (!provider) {
      setError('Please connect your wallet or ensure browser provider is available.');
      return;
    }
    const idNum = parseInt(dealIdInput.trim(), 10);
    if (isNaN(idNum) || idNum < 0) {
      setError('Please enter a valid non-negative deal ID.');
      return;
    }

    try {
      setLoading(true);
      setError(null);
      const data = await getOnChainDeal(idNum, provider);
      setDeal(data);
    } catch (err: unknown) {
      setError(formatContractError(err));
      setDeal(null);
    } finally {
      setLoading(false);
    }
  };

  const handleFetchNextId = async () => {
    if (!provider) return;
    try {
      const id = await getNextDealId(provider);
      setNextId(id);
    } catch (err: unknown) {
      setError(formatContractError(err));
    }
  };

  const getStateBadgeVariant = (stateName: string) => {
    switch (stateName) {
      case 'FUNDED':
        return 'electric';
      case 'CONFIRMED':
        return 'info';
      case 'RELEASED':
        return 'success';
      case 'REFUNDED':
        return 'warning';
      case 'CANCELLED':
        return 'default';
      case 'DISPUTED':
        return 'danger';
      case 'RESOLVED':
        return 'success';
      default:
        return 'default';
    }
  };

  return (
    <Card className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Shield className="w-5 h-5 text-cyan-400" />
          <h3 className="font-semibold text-white">On-Chain Escrow Explorer</h3>
        </div>
        <Button variant="ghost" size="sm" onClick={handleFetchNextId}>
          {nextId !== null ? `Next Deal ID: #${nextId.toString()}` : 'Check Next ID'}
        </Button>
      </div>

      <div className="flex gap-2">
        <Input
          placeholder="Lookup Deal ID (e.g. 0)"
          value={dealIdInput}
          onChange={(e) => setDealIdInput(e.target.value)}
          type="number"
          min="0"
          className="flex-1"
        />
        <Button
          onClick={handleLookup}
          disabled={loading || !dealIdInput.trim()}
          leftIcon={<Search className="w-4 h-4" />}
        >
          Query
        </Button>
      </div>

      {loading && (
        <div className="flex justify-center py-6">
          <Spinner size="md" />
        </div>
      )}

      {error && (
        <div className="p-3 rounded-lg bg-red-950/40 border border-red-500/20 text-red-300 text-xs flex items-center gap-2">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {deal && !loading && (
        <div className="p-4 rounded-lg bg-slate-900/60 border border-slate-800 space-y-3 text-sm">
          <div className="flex items-center justify-between pb-2 border-b border-slate-800">
            <span className="font-mono font-bold text-white">Deal #{deal.dealId}</span>
            <Badge variant={getStateBadgeVariant(deal.stateName)}>{deal.stateName}</Badge>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
            <div className="space-y-1">
              <span className="text-slate-400 flex items-center gap-1">
                <Coins className="w-3.5 h-3.5 text-cyan-400" /> Escrow Amount:
              </span>
              <p className="font-semibold text-white">{deal.amountEth} ETH</p>
            </div>

            <div className="space-y-1">
              <span className="text-slate-400 flex items-center gap-1">
                <Clock className="w-3.5 h-3.5 text-blue-400" /> Delivery Deadline:
              </span>
              <p className="text-slate-200">{deal.deadlineDate.toLocaleString()}</p>
            </div>

            <div className="space-y-1">
              <span className="text-slate-400 flex items-center gap-1">
                <User className="w-3.5 h-3.5 text-slate-400" /> Buyer:
              </span>
              <p className="font-mono text-slate-200">{formatAddress(deal.buyer)}</p>
            </div>

            <div className="space-y-1">
              <span className="text-slate-400 flex items-center gap-1">
                <User className="w-3.5 h-3.5 text-slate-400" /> Seller:
              </span>
              <p className="font-mono text-slate-200">{formatAddress(deal.seller)}</p>
            </div>
          </div>

          <div className="pt-2 border-t border-slate-800 text-xs space-y-1">
            <span className="text-slate-400 flex items-center gap-1">
              <Hash className="w-3.5 h-3.5 text-slate-400" /> Agreement Hash:
            </span>
            <p className="font-mono text-slate-300 truncate bg-slate-950/60 p-1.5 rounded border border-slate-800/80">
              {deal.agreementHash}
            </p>
          </div>
        </div>
      )}
    </Card>
  );
}


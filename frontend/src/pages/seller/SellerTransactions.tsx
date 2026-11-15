import { useState, useEffect, useCallback } from 'react';
import {
  Shield,
  CheckCircle2,
  Lock,
  RefreshCw,
  AlertCircle,
  Clock,
  Eye,
  Filter,
  Info,
  Database,
  ArrowDownLeft,
} from 'lucide-react';
import { OnChainDealCard } from '../../components/escrow/OnChainDealCard';
import { TransactionDetailModal } from '../../components/escrow/TransactionDetailModal';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { Badge } from '../../components/ui/Badge';
import { Spinner } from '../../components/ui/Spinner';
import { EmptyState } from '../../components/ui/EmptyState';
import { ESCROW_CONTRACT_ADDRESS, ESCROW_CHAIN_ID } from '../../config/contracts';
import { listSellerDeals } from '../../services/escrowService';
import { useWallet } from '../../context/WalletContext';
import type { EscrowTransaction, EscrowState } from '../../types';

const STATUS_FILTERS: { label: string; value: EscrowState | 'ALL' }[] = [
  { label: 'All Transactions', value: 'ALL' },
  { label: 'Funded (Active)', value: 'FUNDED' },
  { label: 'Confirmed (Ready to Withdraw)', value: 'CONFIRMED' },
  { label: 'Released (Paid)', value: 'RELEASED' },
  { label: 'Refunded', value: 'REFUNDED' },
  { label: 'Cancelled', value: 'CANCELLED' },
  { label: 'Disputed', value: 'DISPUTED' },
];

export function SellerTransactions() {
  const { account, isConnected, isCorrectNetwork, formatAddress } = useWallet();

  // Modals state
  const [selectedDeal, setSelectedDeal] = useState<EscrowTransaction | null>(null);
  const [detailModalOpen, setDetailModalOpen] = useState(false);

  // Transaction list state
  const [deals, setDeals] = useState<EscrowTransaction[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize] = useState(10);
  const [totalDeals, setTotalDeals] = useState(0);
  const [selectedFilter, setSelectedFilter] = useState<EscrowState | 'ALL'>('ALL');

  // Advanced Tools Toggle
  const [showExplorer, setShowExplorer] = useState(false);

  const fetchDeals = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const params = {
        page: currentPage,
        page_size: pageSize,
        deal_state: selectedFilter === 'ALL' ? undefined : selectedFilter,
      };
      const response = await listSellerDeals(params);
      setDeals(response.items);
      setTotalDeals(response.total);
    } catch (err: unknown) {
      setError(
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
          'Failed to load seller escrow transactions.'
      );
    } finally {
      setLoading(false);
    }
  }, [currentPage, pageSize, selectedFilter]);

  useEffect(() => {
    fetchDeals();
  }, [fetchDeals]);

  const handleDealUpdated = (updated: EscrowTransaction) => {
    setDeals((prev) => prev.map((d) => (d.id === updated.id ? updated : d)));
    if (selectedDeal?.id === updated.id) {
      setSelectedDeal(updated);
    }
  };

  const getStateBadge = (state: EscrowState | null) => {
    switch (state) {
      case 'FUNDED':
        return <Badge variant="electric">FUNDED</Badge>;
      case 'CONFIRMED':
        return <Badge variant="info">CONFIRMED (READY)</Badge>;
      case 'RELEASED':
        return <Badge variant="success">RELEASED</Badge>;
      case 'REFUNDED':
        return <Badge variant="warning">REFUNDED</Badge>;
      case 'CANCELLED':
        return <Badge variant="default">CANCELLED</Badge>;
      case 'DISPUTED':
        return <Badge variant="danger">DISPUTED</Badge>;
      case 'RESOLVED':
        return <Badge variant="success">RESOLVED</Badge>;
      default:
        return <Badge variant="warning">PENDING</Badge>;
    }
  };

  const totalPages = Math.ceil(totalDeals / pageSize) || 1;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight">Seller Escrow Transactions</h1>
          <p className="text-slate-400 text-sm mt-1">
            Monitor incoming escrow payments, manage delivery fulfillment, and withdraw earned ETH funds.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => fetchDeals()}
            disabled={loading}
            leftIcon={<RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />}
          >
            Refresh
          </Button>
        </div>
      </div>

      {/* Contract & Network Status Summary Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-lg bg-blue-500/10 border border-blue-500/20 flex items-center justify-center text-blue-400 shrink-0">
            <Shield className="w-5 h-5" />
          </div>
          <div className="truncate">
            <span className="text-xs text-slate-400 block">Smart Contract</span>
            <span className="font-mono text-xs text-cyan-400 font-semibold truncate block" title={ESCROW_CONTRACT_ADDRESS}>
              {formatAddress(ESCROW_CONTRACT_ADDRESS)}
            </span>
          </div>
        </Card>

        <Card className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-lg bg-cyan-500/10 border border-cyan-500/20 flex items-center justify-center text-cyan-400 shrink-0">
            <Lock className="w-5 h-5" />
          </div>
          <div>
            <span className="text-xs text-slate-400 block">Blockchain Network</span>
            <span className="text-xs text-white font-semibold block">
              Anvil Local (Chain {ESCROW_CHAIN_ID})
            </span>
          </div>
        </Card>

        <Card className="flex items-center gap-3">
          <div
            className={`w-10 h-10 rounded-lg flex items-center justify-center shrink-0 ${
              isConnected && isCorrectNetwork
                ? 'bg-green-500/10 border border-green-500/20 text-green-400'
                : 'bg-amber-500/10 border border-amber-500/20 text-amber-400'
            }`}
          >
            <CheckCircle2 className="w-5 h-5" />
          </div>
          <div className="truncate">
            <span className="text-xs text-slate-400 block">Connected Seller</span>
            <span className="font-mono text-xs text-slate-200 font-semibold truncate block">
              {isConnected ? formatAddress(account) : 'Not Connected'}
            </span>
          </div>
        </Card>
      </div>

      {/* Pull Payment Explainer Banner */}
      <div className="p-4 rounded-xl bg-cyan-950/20 border border-cyan-500/20 flex items-start gap-3">
        <Info className="w-5 h-5 text-cyan-400 shrink-0 mt-0.5" />
        <div className="text-sm space-y-1 text-slate-300">
          <p className="font-medium text-white">Pull-Payment Security Architecture</p>
          <p className="text-xs text-slate-400 leading-relaxed">
            The CipherTrade escrow contract implements a secure pull-payment pattern. When a buyer confirms delivery, the state updates to <code className="text-cyan-400 font-mono">CONFIRMED</code>. As the seller, you must click <strong>Withdraw Payment</strong> in the transaction details to transfer the ETH directly into your wallet.
          </p>
        </div>
      </div>

      {/* Status Filter Tabs */}
      <div className="flex items-center gap-2 overflow-x-auto pb-1 border-b border-slate-800">
        <Filter className="w-4 h-4 text-slate-400 shrink-0 mr-1" />
        {STATUS_FILTERS.map((tab) => (
          <button
            key={tab.value}
            onClick={() => {
              setSelectedFilter(tab.value);
              setCurrentPage(1);
            }}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors whitespace-nowrap ${
              selectedFilter === tab.value
                ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/30'
                : 'text-slate-400 hover:text-white hover:bg-slate-800/60'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Error Banner */}
      {error && (
        <div className="p-4 rounded-xl bg-red-950/40 border border-red-500/20 text-red-300 text-sm flex items-center gap-3">
          <AlertCircle className="w-5 h-5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Transactions List */}
      <Card className="p-0 overflow-hidden">
        {loading ? (
          <div className="flex flex-col items-center justify-center py-16 gap-3">
            <Spinner size="lg" />
            <p className="text-xs text-slate-400">Loading seller escrow transactions...</p>
          </div>
        ) : deals.length === 0 ? (
          <div className="py-12">
            <EmptyState
              title="No Escrow Transactions Found"
              description={
                selectedFilter === 'ALL'
                  ? 'No buyers have funded escrow deals for your offers yet. Once a buyer funds an accepted offer, it will appear here.'
                  : `No escrow transactions currently in "${selectedFilter}" status.`
              }
              action={
                selectedFilter !== 'ALL' ? (
                  <Button
                    variant="ghost"
                    onClick={() => setSelectedFilter('ALL')}
                    size="sm"
                  >
                    Clear Filter
                  </Button>
                ) : undefined
              }
            />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-900/80 text-xs text-slate-400 uppercase tracking-wider border-b border-slate-800">
                <tr>
                  <th className="py-3 px-4">Deal / Item</th>
                  <th className="py-3 px-4">Buyer</th>
                  <th className="py-3 px-4">Amount</th>
                  <th className="py-3 px-4">Status</th>
                  <th className="py-3 px-4">Deadline</th>
                  <th className="py-3 px-4 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 text-xs">
                {deals.map((deal) => {
                  const isPassed = Math.floor(Date.now() / 1000) > deal.deadline_ts;
                  const deadlineDate = new Date(deal.deadline_ts * 1000);
                  const isReadyToWithdraw = deal.on_chain_state === 'CONFIRMED';

                  return (
                    <tr
                      key={deal.id}
                      className="hover:bg-slate-800/30 transition-colors group"
                    >
                      <td className="py-3.5 px-4">
                        <div className="space-y-1">
                          <p className="font-semibold text-white group-hover:text-cyan-300 transition-colors">
                            {deal.requirement_title || 'Escrow Item'}
                          </p>
                          <div className="flex items-center gap-2 text-[11px] text-slate-400 font-mono">
                            <span>ID: {deal.id.slice(0, 8)}...</span>
                            {deal.chain_deal_id !== null && (
                              <span className="text-cyan-400 font-semibold">
                                #{deal.chain_deal_id}
                              </span>
                            )}
                          </div>
                        </div>
                      </td>
                      <td className="py-3.5 px-4">
                        <div className="space-y-0.5">
                          <p className="font-medium text-slate-200">
                            {deal.buyer_display_name || 'Buyer'}
                          </p>
                          <p className="font-mono text-[11px] text-slate-400">
                            {deal.buyer_wallet_address
                              ? formatAddress(deal.buyer_wallet_address)
                              : 'No wallet registered'}
                          </p>
                        </div>
                      </td>
                      <td className="py-3.5 px-4 font-semibold text-white">
                        {deal.amount_eth} ETH
                      </td>
                      <td className="py-3.5 px-4">
                        {getStateBadge(deal.on_chain_state)}
                      </td>
                      <td className="py-3.5 px-4">
                        <div className="space-y-0.5">
                          <div className="flex items-center gap-1 text-slate-300">
                            <Clock className="w-3 h-3 text-slate-400" />
                            <span>{deadlineDate.toLocaleDateString()}</span>
                          </div>
                          <span
                            className={`text-[10px] block ${
                              isPassed ? 'text-amber-400 font-medium' : 'text-slate-400'
                            }`}
                          >
                            {isPassed ? 'Expired' : 'Active'}
                          </span>
                        </div>
                      </td>
                      <td className="py-3.5 px-4 text-right">
                        <div className="flex items-center justify-end gap-2">
                          {isReadyToWithdraw && (
                            <Button
                              size="sm"
                              variant="primary"
                              onClick={() => {
                                setSelectedDeal(deal);
                                setDetailModalOpen(true);
                              }}
                              leftIcon={<ArrowDownLeft className="w-3.5 h-3.5" />}
                            >
                              Withdraw
                            </Button>
                          )}
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => {
                              setSelectedDeal(deal);
                              setDetailModalOpen(true);
                            }}
                            leftIcon={<Eye className="w-3.5 h-3.5" />}
                          >
                            View Details
                          </Button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Pagination footer */}
        {deals.length > 0 && totalPages > 1 && (
          <div className="p-4 border-t border-slate-800 flex items-center justify-between text-xs text-slate-400">
            <span>
              Showing Page {currentPage} of {totalPages} ({totalDeals} total)
            </span>
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                size="sm"
                disabled={currentPage <= 1}
                onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
              >
                Previous
              </Button>
              <Button
                variant="ghost"
                size="sm"
                disabled={currentPage >= totalPages}
                onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
              >
                Next
              </Button>
            </div>
          </div>
        )}
      </Card>

      {/* Explorer Toggle */}
      <div className="pt-2">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setShowExplorer((prev) => !prev)}
          leftIcon={<Database className="w-4 h-4" />}
        >
          {showExplorer ? 'Hide On-Chain Explorer' : 'Explore On-Chain Escrow Data'}
        </Button>
      </div>

      {showExplorer && (
        <div className="pt-2">
          <OnChainDealCard />
        </div>
      )}

      {/* Transaction Detail & Actions Modal */}
      <TransactionDetailModal
        open={detailModalOpen}
        onClose={() => setDetailModalOpen(false)}
        deal={selectedDeal}
        viewerRole="seller"
        onDealUpdated={handleDealUpdated}
      />
    </div>
  );
}

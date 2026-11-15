import { useState, useEffect, useCallback } from 'react';
import {
  Shield,
  ShieldCheck,
  CheckCircle2,
  Lock,
  Search,
  RefreshCw,
  AlertCircle,
  Database,
  Clock,
  Eye,
  Filter,
} from 'lucide-react';
import { OnChainDealCard } from '../../components/escrow/OnChainDealCard';
import { FundEscrowModal } from '../../components/escrow/FundEscrowModal';
import { TransactionDetailModal } from '../../components/escrow/TransactionDetailModal';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { Input } from '../../components/ui/Input';
import { Badge } from '../../components/ui/Badge';
import { Spinner } from '../../components/ui/Spinner';
import { EmptyState } from '../../components/ui/EmptyState';
import { ESCROW_CONTRACT_ADDRESS, ESCROW_CHAIN_ID } from '../../config/contracts';
import { listBuyerDeals, getEscrowDeal, verifyFunding } from '../../services/escrowService';
import { useWallet } from '../../context/WalletContext';
import type { EscrowTransaction, EscrowState } from '../../types';

const STATUS_FILTERS: { label: string; value: EscrowState | 'ALL' }[] = [
  { label: 'All Transactions', value: 'ALL' },
  { label: 'Funded', value: 'FUNDED' },
  { label: 'Confirmed', value: 'CONFIRMED' },
  { label: 'Released', value: 'RELEASED' },
  { label: 'Refunded', value: 'REFUNDED' },
  { label: 'Cancelled', value: 'CANCELLED' },
  { label: 'Disputed', value: 'DISPUTED' },
];

export function BuyerTransactions() {
  const { account, isConnected, isCorrectNetwork, formatAddress } = useWallet();

  // Modals state
  const [fundModalOpen, setFundModalOpen] = useState(false);
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

  // Manual Inspector & Verification state
  const [showInspector, setShowInspector] = useState(false);
  const [dealIdInput, setDealIdInput] = useState('');
  const [txHashInput, setTxHashInput] = useState('');
  const [inspectorLoading, setInspectorLoading] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [inspectedDeal, setInspectedDeal] = useState<EscrowTransaction | null>(null);
  const [inspectorError, setInspectorError] = useState<string | null>(null);
  const [verifySuccess, setVerifySuccess] = useState<string | null>(null);

  const fetchDeals = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const params = {
        page: currentPage,
        page_size: pageSize,
        deal_state: selectedFilter === 'ALL' ? undefined : selectedFilter,
      };
      const response = await listBuyerDeals(params);
      setDeals(response.items);
      setTotalDeals(response.total);
    } catch (err: unknown) {
      setError(
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
          'Failed to load buyer escrow transactions.'
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

  const handleLookupDeal = async () => {
    if (!dealIdInput.trim()) return;
    try {
      setInspectorLoading(true);
      setInspectorError(null);
      setVerifySuccess(null);
      const deal = await getEscrowDeal(dealIdInput.trim());
      setInspectedDeal(deal);
      if (deal.fund_tx_hash) {
        setTxHashInput(deal.fund_tx_hash);
      }
    } catch (err: unknown) {
      setInspectorError(
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
          'Failed to load deal record from backend.'
      );
      setInspectedDeal(null);
    } finally {
      setInspectorLoading(false);
    }
  };

  const handleVerifyExistingTx = async () => {
    if (!inspectedDeal || !txHashInput.trim()) return;
    try {
      setVerifying(true);
      setInspectorError(null);
      setVerifySuccess(null);
      await verifyFunding(inspectedDeal.id, txHashInput.trim());
      const updated = await getEscrowDeal(inspectedDeal.id);
      setInspectedDeal(updated);
      setVerifySuccess('Transaction successfully verified! Deal state updated to FUNDED.');
      fetchDeals();
    } catch (err: unknown) {
      setInspectorError(
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
          'Backend verification failed.'
      );
    } finally {
      setVerifying(false);
    }
  };

  const getStateBadge = (state: EscrowState | null) => {
    switch (state) {
      case 'FUNDED':
        return <Badge variant="electric">FUNDED</Badge>;
      case 'CONFIRMED':
        return <Badge variant="info">CONFIRMED</Badge>;
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
          <h1 className="text-2xl font-bold text-white tracking-tight">Buyer Escrow Transactions</h1>
          <p className="text-slate-400 text-sm mt-1">
            Track and manage all smart-contract escrow payments you have funded on CipherTrade.
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
          <Button
            onClick={() => setFundModalOpen(true)}
            leftIcon={<ShieldCheck className="w-4 h-4" />}
          >
            Fund Escrow Deal
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
            <span className="text-xs text-slate-400 block">Connected Buyer</span>
            <span className="font-mono text-xs text-slate-200 font-semibold truncate block">
              {isConnected ? formatAddress(account) : 'Not Connected'}
            </span>
          </div>
        </Card>
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
            <p className="text-xs text-slate-400">Loading escrow transactions...</p>
          </div>
        ) : deals.length === 0 ? (
          <div className="py-12">
            <EmptyState
              title="No Escrow Deals Found"
              description={
                selectedFilter === 'ALL'
                  ? 'You have not funded any escrow deals yet. Fund an accepted offer to initiate non-custodial smart contract protection.'
                  : `No escrow transactions currently in "${selectedFilter}" status.`
              }
              action={
                selectedFilter === 'ALL' ? (
                  <Button
                    onClick={() => setFundModalOpen(true)}
                    leftIcon={<ShieldCheck className="w-4 h-4" />}
                    size="sm"
                  >
                    Fund New Escrow Deal
                  </Button>
                ) : (
                  <Button
                    variant="ghost"
                    onClick={() => setSelectedFilter('ALL')}
                    size="sm"
                  >
                    Clear Filter
                  </Button>
                )
              }
            />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-900/80 text-xs text-slate-400 uppercase tracking-wider border-b border-slate-800">
                <tr>
                  <th className="py-3 px-4">Deal / Item</th>
                  <th className="py-3 px-4">Seller</th>
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
                            {deal.seller_display_name || 'Seller'}
                          </p>
                          <p className="font-mono text-[11px] text-slate-400">
                            {deal.seller_wallet_address
                              ? formatAddress(deal.seller_wallet_address)
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

      {/* Advanced Verification & Contract Explorer Toggle */}
      <div className="pt-2">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setShowInspector((prev) => !prev)}
          leftIcon={<Database className="w-4 h-4" />}
        >
          {showInspector ? 'Hide Advanced Escrow Tools' : 'Show Advanced Escrow Tools'}
        </Button>
      </div>

      {showInspector && (
        <div className="space-y-4 pt-2">
          {/* Backend Deal Verification Inspector */}
          <Card className="space-y-4">
            <div className="flex items-center gap-2">
              <Database className="w-5 h-5 text-blue-400" />
              <h3 className="font-semibold text-white">Backend Escrow Verification Inspector</h3>
            </div>
            <p className="text-xs text-slate-400">
              Inspect any backend escrow deal record or manually submit/retry an on-chain transaction hash for backend verification.
            </p>

            <div className="flex gap-2">
              <Input
                placeholder="Backend Deal UUID (e.g. 12345678-1234-1234-1234-123456789abc)"
                value={dealIdInput}
                onChange={(e) => setDealIdInput(e.target.value)}
                className="flex-1"
              />
              <Button
                onClick={handleLookupDeal}
                disabled={inspectorLoading || !dealIdInput.trim()}
                leftIcon={<Search className="w-4 h-4" />}
              >
                Lookup
              </Button>
            </div>

            {inspectorLoading && (
              <div className="flex justify-center py-4">
                <Spinner size="md" />
              </div>
            )}

            {inspectorError && (
              <div className="p-3 rounded-lg bg-red-950/40 border border-red-500/20 text-red-300 text-xs flex items-center gap-2">
                <AlertCircle className="w-4 h-4 shrink-0" />
                <span>{inspectorError}</span>
              </div>
            )}

            {verifySuccess && (
              <div className="p-3 rounded-lg bg-green-950/40 border border-green-500/20 text-green-300 text-xs flex items-center gap-2">
                <CheckCircle2 className="w-4 h-4 shrink-0" />
                <span>{verifySuccess}</span>
              </div>
            )}

            {inspectedDeal && !inspectorLoading && (
              <div className="p-4 rounded-lg bg-slate-900/60 border border-slate-800 space-y-3 text-sm">
                <div className="flex items-center justify-between pb-2 border-b border-slate-800">
                  <span className="font-mono text-xs text-slate-300">Deal: {inspectedDeal.id}</span>
                  <Badge variant={inspectedDeal.on_chain_state === 'FUNDED' ? 'success' : 'warning'}>
                    {inspectedDeal.on_chain_state || 'PENDING VERIFICATION'}
                  </Badge>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
                  <div>
                    <span className="text-slate-400">Amount:</span>
                    <p className="font-semibold text-white">{inspectedDeal.amount_eth} ETH</p>
                  </div>
                  <div>
                    <span className="text-slate-400">Deadline:</span>
                    <p className="text-slate-200">
                      {new Date(inspectedDeal.deadline_ts * 1000).toLocaleString()}
                    </p>
                  </div>
                  <div>
                    <span className="text-slate-400">On-Chain Deal ID:</span>
                    <p className="font-mono text-cyan-400">
                      {inspectedDeal.chain_deal_id !== null ? `#${inspectedDeal.chain_deal_id}` : 'Not linked yet'}
                    </p>
                  </div>
                  <div>
                    <span className="text-slate-400">Verified Tx Hash:</span>
                    <p className="font-mono text-slate-300 truncate">
                      {inspectedDeal.fund_tx_hash || 'Not verified yet'}
                    </p>
                  </div>
                </div>

                {inspectedDeal.on_chain_state !== 'FUNDED' && (
                  <div className="pt-3 border-t border-slate-800 space-y-2">
                    <label className="block text-xs font-medium text-slate-300">
                      Transaction Hash to Verify (0x...)
                    </label>
                    <div className="flex gap-2">
                      <Input
                        placeholder="0x..."
                        value={txHashInput}
                        onChange={(e) => setTxHashInput(e.target.value.trim())}
                        className="flex-1 font-mono text-xs"
                      />
                      <Button
                        size="sm"
                        onClick={handleVerifyExistingTx}
                        disabled={verifying || !txHashInput.trim()}
                        leftIcon={<RefreshCw className="w-4 h-4" />}
                      >
                        Verify On-Chain
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </Card>

          {/* On-Chain Escrow Explorer */}
          <OnChainDealCard />
        </div>
      )}

      {/* Fund Escrow Modal */}
      <FundEscrowModal
        open={fundModalOpen}
        onClose={() => setFundModalOpen(false)}
        onSuccess={() => {
          fetchDeals();
        }}
      />

      {/* Transaction Detail & Actions Modal */}
      <TransactionDetailModal
        open={detailModalOpen}
        onClose={() => setDetailModalOpen(false)}
        deal={selectedDeal}
        viewerRole="buyer"
        onDealUpdated={handleDealUpdated}
      />
    </div>
  );
}

import { useState } from 'react';
import { Shield, ShieldCheck, Info, CheckCircle2, Lock, Search, RefreshCw, AlertCircle, Database } from 'lucide-react';
import { OnChainDealCard } from '../../components/escrow/OnChainDealCard';
import { FundEscrowModal } from '../../components/escrow/FundEscrowModal';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { Input } from '../../components/ui/Input';
import { Badge } from '../../components/ui/Badge';
import { Spinner } from '../../components/ui/Spinner';
import { ESCROW_CONTRACT_ADDRESS, ESCROW_CHAIN_ID } from '../../config/contracts';
import { getEscrowDeal, verifyFunding } from '../../services/escrowService';
import { useWallet } from '../../context/WalletContext';
import type { EscrowDeal } from '../../types';

export function BuyerTransactions() {
  const { account, isConnected, isCorrectNetwork, formatAddress } = useWallet();
  const [modalOpen, setModalOpen] = useState(false);

  // Backend Deal Lookup & Verification
  const [dealIdInput, setDealIdInput] = useState('');
  const [txHashInput, setTxHashInput] = useState('');
  const [dealLoading, setDealLoading] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [loadedDeal, setLoadedDeal] = useState<EscrowDeal | null>(null);
  const [dealError, setDealError] = useState<string | null>(null);
  const [verifySuccess, setVerifySuccess] = useState<string | null>(null);

  const handleLookupDeal = async () => {
    if (!dealIdInput.trim()) return;
    try {
      setDealLoading(true);
      setDealError(null);
      setVerifySuccess(null);
      const deal = await getEscrowDeal(dealIdInput.trim());
      setLoadedDeal(deal);
      if (deal.fund_tx_hash) {
        setTxHashInput(deal.fund_tx_hash);
      }
    } catch (err: unknown) {
      setDealError(
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
          'Failed to load deal record from backend.'
      );
      setLoadedDeal(null);
    } finally {
      setDealLoading(false);
    }
  };

  const handleVerifyExistingTx = async () => {
    if (!loadedDeal || !txHashInput.trim()) return;
    try {
      setVerifying(true);
      setDealError(null);
      setVerifySuccess(null);
      const updated = await verifyFunding(loadedDeal.id, txHashInput.trim());
      setLoadedDeal(updated);
      setVerifySuccess('Transaction successfully verified by backend! Deal state is now FUNDED.');
    } catch (err: unknown) {
      setDealError(
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
          'Backend verification failed.'
      );
    } finally {
      setVerifying(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight">Escrow & Transactions</h1>
          <p className="text-slate-400 text-sm mt-1">
            Interact directly with the CipherTradeEscrow smart contract and backend verification service.
          </p>
        </div>
        <Button
          onClick={() => setModalOpen(true)}
          leftIcon={<ShieldCheck className="w-4 h-4" />}
        >
          Fund Escrow Deal
        </Button>
      </div>

      {/* Contract & Wallet Status Summary */}
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
          <div className={`w-10 h-10 rounded-lg flex items-center justify-center shrink-0 ${
            isConnected && isCorrectNetwork
              ? 'bg-green-500/10 border border-green-500/20 text-green-400'
              : 'bg-amber-500/10 border border-amber-500/20 text-amber-400'
          }`}>
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

      {/* How Escrow Works Info Banner */}
      <div className="p-4 rounded-xl bg-blue-950/30 border border-blue-500/20 flex items-start gap-3">
        <Info className="w-5 h-5 text-blue-400 shrink-0 mt-0.5" />
        <div className="text-sm space-y-1 text-slate-300">
          <p className="font-medium text-white">Non-Custodial Smart Contract Escrow & Dual Verification</p>
          <p className="text-xs text-slate-400 leading-relaxed">
            Funds deposited via <code className="text-cyan-400 font-mono">fundDeal()</code> remain locked in the verified Solidity contract. The backend independently audits the receipt, chain ID, mined event logs, agreement hash, and contract state before advancing deal state.
          </p>
        </div>
      </div>

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
            disabled={dealLoading || !dealIdInput.trim()}
            leftIcon={<Search className="w-4 h-4" />}
          >
            Lookup
          </Button>
        </div>

        {dealLoading && (
          <div className="flex justify-center py-4">
            <Spinner size="md" />
          </div>
        )}

        {dealError && (
          <div className="p-3 rounded-lg bg-red-950/40 border border-red-500/20 text-red-300 text-xs flex items-center gap-2">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span>{dealError}</span>
          </div>
        )}

        {verifySuccess && (
          <div className="p-3 rounded-lg bg-green-950/40 border border-green-500/20 text-green-300 text-xs flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 shrink-0" />
            <span>{verifySuccess}</span>
          </div>
        )}

        {loadedDeal && !dealLoading && (
          <div className="p-4 rounded-lg bg-slate-900/60 border border-slate-800 space-y-3 text-sm">
            <div className="flex items-center justify-between pb-2 border-b border-slate-800">
              <span className="font-mono text-xs text-slate-300">Deal: {loadedDeal.id}</span>
              <Badge variant={loadedDeal.on_chain_state === 'FUNDED' ? 'success' : 'warning'}>
                {loadedDeal.on_chain_state || 'PENDING VERIFICATION'}
              </Badge>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
              <div>
                <span className="text-slate-400">Amount:</span>
                <p className="font-semibold text-white">{loadedDeal.amount_eth} ETH</p>
              </div>
              <div>
                <span className="text-slate-400">Deadline:</span>
                <p className="text-slate-200">{new Date(loadedDeal.deadline_ts * 1000).toLocaleString()}</p>
              </div>
              <div>
                <span className="text-slate-400">On-Chain Deal ID:</span>
                <p className="font-mono text-cyan-400">
                  {loadedDeal.chain_deal_id !== null ? `#${loadedDeal.chain_deal_id}` : 'Not linked yet'}
                </p>
              </div>
              <div>
                <span className="text-slate-400">Verified Tx Hash:</span>
                <p className="font-mono text-slate-300 truncate">
                  {loadedDeal.fund_tx_hash || 'Not verified yet'}
                </p>
              </div>
            </div>

            {/* If not yet verified or retrying verification */}
            {loadedDeal.on_chain_state !== 'FUNDED' && (
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

      {/* Fund Escrow Modal */}
      <FundEscrowModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onSuccess={(result) => {
          console.log('Funded & verified escrow deal:', result);
          if (result.deal) {
            setLoadedDeal(result.deal);
            setDealIdInput(result.deal.id);
          }
        }}
      />
    </div>
  );
}

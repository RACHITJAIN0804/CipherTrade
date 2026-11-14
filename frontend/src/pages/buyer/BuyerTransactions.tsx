import { useState } from 'react';
import { Shield, ShieldCheck, Info, CheckCircle2, Lock } from 'lucide-react';
import { OnChainDealCard } from '../../components/escrow/OnChainDealCard';
import { FundEscrowModal } from '../../components/escrow/FundEscrowModal';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { ESCROW_CONTRACT_ADDRESS, ESCROW_CHAIN_ID } from '../../config/contracts';
import { useWallet } from '../../context/WalletContext';

export function BuyerTransactions() {
  const { account, isConnected, isCorrectNetwork, formatAddress } = useWallet();
  const [modalOpen, setModalOpen] = useState(false);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight">Escrow & Transactions</h1>
          <p className="text-slate-400 text-sm mt-1">
            Interact directly with the CipherTradeEscrow smart contract on Anvil Local.
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
          <p className="font-medium text-white">Trustless Smart Contract Escrow Foundation</p>
          <p className="text-xs text-slate-400 leading-relaxed">
            Funds deposited via <code className="text-cyan-400 font-mono">fundDeal()</code> remain non-custodially locked in the verified Solidity contract. The seller cannot withdraw until you explicitly confirm delivery on-chain. If delivery fails past the deadline, you retain full unilateral cancellation and refund rights.
          </p>
        </div>
      </div>

      {/* On-Chain Escrow Explorer */}
      <OnChainDealCard />

      {/* Fund Escrow Modal */}
      <FundEscrowModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onSuccess={(result) => {
          console.log('Funded escrow:', result);
        }}
      />
    </div>
  );
}

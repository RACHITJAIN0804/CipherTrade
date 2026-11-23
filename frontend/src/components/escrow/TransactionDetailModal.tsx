import { useState } from 'react';
import {
  Shield,
  Clock,
  User,
  Hash,
  AlertTriangle,
  CheckCircle2,
  Copy,
  RefreshCw,
  AlertCircle,
  FileText,
  Tag,
} from 'lucide-react';
import { Modal } from '../ui/Modal';
import { Button } from '../ui/Button';
import { Badge } from '../ui/Badge';
import { Spinner } from '../ui/Spinner';
import { useWallet } from '../../context/WalletContext';
import {
  confirmDeliveryOnChain,
  cancelExpiredOnChain,
  withdrawPaymentOnChain,
  refundBuyerOnChain,
  raiseDisputeOnChain,
  formatContractError,
} from '../../services/contractService';
import { syncDealState } from '../../services/escrowService';
import type { EscrowTransaction, EscrowState } from '../../types';

interface TransactionDetailModalProps {
  open: boolean;
  onClose: () => void;
  deal: EscrowTransaction | null;
  viewerRole: 'buyer' | 'seller';
  onDealUpdated?: (updated: EscrowTransaction) => void;
}

export function TransactionDetailModal({
  open,
  onClose,
  deal,
  viewerRole,
  onDealUpdated,
}: TransactionDetailModalProps) {
  const { account, isConnected, isCorrectNetwork, signer, formatAddress, connectWallet, switchToAnvil } = useWallet();

  const [currentDeal, setCurrentDeal] = useState<EscrowTransaction | null>(deal);
  const [loadingAction, setLoadingAction] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  // Sync state if prop changes
  if (deal && deal.id !== currentDeal?.id) {
    setCurrentDeal(deal);
    setActionError(null);
    setActionSuccess(null);
  }

  if (!open || !currentDeal) return null;

  const nowSeconds = Math.floor(Date.now() / 1000);
  const isDeadlinePassed = nowSeconds > currentDeal.deadline_ts;
  const deadlineDate = new Date(currentDeal.deadline_ts * 1000);
  const createdDate = new Date(currentDeal.created_at);

  const copyToClipboard = (text: string, key: string) => {
    navigator.clipboard.writeText(text);
    setCopiedKey(key);
    setTimeout(() => setCopiedKey(null), 2000);
  };

  const getStatusBadge = (state: EscrowState | null) => {
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
        return <Badge variant="warning">PENDING VERIFICATION</Badge>;
    }
  };

  const handleSync = async () => {
    try {
      setSyncing(true);
      setActionError(null);
      const updated = await syncDealState(currentDeal.id);
      setCurrentDeal(updated);
      if (onDealUpdated) onDealUpdated(updated);
      setActionSuccess('Deal state successfully synchronized from the blockchain.');
    } catch (err: unknown) {
      setActionError(
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
          'Failed to sync state from blockchain.'
      );
    } finally {
      setSyncing(false);
    }
  };

  const executeContractAction = async (
    actionName: string,
    actionFn: (chainDealId: number, signerInstance: any, contractIdentity?: any) => Promise<string>,
    successMsg: string
  ) => {
    if (!isConnected) {
      await connectWallet();
      return;
    }
    if (!isCorrectNetwork) {
      await switchToAnvil();
      return;
    }
    if (!signer) {
      setActionError('Wallet signer not found. Please reconnect MetaMask.');
      return;
    }
    if (currentDeal.chain_deal_id === null || currentDeal.chain_deal_id === undefined) {
      setActionError('This deal does not have a linked on-chain deal ID.');
      return;
    }

    // Verify connected wallet matches the role's registered wallet address
    if (viewerRole === 'buyer' && currentDeal.buyer_wallet_address) {
      if (account && account.toLowerCase() !== currentDeal.buyer_wallet_address.toLowerCase()) {
        setActionError(
          `Connected wallet (${formatAddress(account)}) does not match the buyer's registered wallet (${formatAddress(currentDeal.buyer_wallet_address)}). Please switch accounts in MetaMask.`
        );
        return;
      }
    }
    if (viewerRole === 'seller' && currentDeal.seller_wallet_address) {
      if (account && account.toLowerCase() !== currentDeal.seller_wallet_address.toLowerCase()) {
        setActionError(
          `Connected wallet (${formatAddress(account)}) does not match the seller's registered wallet (${formatAddress(currentDeal.seller_wallet_address)}). Please switch accounts in MetaMask.`
        );
        return;
      }
    }

    try {
      setLoadingAction(actionName);
      setActionError(null);
      setActionSuccess(null);

      const contractIdentity = {
        contract_address: currentDeal.contract_address,
        contract_version: currentDeal.contract_version,
      };

      const txHash = await actionFn(currentDeal.chain_deal_id, signer, contractIdentity);

      // Automatically sync on-chain state to the backend database
      let updatedDeal: EscrowTransaction;
      try {
        updatedDeal = await syncDealState(currentDeal.id);
      } catch {
        // If sync failed, keep existing deal but note success
        updatedDeal = currentDeal;
      }

      setCurrentDeal(updatedDeal);
      if (onDealUpdated) onDealUpdated(updatedDeal);
      setActionSuccess(`${successMsg} (Tx: ${txHash.slice(0, 10)}...${txHash.slice(-8)})`);
    } catch (err: unknown) {
      setActionError(formatContractError(err));
    } finally {
      setLoadingAction(null);
    }
  };

  return (
    <Modal open={open} onClose={onClose} size="lg">
      <div className="space-y-5">
        {/* Header */}
        <div className="flex items-start justify-between gap-4 pb-4 border-b border-slate-800">
          <div>
            <div className="flex items-center gap-2">
              <Shield className="w-5 h-5 text-cyan-400" />
              <h2 className="text-lg font-bold text-white">Escrow Transaction Details</h2>
            </div>
            <p className="text-xs text-slate-400 mt-1 font-mono">
              Deal UUID: {currentDeal.id}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {getStatusBadge(currentDeal.on_chain_state)}
            <Button
              variant="ghost"
              size="sm"
              onClick={handleSync}
              disabled={syncing}
              title="Sync state from blockchain"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${syncing ? 'animate-spin' : ''}`} />
            </Button>
          </div>
        </div>

        {/* Action Error / Success alerts */}
        {actionError && (
          <div className="p-3 rounded-lg bg-red-950/40 border border-red-500/20 text-red-300 text-xs flex items-center gap-2">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span className="flex-1">{actionError}</span>
          </div>
        )}
        {actionSuccess && (
          <div className="p-3 rounded-lg bg-green-950/40 border border-green-500/20 text-green-300 text-xs flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 shrink-0" />
            <span className="flex-1">{actionSuccess}</span>
          </div>
        )}

        {/* Product / Requirement Details */}
        <div className="p-4 rounded-xl bg-slate-900/60 border border-slate-800 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <FileText className="w-4 h-4 text-cyan-400" />
              <span className="text-sm font-semibold text-white">
                {currentDeal.requirement_title || 'Marketplace Item'}
              </span>
            </div>
            {currentDeal.requirement_category && (
              <span className="text-xs px-2 py-0.5 rounded bg-slate-800 text-slate-300 flex items-center gap-1">
                <Tag className="w-3 h-3" />
                {currentDeal.requirement_category}
              </span>
            )}
          </div>
          {currentDeal.offer_terms && (
            <div className="text-xs text-slate-300 bg-slate-950/40 p-2.5 rounded border border-slate-800/80">
              <span className="text-slate-400 block mb-1 font-medium">Agreed Offer Terms:</span>
              <p className="whitespace-pre-wrap">{currentDeal.offer_terms}</p>
            </div>
          )}

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-2 text-xs">
            <div>
              <span className="text-slate-400 block">Amount</span>
              <span className="text-white font-bold text-sm">{currentDeal.amount_eth} ETH</span>
            </div>
            <div>
              <span className="text-slate-400 block">On-Chain ID</span>
              <span className="font-mono text-cyan-400 font-semibold">
                {currentDeal.chain_deal_id !== null ? `#${currentDeal.chain_deal_id}` : 'None'}
              </span>
            </div>
            <div>
              <span className="text-slate-400 block">Created</span>
              <span className="text-slate-200">{createdDate.toLocaleDateString()}</span>
            </div>
            <div>
              <span className="text-slate-400 block">Delivery Deadline</span>
              <div className="flex items-center gap-1">
                <Clock className="w-3 h-3 text-slate-400" />
                <span className={isDeadlinePassed ? 'text-amber-400 font-medium' : 'text-slate-200'}>
                  {deadlineDate.toLocaleDateString()}
                </span>
              </div>
              <span className="text-[10px] text-slate-400 block">
                {isDeadlinePassed ? '(Deadline Elapsed)' : '(Active)'}
              </span>
            </div>
          </div>
        </div>

        {/* Counterparty Information */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
          <div className="p-3 rounded-lg bg-slate-900/40 border border-slate-800 space-y-1.5">
            <div className="flex items-center gap-1.5 text-slate-400">
              <User className="w-3.5 h-3.5 text-blue-400" />
              <span className="font-medium text-slate-300">Buyer</span>
              {viewerRole === 'buyer' && (
                <span className="text-[10px] px-1.5 py-0.2 rounded bg-blue-500/10 text-blue-400 border border-blue-500/20 ml-auto">
                  You
                </span>
              )}
            </div>
            <p className="font-semibold text-white">{currentDeal.buyer_display_name || 'Buyer'}</p>
            {currentDeal.buyer_wallet_address ? (
              <div className="flex items-center justify-between font-mono text-[11px] text-slate-300 bg-slate-950/60 p-1.5 rounded">
                <span>{formatAddress(currentDeal.buyer_wallet_address)}</span>
                <button
                  type="button"
                  onClick={() => copyToClipboard(currentDeal.buyer_wallet_address!, 'buyer_addr')}
                  className="text-slate-400 hover:text-white"
                  title="Copy address"
                >
                  {copiedKey === 'buyer_addr' ? (
                    <CheckCircle2 className="w-3 h-3 text-green-400" />
                  ) : (
                    <Copy className="w-3 h-3" />
                  )}
                </button>
              </div>
            ) : (
              <p className="text-slate-400 italic">No wallet registered</p>
            )}
          </div>

          <div className="p-3 rounded-lg bg-slate-900/40 border border-slate-800 space-y-1.5">
            <div className="flex items-center gap-1.5 text-slate-400">
              <User className="w-3.5 h-3.5 text-emerald-400" />
              <span className="font-medium text-slate-300">Seller</span>
              {viewerRole === 'seller' && (
                <span className="text-[10px] px-1.5 py-0.2 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 ml-auto">
                  You
                </span>
              )}
            </div>
            <p className="font-semibold text-white">{currentDeal.seller_display_name || 'Seller'}</p>
            {currentDeal.seller_wallet_address ? (
              <div className="flex items-center justify-between font-mono text-[11px] text-slate-300 bg-slate-950/60 p-1.5 rounded">
                <span>{formatAddress(currentDeal.seller_wallet_address)}</span>
                <button
                  type="button"
                  onClick={() => copyToClipboard(currentDeal.seller_wallet_address!, 'seller_addr')}
                  className="text-slate-400 hover:text-white"
                  title="Copy address"
                >
                  {copiedKey === 'seller_addr' ? (
                    <CheckCircle2 className="w-3 h-3 text-green-400" />
                  ) : (
                    <Copy className="w-3 h-3" />
                  )}
                </button>
              </div>
            ) : (
              <p className="text-slate-400 italic">No wallet registered</p>
            )}
          </div>
        </div>

        {/* Blockchain Cryptographic Proofs */}
        <div className="p-3 rounded-lg bg-slate-950/60 border border-slate-800 space-y-2 text-xs">
          <div className="flex items-center gap-1.5 text-slate-300 font-medium">
            <Hash className="w-3.5 h-3.5 text-cyan-400" />
            <span>On-Chain Cryptographic Proofs</span>
          </div>

          <div className="space-y-1 font-mono text-[11px]">
            {currentDeal.fund_tx_hash && (
              <div className="flex items-center justify-between text-slate-400 bg-slate-900/50 p-1.5 rounded">
                <span className="text-slate-400">Tx Hash:</span>
                <span className="text-slate-200 truncate mx-2">{currentDeal.fund_tx_hash}</span>
                <button
                  type="button"
                  onClick={() => copyToClipboard(currentDeal.fund_tx_hash!, 'tx_hash')}
                  className="text-slate-400 hover:text-white shrink-0"
                >
                  {copiedKey === 'tx_hash' ? (
                    <CheckCircle2 className="w-3 h-3 text-green-400" />
                  ) : (
                    <Copy className="w-3 h-3" />
                  )}
                </button>
              </div>
            )}
            {currentDeal.agreement_hash && (
              <div className="flex items-center justify-between text-slate-400 bg-slate-900/50 p-1.5 rounded">
                <span className="text-slate-400">Agreement Hash:</span>
                <span className="text-slate-200 truncate mx-2">{currentDeal.agreement_hash}</span>
                <button
                  type="button"
                  onClick={() => copyToClipboard(currentDeal.agreement_hash!, 'ag_hash')}
                  className="text-slate-400 hover:text-white shrink-0"
                >
                  {copiedKey === 'ag_hash' ? (
                    <CheckCircle2 className="w-3 h-3 text-green-400" />
                  ) : (
                    <Copy className="w-3 h-3" />
                  )}
                </button>
              </div>
            )}
            {currentDeal.contract_version && (
              <div className="flex items-center justify-between text-slate-400 bg-slate-900/50 p-1.5 rounded">
                <span className="text-slate-400">Contract Version:</span>
                <span className="text-cyan-400 font-semibold">{currentDeal.contract_version.toUpperCase()}</span>
              </div>
            )}
            {currentDeal.contract_address && (
              <div className="flex items-center justify-between text-slate-400 bg-slate-900/50 p-1.5 rounded">
                <span className="text-slate-400">Contract Address:</span>
                <span className="text-slate-200 truncate mx-2">{formatAddress(currentDeal.contract_address)}</span>
                <button
                  type="button"
                  onClick={() => copyToClipboard(currentDeal.contract_address!, 'contract_addr')}
                  className="text-slate-400 hover:text-white shrink-0"
                  title="Copy contract address"
                >
                  {copiedKey === 'contract_addr' ? (
                    <CheckCircle2 className="w-3 h-3 text-green-400" />
                  ) : (
                    <Copy className="w-3 h-3" />
                  )}
                </button>
              </div>
            )}
          </div>
        </div>

        {/* Smart Contract Actions Section */}
        <div className="pt-2 border-t border-slate-800 space-y-3">
          <span className="text-xs font-semibold text-slate-300 block">Authorized Actions</span>

          {currentDeal.chain_deal_id === null ? (
            <p className="text-xs text-slate-400 italic">
              Deal is not yet linked to an on-chain escrow record. Smart contract actions are disabled.
            </p>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              {/* BUYER ACTIONS */}
              {viewerRole === 'buyer' && (
                <>
                  {currentDeal.on_chain_state === 'FUNDED' && (
                    <>
                      <Button
                        size="sm"
                        variant="primary"
                        disabled={!!loadingAction}
                        onClick={() =>
                          executeContractAction(
                            'confirmDelivery',
                            confirmDeliveryOnChain,
                            'Delivery confirmed! Seller can now withdraw payment.'
                          )
                        }
                      >
                        {loadingAction === 'confirmDelivery' ? (
                          <Spinner size="sm" />
                        ) : (
                          'Confirm Delivery'
                        )}
                      </Button>

                      {isDeadlinePassed ? (
                        <Button
                          size="sm"
                          variant="secondary"
                          disabled={!!loadingAction}
                          onClick={() =>
                            executeContractAction(
                              'cancelExpired',
                              cancelExpiredOnChain,
                              'Deal cancelled and funds refunded to your wallet.'
                            )
                          }
                        >
                          {loadingAction === 'cancelExpired' ? (
                            <Spinner size="sm" />
                          ) : (
                            'Cancel & Refund (Expired)'
                          )}
                        </Button>
                      ) : (
                        <span
                          className="text-[11px] text-slate-400 italic"
                          title={`Cancel becomes available on ${deadlineDate.toLocaleString()}`}
                        >
                          (Cancellation unlocks after deadline)
                        </span>
                      )}

                      <Button
                        size="sm"
                        variant="danger"
                        disabled={!!loadingAction}
                        onClick={() =>
                          executeContractAction(
                            'raiseDispute',
                            raiseDisputeOnChain,
                            'Dispute raised. Escrow funds are locked for arbitrator review.'
                          )
                        }
                      >
                        {loadingAction === 'raiseDispute' ? (
                          <Spinner size="sm" />
                        ) : (
                          'Raise Dispute'
                        )}
                      </Button>
                    </>
                  )}

                  {currentDeal.on_chain_state === 'CONFIRMED' && (
                    <Button
                      size="sm"
                      variant="danger"
                      disabled={!!loadingAction}
                      onClick={() =>
                        executeContractAction(
                          'raiseDispute',
                          raiseDisputeOnChain,
                          'Dispute raised. Escrow funds are locked for arbitrator review.'
                        )
                      }
                    >
                      {loadingAction === 'raiseDispute' ? (
                        <Spinner size="sm" />
                      ) : (
                        'Raise Dispute'
                      )}
                    </Button>
                  )}

                  {['RELEASED', 'REFUNDED', 'CANCELLED', 'RESOLVED'].includes(
                    currentDeal.on_chain_state || ''
                  ) && (
                    <p className="text-xs text-slate-400 italic">
                      This escrow deal has reached its terminal state ({currentDeal.on_chain_state}). No further actions are required.
                    </p>
                  )}
                  {currentDeal.on_chain_state === 'DISPUTED' && (
                    <p className="text-xs text-amber-400 flex items-center gap-1.5">
                      <AlertTriangle className="w-3.5 h-3.5" />
                      Deal is under dispute. Escrow funds are locked pending arbitrator ruling.
                    </p>
                  )}
                </>
              )}

              {/* SELLER ACTIONS */}
              {viewerRole === 'seller' && (
                <>
                  {currentDeal.on_chain_state === 'CONFIRMED' && (
                    <>
                      <Button
                        size="sm"
                        variant="primary"
                        disabled={!!loadingAction}
                        onClick={() =>
                          executeContractAction(
                            'withdrawPayment',
                            withdrawPaymentOnChain,
                            'Payment successfully released and transferred to your wallet!'
                          )
                        }
                      >
                        {loadingAction === 'withdrawPayment' ? (
                          <Spinner size="sm" />
                        ) : (
                          'Withdraw Payment'
                        )}
                      </Button>
                      <Button
                        size="sm"
                        variant="danger"
                        disabled={!!loadingAction}
                        onClick={() =>
                          executeContractAction(
                            'raiseDispute',
                            raiseDisputeOnChain,
                            'Dispute raised. Escrow funds are locked for arbitrator review.'
                          )
                        }
                      >
                        {loadingAction === 'raiseDispute' ? (
                          <Spinner size="sm" />
                        ) : (
                          'Raise Dispute'
                        )}
                      </Button>
                    </>
                  )}

                  {currentDeal.on_chain_state === 'FUNDED' && (
                    <>
                      <Button
                        size="sm"
                        variant="secondary"
                        disabled={!!loadingAction}
                        onClick={() =>
                          executeContractAction(
                            'refundBuyer',
                            refundBuyerOnChain,
                            'Refund successfully issued to buyer.'
                          )
                        }
                      >
                        {loadingAction === 'refundBuyer' ? (
                          <Spinner size="sm" />
                        ) : (
                          'Voluntary Refund to Buyer'
                        )}
                      </Button>
                      <Button
                        size="sm"
                        variant="danger"
                        disabled={!!loadingAction}
                        onClick={() =>
                          executeContractAction(
                            'raiseDispute',
                            raiseDisputeOnChain,
                            'Dispute raised. Escrow funds are locked for arbitrator review.'
                          )
                        }
                      >
                        {loadingAction === 'raiseDispute' ? (
                          <Spinner size="sm" />
                        ) : (
                          'Raise Dispute'
                        )}
                      </Button>
                    </>
                  )}

                  {['RELEASED', 'REFUNDED', 'CANCELLED', 'RESOLVED'].includes(
                    currentDeal.on_chain_state || ''
                  ) && (
                    <p className="text-xs text-slate-400 italic">
                      This escrow deal has reached its terminal state ({currentDeal.on_chain_state}). No further actions are required.
                    </p>
                  )}
                  {currentDeal.on_chain_state === 'DISPUTED' && (
                    <p className="text-xs text-amber-400 flex items-center gap-1.5">
                      <AlertTriangle className="w-3.5 h-3.5" />
                      Deal is under dispute. Escrow funds are locked pending arbitrator ruling.
                    </p>
                  )}
                </>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="pt-3 border-t border-slate-800 flex justify-end">
          <Button variant="ghost" size="sm" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
    </Modal>
  );
}

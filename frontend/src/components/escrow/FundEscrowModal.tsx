import { useState, useEffect } from 'react';
import {
  Coins,
  ShieldCheck,
  AlertTriangle,
  CheckCircle2,
  Copy,
  Check,
  Clock,
  ArrowRight,
  RefreshCw,
  Wallet,
  AlertCircle,
  Database,
} from 'lucide-react';
import { isAddress, getAddress, parseEther } from 'ethers';
import { useWallet } from '../../context/WalletContext';
import { useAuth } from '../../context/AuthContext';
import {
  fundEscrowDeal,
  generateAgreementHash,
  formatContractError,
  type FundDealResult,
} from '../../services/contractService';
import {
  createEscrowDeal,
  verifyFunding,
} from '../../services/escrowService';
import { getUser, updateMe } from '../../services/userService';
import { ESCROW_CONTRACT_ADDRESS, ESCROW_CHAIN_ID } from '../../config/contracts';
import { Modal } from '../ui/Modal';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { Spinner } from '../ui/Spinner';
import type { EscrowDeal } from '../../types';

export interface FundEscrowModalProps {
  open: boolean;
  onClose: () => void;
  dealId?: string; // Optional backend deal UUID if already created
  offerId?: string;
  sellerId?: string;
  sellerAddress?: string;
  amountEth?: number | string;
  deliveryDeadline?: number; // Unix timestamp in seconds
  terms?: string;
  agreementHash?: string;
  onSuccess?: (result: { onChain: FundDealResult; deal: EscrowDeal }) => void;
}

type Step =
  | 'form'
  | 'awaiting_signature'
  | 'pending_tx'
  | 'verifying_backend'
  | 'verified'
  | 'verification_failed'
  | 'blockchain_error';

export function FundEscrowModal({
  open,
  onClose,
  dealId: initialDealId = '',
  offerId = '',
  sellerId = '',
  sellerAddress: initialSellerAddress = '',
  amountEth: initialAmountEth = '',
  deliveryDeadline: initialDeadline = 0,
  terms = '',
  agreementHash: initialAgreementHash = '',
  onSuccess,
}: FundEscrowModalProps) {
  const {
    account,
    chainId,
    isConnected,
    isCorrectNetwork,
    signer,
    connectWallet,
    switchToAnvil,
    formatAddress,
  } = useWallet();

  const { user } = useAuth();

  // Form state
  const [sellerAddr, setSellerAddr] = useState('');
  const [registeredSellerWallet, setRegisteredSellerWallet] = useState<string | null>(null);
  const [checkingSellerWallet, setCheckingSellerWallet] = useState(false);
  const [amount, setAmount] = useState('');
  const [deadlineDate, setDeadlineDate] = useState('');
  const [customHash, setCustomHash] = useState('');
  const [useAutoHash, setUseAutoHash] = useState(true);

  // Backend Deal ID (UUID)
  const [activeDealId, setActiveDealId] = useState<string>(initialDealId);

  // Flow & transaction state
  const [step, setStep] = useState<Step>('form');
  const [pendingTxHash, setPendingTxHash] = useState<string | null>(null);
  const [confirmedResult, setConfirmedResult] = useState<FundDealResult | null>(null);
  const [verifiedDeal, setVerifiedDeal] = useState<EscrowDeal | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [verificationError, setVerificationError] = useState<string | null>(null);
  const [copiedHash, setCopiedHash] = useState(false);
  const [copiedTx, setCopiedTx] = useState(false);
  const [copiedDealId, setCopiedDealId] = useState(false);

  // Initialize form state when modal opens
  useEffect(() => {
    if (open) {
      setStep('form');
      setPendingTxHash(null);
      setConfirmedResult(null);
      setVerifiedDeal(null);
      setErrorMessage(null);
      setVerificationError(null);
      setActiveDealId(initialDealId);

      setSellerAddr(initialSellerAddress);
      setAmount(initialAmountEth ? String(initialAmountEth) : '');

      if (initialDeadline && initialDeadline > 0) {
        const d = new Date(initialDeadline * 1000);
        setDeadlineDate(d.toISOString().slice(0, 16));
      } else {
        const tomorrow = new Date(Date.now() + 24 * 60 * 60 * 1000);
        setDeadlineDate(tomorrow.toISOString().slice(0, 16));
      }

      if (initialAgreementHash) {
        setCustomHash(initialAgreementHash);
        setUseAutoHash(false);
      } else {
        setCustomHash('');
        setUseAutoHash(true);
      }

      // Check seller registered wallet if sellerId is provided
      if (sellerId) {
        setCheckingSellerWallet(true);
        getUser(sellerId)
          .then((sellerInfo) => {
            if (sellerInfo.wallet_address) {
              setRegisteredSellerWallet(sellerInfo.wallet_address);
              setSellerAddr(sellerInfo.wallet_address);
            } else {
              setRegisteredSellerWallet(null);
            }
          })
          .catch(() => {
            setRegisteredSellerWallet(null);
          })
          .finally(() => {
            setCheckingSellerWallet(false);
          });
      }
    }
  }, [
    open,
    initialDealId,
    sellerId,
    initialSellerAddress,
    initialAmountEth,
    initialDeadline,
    initialAgreementHash,
  ]);

  // Compute active agreement hash
  const computedDeadlineTs = deadlineDate ? Math.floor(new Date(deadlineDate).getTime() / 1000) : 0;
  const effectiveHash = useAutoHash
    ? generateAgreementHash(offerId || 'generic', terms || 'standard', amount || '0', computedDeadlineTs)
    : customHash.trim();

  // Copy helpers
  const handleCopy = (text: string, type: 'hash' | 'tx' | 'dealId') => {
    navigator.clipboard.writeText(text);
    if (type === 'hash') {
      setCopiedHash(true);
      setTimeout(() => setCopiedHash(false), 2000);
    } else if (type === 'tx') {
      setCopiedTx(true);
      setTimeout(() => setCopiedTx(false), 2000);
    } else {
      setCopiedDealId(true);
      setTimeout(() => setCopiedDealId(false), 2000);
    }
  };

  // Self-deal check
  const isSelfDeal =
    account && sellerAddr && isAddress(sellerAddr)
      ? getAddress(account).toLowerCase() === getAddress(sellerAddr).toLowerCase()
      : false;

  // Validation logic
  const isAddressValid = Boolean(sellerAddr && isAddress(sellerAddr));
  const isAmountValid = Boolean(amount && !isNaN(Number(amount)) && Number(amount) > 0);
  const isDeadlineValid = Boolean(computedDeadlineTs > Math.floor(Date.now() / 1000));
  const isHashValid = Boolean(/^0x[0-9a-fA-F]{64}$/.test(effectiveHash));

  // Seller wallet mismatch warning
  const isSellerWalletMismatch = Boolean(
    registeredSellerWallet &&
      isAddressValid &&
      getAddress(sellerAddr).toLowerCase() !== getAddress(registeredSellerWallet).toLowerCase()
  );

  const canSubmit =
    isConnected &&
    isCorrectNetwork &&
    signer &&
    isAddressValid &&
    isAmountValid &&
    isDeadlineValid &&
    isHashValid &&
    !isSelfDeal &&
    step === 'form';

  /**
   * Helper to execute backend verification on an already confirmed transaction.
   */
  const executeBackendVerification = async (dealIdToVerify: string, txHashToVerify: string) => {
    setStep('verifying_backend');
    setVerificationError(null);
    try {
      const verified = await verifyFunding(dealIdToVerify, txHashToVerify);
      setVerifiedDeal(verified);
      setStep('verified');
      if (onSuccess && confirmedResult) {
        onSuccess({ onChain: confirmedResult, deal: verified });
      }
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (err as Error)?.message ??
        'Backend failed to verify on-chain funding transaction.';
      setVerificationError(msg);
      setStep('verification_failed');
    }
  };

  /**
   * Primary Fund & Verification workflow
   */
  const handleFund = async () => {
    if (!signer || !account) {
      setErrorMessage('Please connect your MetaMask wallet first.');
      return;
    }
    if (!isCorrectNetwork) {
      setErrorMessage('Please switch to the Anvil Local network (Chain ID 31337).');
      return;
    }
    if (isSelfDeal) {
      setErrorMessage('Buyer and seller wallet cannot be identical (SelfDeal).');
      return;
    }

    try {
      setErrorMessage(null);
      setVerificationError(null);

      // Pre-flight 1: Ensure buyer's wallet is saved on their profile
      if (!user?.wallet_address || getAddress(user.wallet_address).toLowerCase() !== getAddress(account).toLowerCase()) {
        try {
          await updateMe({ wallet_address: account });
        } catch {
          // Non-blocking if already matched
        }
      }

      // Pre-flight 2: Create or retrieve backend EscrowDeal record to obtain the authoritative deal UUID
      let targetDealId = activeDealId;
      let finalAgreementHash = effectiveHash;

      if (!targetDealId && offerId) {
        try {
          const dealRecord = await createEscrowDeal({
            offer_id: offerId,
            amount_eth: parseFloat(amount),
            deadline_ts: computedDeadlineTs,
            agreement_hash: effectiveHash,
          });
          targetDealId = dealRecord.id;
          setActiveDealId(dealRecord.id);

          // If the deal had an existing agreement_hash, adopt it
          if (dealRecord.agreement_hash) {
            finalAgreementHash = dealRecord.agreement_hash;
          }
        } catch (dealErr: unknown) {
          const msg =
            (dealErr as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
            'Failed to create backend escrow deal record.';
          setErrorMessage(msg);
          setStep('blockchain_error');
          return;
        }
      }

      // Step: Awaiting MetaMask signature
      setStep('awaiting_signature');

      // Dispatch smart contract transaction
      const onChainResult = await fundEscrowDeal({
        sellerAddress: sellerAddr,
        deadlineTs: computedDeadlineTs,
        agreementHash: finalAgreementHash,
        amountEth: amount,
        signer,
        onTxSubmitted: (txHash: string) => {
          setPendingTxHash(txHash);
          setStep('pending_tx');
        },
      });

      setConfirmedResult(onChainResult);

      // Step: Trigger backend independent verification
      if (targetDealId) {
        await executeBackendVerification(targetDealId, onChainResult.txHash);
      } else {
        // Fallback: If no backend deal ID could be established, remain in confirmed state
        setStep('verified');
      }
    } catch (err: unknown) {
      const formatted = formatContractError(err);
      setErrorMessage(formatted);
      setStep('blockchain_error');
    }
  };

  /**
   * Retry handler for backend verification (without broadcasting a new transaction)
   */
  const handleRetryVerification = async () => {
    if (!activeDealId || !confirmedResult) return;
    await executeBackendVerification(activeDealId, confirmedResult.txHash);
  };

  return (
    <Modal open={open} onClose={onClose} title="Fund Escrow & Verify On-Chain" size="lg">
      <div className="space-y-5 text-slate-200">
        {/* Network / Wallet Warning Banner */}
        {!isConnected && (
          <div className="p-4 rounded-lg bg-blue-900/20 border border-blue-500/30 flex items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <Wallet className="w-5 h-5 text-blue-400 shrink-0" />
              <div className="text-sm">
                <p className="font-medium text-white">MetaMask Wallet Required</p>
                <p className="text-slate-400">Connect your wallet to lock funds into smart contract escrow.</p>
              </div>
            </div>
            <Button size="sm" onClick={connectWallet} leftIcon={<Wallet className="w-4 h-4" />}>
              Connect
            </Button>
          </div>
        )}

        {isConnected && !isCorrectNetwork && (
          <div className="p-4 rounded-lg bg-amber-900/20 border border-amber-500/30 flex items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <AlertTriangle className="w-5 h-5 text-amber-400 shrink-0" />
              <div className="text-sm">
                <p className="font-medium text-white">Wrong Network (Chain ID: {chainId})</p>
                <p className="text-slate-400">
                  Switch to Anvil Local (Chain ID {ESCROW_CHAIN_ID}) to interact with the deployed escrow contract.
                </p>
              </div>
            </div>
            <Button size="sm" variant="secondary" onClick={switchToAnvil}>
              Switch to Anvil
            </Button>
          </div>
        )}

        {/* STEP: Form */}
        {step === 'form' && (
          <div className="space-y-4">
            {/* Contract info bar */}
            <div className="px-3.5 py-2.5 rounded-lg bg-slate-900/60 border border-slate-800 text-xs text-slate-400 flex items-center justify-between">
              <span>Escrow Contract:</span>
              <span className="font-mono text-cyan-400">{formatAddress(ESCROW_CONTRACT_ADDRESS)}</span>
            </div>

            {/* Seller Address */}
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label className="block text-sm font-medium text-slate-300">
                  Seller Wallet Address (Recipient) *
                </label>
                {checkingSellerWallet ? (
                  <span className="text-xs text-slate-400 flex items-center gap-1">
                    <Spinner size="sm" /> Checking registered wallet...
                  </span>
                ) : registeredSellerWallet ? (
                  <span className="text-xs text-green-400 flex items-center gap-1">
                    <Check className="w-3.5 h-3.5" /> Registered profile wallet
                  </span>
                ) : sellerId ? (
                  <span className="text-xs text-amber-400 flex items-center gap-1">
                    <AlertTriangle className="w-3.5 h-3.5" /> No wallet registered in seller profile
                  </span>
                ) : null}
              </div>
              <Input
                placeholder="0x..."
                value={sellerAddr}
                onChange={(e) => setSellerAddr(e.target.value.trim())}
                error={
                  sellerAddr && !isAddressValid
                    ? 'Invalid Ethereum address format'
                    : isSelfDeal
                    ? 'Seller address cannot match your connected buyer address'
                    : isSellerWalletMismatch
                    ? `Warning: Address differs from seller's registered wallet (${formatAddress(registeredSellerWallet)})`
                    : undefined
                }
                hint={isAddressValid ? 'Valid 20-byte address' : 'Address of the counterparty seller'}
              />
            </div>

            {/* Amount and Deadline grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-slate-300 mb-1.5">
                  Escrow Amount (ETH) *
                </label>
                <Input
                  type="number"
                  step="0.0001"
                  min="0.0001"
                  placeholder="e.g. 0.5"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  leftElement={<Coins className="w-4 h-4 text-cyan-400" />}
                  error={amount && !isAmountValid ? 'Must be greater than 0' : undefined}
                  hint={
                    isAmountValid
                      ? `≈ ${(() => {
                          try {
                            return parseEther(amount).toString();
                          } catch {
                            return '0';
                          }
                        })()} Wei`
                      : 'Amount locked until delivery'
                  }
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-300 mb-1.5">
                  Delivery Deadline *
                </label>
                <Input
                  type="datetime-local"
                  value={deadlineDate}
                  onChange={(e) => setDeadlineDate(e.target.value)}
                  leftElement={<Clock className="w-4 h-4 text-blue-400" />}
                  error={deadlineDate && !isDeadlineValid ? 'Deadline must be in the future' : undefined}
                  hint={
                    isDeadlineValid
                      ? `Unix: ${computedDeadlineTs}`
                      : 'Buyer can cancel if seller fails to deliver by deadline'
                  }
                />
              </div>
            </div>

            {/* Agreement Hash */}
            <div className="space-y-2">
              <div className="flex items-center justify-between text-sm">
                <label className="font-medium text-slate-300">Agreement Hash (Integrity Fingerprint)</label>
                <button
                  type="button"
                  onClick={() => setUseAutoHash(!useAutoHash)}
                  className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
                >
                  {useAutoHash ? 'Enter Custom Hash' : 'Auto-Generate Hash'}
                </button>
              </div>

              {useAutoHash ? (
                <div className="p-3 rounded-lg bg-slate-900/80 border border-slate-800 flex items-center justify-between gap-2">
                  <div className="truncate font-mono text-xs text-slate-300">
                    {effectiveHash}
                  </div>
                  <button
                    type="button"
                    onClick={() => handleCopy(effectiveHash, 'hash')}
                    className="p-1.5 text-slate-400 hover:text-white rounded hover:bg-slate-800 shrink-0"
                    title="Copy agreement hash"
                  >
                    {copiedHash ? <Check className="w-4 h-4 text-green-400" /> : <Copy className="w-4 h-4" />}
                  </button>
                </div>
              ) : (
                <Input
                  placeholder="0x..."
                  value={customHash}
                  onChange={(e) => setCustomHash(e.target.value)}
                  error={customHash && !isHashValid ? 'Must be a 32-byte hex string (0x + 64 hex chars)' : undefined}
                  hint="32-byte keccak256 hash representing deal terms"
                />
              )}
            </div>

            {/* Buyer Wallet & Summary Box */}
            <div className="p-4 rounded-xl bg-slate-900/40 border border-slate-800/80 space-y-2 text-xs">
              <div className="flex justify-between text-slate-400">
                <span>Buyer (Your Account):</span>
                <span className="font-mono text-slate-200">
                  {account ? formatAddress(account) : 'Not connected'}
                </span>
              </div>
              <div className="flex justify-between text-slate-400">
                <span>Target Contract:</span>
                <span className="font-mono text-cyan-400">{formatAddress(ESCROW_CONTRACT_ADDRESS)}</span>
              </div>
              <div className="flex justify-between text-slate-400">
                <span>Network:</span>
                <span className="text-slate-200">Anvil Local (31337)</span>
              </div>
            </div>

            {/* Action Buttons */}
            <div className="flex justify-end gap-3 pt-2">
              <Button variant="secondary" onClick={onClose}>
                Cancel
              </Button>
              <Button
                variant="primary"
                onClick={handleFund}
                disabled={!canSubmit}
                leftIcon={<ShieldCheck className="w-4 h-4" />}
              >
                Deposit & Fund Escrow
              </Button>
            </div>
          </div>
        )}

        {/* STEP: Awaiting Signature */}
        {step === 'awaiting_signature' && (
          <div className="py-8 flex flex-col items-center justify-center text-center space-y-4">
            <div className="relative">
              <Spinner size="lg" />
              <Wallet className="w-5 h-5 text-blue-400 absolute inset-0 m-auto" />
            </div>
            <div>
              <h3 className="text-lg font-semibold text-white">Awaiting MetaMask Confirmation</h3>
              <p className="text-sm text-slate-400 max-w-sm mt-1">
                Please review and approve the <code className="text-cyan-400 font-mono">fundDeal</code> transaction in your MetaMask wallet popup.
              </p>
            </div>
            <div className="p-3 rounded-lg bg-blue-900/20 border border-blue-500/20 text-xs text-blue-300 max-w-md">
              Amount to lock: <span className="font-bold text-white">{amount} ETH</span> + standard gas fee
            </div>
          </div>
        )}

        {/* STEP: Pending Blockchain Transaction */}
        {step === 'pending_tx' && (
          <div className="py-8 flex flex-col items-center justify-center text-center space-y-4">
            <div className="relative">
              <Spinner size="lg" />
              <RefreshCw className="w-5 h-5 text-cyan-400 animate-spin absolute inset-0 m-auto" />
            </div>
            <div>
              <h3 className="text-lg font-semibold text-white">Transaction Submitted</h3>
              <p className="text-sm text-slate-400 max-w-sm mt-1">
                Waiting for blockchain transaction confirmation on Anvil...
              </p>
            </div>
            {pendingTxHash && (
              <div className="p-3 rounded-lg bg-slate-900/80 border border-slate-800 flex items-center justify-between gap-3 max-w-md w-full">
                <div className="truncate font-mono text-xs text-slate-300">
                  Tx: {pendingTxHash}
                </div>
                <button
                  type="button"
                  onClick={() => handleCopy(pendingTxHash, 'tx')}
                  className="p-1.5 text-slate-400 hover:text-white rounded hover:bg-slate-800 shrink-0"
                  title="Copy transaction hash"
                >
                  {copiedTx ? <Check className="w-4 h-4 text-green-400" /> : <Copy className="w-4 h-4" />}
                </button>
              </div>
            )}
          </div>
        )}

        {/* STEP: Verifying with Backend */}
        {step === 'verifying_backend' && (
          <div className="py-8 flex flex-col items-center justify-center text-center space-y-4">
            <div className="relative">
              <Spinner size="lg" />
              <Database className="w-5 h-5 text-blue-400 animate-pulse absolute inset-0 m-auto" />
            </div>
            <div>
              <h3 className="text-lg font-semibold text-white">Verifying with Backend</h3>
              <p className="text-sm text-slate-400 max-w-sm mt-1">
                Transaction confirmed on-chain! Backend service is independently verifying the receipt and logs against the contract...
              </p>
            </div>
            {confirmedResult && (
              <div className="p-3 rounded-lg bg-slate-900/80 border border-slate-800 text-xs text-slate-300 max-w-md w-full space-y-1 text-left font-mono">
                <div>On-Chain Deal ID: #{confirmedResult.chainDealId}</div>
                <div className="truncate">Tx Hash: {confirmedResult.txHash}</div>
              </div>
            )}
          </div>
        )}

        {/* STEP: Fully Verified */}
        {step === 'verified' && confirmedResult && (
          <div className="py-4 space-y-5">
            <div className="flex flex-col items-center text-center space-y-2">
              <div className="w-12 h-12 rounded-full bg-green-500/20 border border-green-500/40 flex items-center justify-center text-green-400">
                <CheckCircle2 className="w-7 h-7" />
              </div>
              <h3 className="text-xl font-bold text-white">Escrow Funded & Verified!</h3>
              <p className="text-sm text-slate-400">
                On-chain deposit confirmed and independently verified by the backend.
              </p>
            </div>

            {/* Result details card */}
            <div className="p-4 rounded-xl bg-slate-900/60 border border-slate-800 space-y-2.5 text-sm">
              <div className="flex justify-between items-center pb-2 border-b border-slate-800">
                <span className="text-slate-400">On-Chain Deal ID:</span>
                <span className="font-mono font-bold text-lg text-cyan-400">
                  #{confirmedResult.chainDealId}
                </span>
              </div>
              {verifiedDeal && (
                <div className="flex justify-between items-center pb-2 border-b border-slate-800">
                  <span className="text-slate-400">Backend Escrow State:</span>
                  <span className="px-2 py-0.5 rounded text-xs font-bold bg-green-500/20 text-green-400 border border-green-500/30">
                    {verifiedDeal.on_chain_state || 'FUNDED'}
                  </span>
                </div>
              )}
              <div className="flex justify-between items-center">
                <span className="text-slate-400">Locked Amount:</span>
                <span className="font-semibold text-white">{confirmedResult.amountEth} ETH</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-slate-400">Seller Recipient:</span>
                <span className="font-mono text-slate-300">{formatAddress(confirmedResult.seller)}</span>
              </div>
              {activeDealId && (
                <div className="pt-2 border-t border-slate-800 space-y-1">
                  <span className="text-xs text-slate-400 block">Backend Deal UUID:</span>
                  <div className="p-2 rounded bg-slate-950/80 border border-slate-800/80 flex items-center justify-between text-xs font-mono text-slate-300">
                    <span className="truncate">{activeDealId}</span>
                    <button
                      type="button"
                      onClick={() => handleCopy(activeDealId, 'dealId')}
                      className="ml-2 text-slate-400 hover:text-white shrink-0"
                    >
                      {copiedDealId ? <Check className="w-3.5 h-3.5 text-green-400" /> : <Copy className="w-3.5 h-3.5" />}
                    </button>
                  </div>
                </div>
              )}
              <div className="pt-2 border-t border-slate-800 space-y-1">
                <span className="text-xs text-slate-400 block">Transaction Hash:</span>
                <div className="p-2 rounded bg-slate-950/80 border border-slate-800/80 flex items-center justify-between text-xs font-mono text-slate-300">
                  <span className="truncate">{confirmedResult.txHash}</span>
                  <button
                    type="button"
                    onClick={() => handleCopy(confirmedResult.txHash, 'tx')}
                    className="ml-2 text-slate-400 hover:text-white shrink-0"
                  >
                    {copiedTx ? <Check className="w-3.5 h-3.5 text-green-400" /> : <Copy className="w-3.5 h-3.5" />}
                  </button>
                </div>
              </div>
            </div>

            <div className="flex justify-end gap-3 pt-2">
              <Button variant="primary" onClick={onClose} leftIcon={<ArrowRight className="w-4 h-4" />}>
                Done
              </Button>
            </div>
          </div>
        )}

        {/* STEP: Scenario A / Failure — Blockchain Succeeded, Backend Verification Failed */}
        {step === 'verification_failed' && confirmedResult && (
          <div className="py-4 space-y-5">
            <div className="flex flex-col items-center text-center space-y-2">
              <div className="w-12 h-12 rounded-full bg-amber-500/20 border border-amber-500/40 flex items-center justify-center text-amber-400">
                <AlertTriangle className="w-7 h-7" />
              </div>
              <h3 className="text-lg font-bold text-white">Blockchain Confirmed — Verification Incomplete</h3>
              <p className="text-sm text-slate-400 max-w-md">
                Your ETH was successfully deposited on-chain, but the backend verification encountered an issue.
              </p>
            </div>

            {/* Error detail banner */}
            <div className="p-3 rounded-lg bg-amber-950/40 border border-amber-500/20 text-amber-200 text-xs">
              <p className="font-semibold mb-1">Verification Response:</p>
              <p className="font-mono text-amber-300/90">{verificationError || 'Verification call failed or timed out.'}</p>
            </div>

            {/* Confirmed details preserved */}
            <div className="p-4 rounded-xl bg-slate-900/60 border border-slate-800 space-y-2 text-xs">
              <div className="flex justify-between text-slate-400">
                <span>On-Chain Deal ID:</span>
                <span className="font-mono text-cyan-400 font-bold">#{confirmedResult.chainDealId}</span>
              </div>
              <div className="flex justify-between text-slate-400">
                <span>Transaction Hash:</span>
                <span className="font-mono text-slate-300 truncate max-w-[240px]">{confirmedResult.txHash}</span>
              </div>
              <div className="flex justify-between text-slate-400">
                <span>Backend Deal ID:</span>
                <span className="font-mono text-slate-300 truncate max-w-[240px]">{activeDealId || 'Not set'}</span>
              </div>
            </div>

            <p className="text-xs text-slate-400 text-center">
              Do not send another blockchain transaction. You can retry verification with the existing transaction hash.
            </p>

            <div className="flex justify-end gap-3 pt-2">
              <Button variant="secondary" onClick={onClose}>
                Close
              </Button>
              <Button
                variant="primary"
                onClick={handleRetryVerification}
                leftIcon={<RefreshCw className="w-4 h-4" />}
              >
                Retry Backend Verification
              </Button>
            </div>
          </div>
        )}

        {/* STEP: Blockchain Error */}
        {step === 'blockchain_error' && (
          <div className="py-6 space-y-5">
            <div className="flex flex-col items-center text-center space-y-2">
              <div className="w-12 h-12 rounded-full bg-red-500/20 border border-red-500/40 flex items-center justify-center text-red-400">
                <AlertCircle className="w-7 h-7" />
              </div>
              <h3 className="text-lg font-semibold text-white">Transaction Failed</h3>
              <p className="text-sm text-red-300/90 max-w-md bg-red-950/40 border border-red-500/20 rounded-lg p-3">
                {errorMessage || 'An error occurred while attempting to fund the escrow deal.'}
              </p>
            </div>

            <div className="flex justify-end gap-3 pt-2">
              <Button variant="secondary" onClick={onClose}>
                Close
              </Button>
              <Button variant="primary" onClick={() => setStep('form')} leftIcon={<RefreshCw className="w-4 h-4" />}>
                Try Again
              </Button>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}

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
} from 'lucide-react';
import { isAddress, getAddress, parseEther } from 'ethers';
import { useWallet } from '../../context/WalletContext';
import {
  fundEscrowDeal,
  generateAgreementHash,
  formatContractError,
  type FundDealResult,
} from '../../services/contractService';
import { ESCROW_CONTRACT_ADDRESS, ESCROW_CHAIN_ID } from '../../config/contracts';
import { Modal } from '../ui/Modal';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { Spinner } from '../ui/Spinner';

export interface FundEscrowModalProps {
  open: boolean;
  onClose: () => void;
  offerId?: string;
  sellerId?: string;
  sellerAddress?: string;
  amountEth?: number | string;
  deliveryDeadline?: number; // Unix timestamp in seconds
  terms?: string;
  agreementHash?: string;
  onSuccess?: (result: FundDealResult) => void;
}

type Step = 'form' | 'awaiting_signature' | 'pending_tx' | 'confirmed' | 'error';

export function FundEscrowModal({
  open,
  onClose,
  offerId = '',
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

  // Form state
  const [sellerAddr, setSellerAddr] = useState('');
  const [amount, setAmount] = useState('');
  const [deadlineDate, setDeadlineDate] = useState('');
  const [customHash, setCustomHash] = useState('');
  const [useAutoHash, setUseAutoHash] = useState(true);

  // Flow & transaction state
  const [step, setStep] = useState<Step>('form');
  const [pendingTxHash, setPendingTxHash] = useState<string | null>(null);
  const [confirmedResult, setConfirmedResult] = useState<FundDealResult | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [copiedHash, setCopiedHash] = useState(false);
  const [copiedTx, setCopiedTx] = useState(false);

  // Initialize or reset form when modal opens
  useEffect(() => {
    if (open) {
      setStep('form');
      setPendingTxHash(null);
      setConfirmedResult(null);
      setErrorMessage(null);

      setSellerAddr(initialSellerAddress);
      setAmount(initialAmountEth ? String(initialAmountEth) : '');

      // Set deadline date (convert unix ts or default to tomorrow)
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
    }
  }, [open, initialSellerAddress, initialAmountEth, initialDeadline, initialAgreementHash]);

  // Compute active agreement hash
  const computedDeadlineTs = deadlineDate ? Math.floor(new Date(deadlineDate).getTime() / 1000) : 0;
  const effectiveHash = useAutoHash
    ? generateAgreementHash(offerId || 'generic', terms || 'standard', amount || '0', computedDeadlineTs)
    : customHash.trim();

  // Copy helper
  const handleCopy = (text: string, type: 'hash' | 'tx') => {
    navigator.clipboard.writeText(text);
    if (type === 'hash') {
      setCopiedHash(true);
      setTimeout(() => setCopiedHash(false), 2000);
    } else {
      setCopiedTx(true);
      setTimeout(() => setCopiedTx(false), 2000);
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
      setStep('awaiting_signature');

      const result = await fundEscrowDeal({
        sellerAddress: sellerAddr,
        deadlineTs: computedDeadlineTs,
        agreementHash: effectiveHash,
        amountEth: amount,
        signer,
        onTxSubmitted: (txHash: string) => {
          setPendingTxHash(txHash);
          setStep('pending_tx');
        },
      });

      setConfirmedResult(result);
      setStep('confirmed');
      if (onSuccess) {
        onSuccess(result);
      }
    } catch (err: unknown) {
      const formatted = formatContractError(err);
      setErrorMessage(formatted);
      setStep('error');
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="Fund Escrow Smart Contract" size="lg">
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
              <label className="block text-sm font-medium text-slate-300 mb-1.5">
                Seller Wallet Address (Recipient) *
              </label>
              <Input
                placeholder="0x..."
                value={sellerAddr}
                onChange={(e) => setSellerAddr(e.target.value.trim())}
                error={
                  sellerAddr && !isAddressValid
                    ? 'Invalid Ethereum address format'
                    : isSelfDeal
                    ? 'Seller address cannot match your connected buyer address'
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

        {/* STEP: Pending Transaction */}
        {step === 'pending_tx' && (
          <div className="py-8 flex flex-col items-center justify-center text-center space-y-4">
            <div className="relative">
              <Spinner size="lg" />
              <RefreshCw className="w-5 h-5 text-cyan-400 animate-spin absolute inset-0 m-auto" />
            </div>
            <div>
              <h3 className="text-lg font-semibold text-white">Transaction Submitted</h3>
              <p className="text-sm text-slate-400 max-w-sm mt-1">
                Waiting for transaction confirmation on the Anvil local blockchain...
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

        {/* STEP: Confirmed */}
        {step === 'confirmed' && confirmedResult && (
          <div className="py-4 space-y-5">
            <div className="flex flex-col items-center text-center space-y-2">
              <div className="w-12 h-12 rounded-full bg-green-500/20 border border-green-500/40 flex items-center justify-center text-green-400">
                <CheckCircle2 className="w-7 h-7" />
              </div>
              <h3 className="text-xl font-bold text-white">Escrow Funded Successfully!</h3>
              <p className="text-sm text-slate-400">
                Your funds are safely locked in the smart contract escrow.
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
              <div className="flex justify-between items-center">
                <span className="text-slate-400">Locked Amount:</span>
                <span className="font-semibold text-white">{confirmedResult.amountEth} ETH</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-slate-400">Block Number:</span>
                <span className="font-mono text-slate-300">{confirmedResult.blockNumber}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-slate-400">Seller Recipient:</span>
                <span className="font-mono text-slate-300">{formatAddress(confirmedResult.seller)}</span>
              </div>
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

        {/* STEP: Error */}
        {step === 'error' && (
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

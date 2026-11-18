import { useState, useRef, useEffect } from 'react';
import {
  Wallet,
  AlertTriangle,
  Check,
  Copy,
  ChevronDown,
  LogOut,
  Loader2,
  ExternalLink,
  X,
  Radio,
  Shield,
  ShieldCheck,
  ShieldAlert,
} from 'lucide-react';
import { useWallet } from '../../context/WalletContext';
import { useAuth } from '../../context/AuthContext';

interface WalletButtonProps {
  className?: string;
  showNetworkBadge?: boolean;
}

export function WalletButton({ className = '', showNetworkBadge = true }: WalletButtonProps) {
  const { user } = useAuth();
  const {
    account,
    chainId,
    isConnected,
    isConnecting,
    isCorrectNetwork,
    walletError,
    isMetaMaskInstalled,
    connectWallet,
    disconnectWallet,
    switchToAnvil,
    clearWalletError,
    formatAddress,
    isWalletVerified,
    isVerifyingWallet,
    walletVerificationError,
    verifyWalletOwnership,
    clearWalletVerificationError,
  } = useWallet();

  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [switchingNetwork, setSwitchingNetwork] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);


  // Close dropdown on outside click
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false);
      }
    }
    if (dropdownOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [dropdownOpen]);

  const handleCopy = async () => {
    if (!account) return;
    try {
      await navigator.clipboard.writeText(account);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback
    }
  };

  const handleSwitchNetwork = async () => {
    setSwitchingNetwork(true);
    try {
      await switchToAnvil();
    } finally {
      setSwitchingNetwork(false);
    }
  };

  // State 1: MetaMask not detected
  if (!isMetaMaskInstalled) {
    return (
      <div className={`relative inline-flex items-center ${className}`}>
        <a
          href="https://metamask.io/download/"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold text-amber-300 bg-amber-500/10 border border-amber-500/30 hover:bg-amber-500/20 transition-all"
        >
          <Wallet className="w-3.5 h-3.5" />
          Install MetaMask
          <ExternalLink className="w-3 h-3 ml-0.5 opacity-70" />
        </a>
      </div>
    );
  }

  // State 2: Disconnected
  if (!isConnected) {
    return (
      <div className={`relative inline-flex flex-col items-end ${className}`}>
        <button
          onClick={connectWallet}
          disabled={isConnecting}
          className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-xs font-semibold text-white bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed shadow-sm shadow-blue-500/20 transition-all border border-blue-400/30"
        >
          {isConnecting ? (
            <>
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              <span>Connecting...</span>
            </>
          ) : (
            <>
              <Wallet className="w-3.5 h-3.5" />
              <span>Connect Wallet</span>
            </>
          )}
        </button>

        {walletError && (
          <div className="absolute top-full mt-2 right-0 z-50 w-72 p-2.5 rounded-lg bg-red-950/90 border border-red-500/40 text-red-200 text-xs shadow-xl backdrop-blur-md flex items-start justify-between gap-2">
            <span className="flex-1 leading-relaxed">{walletError}</span>
            <button
              onClick={clearWalletError}
              className="text-red-400 hover:text-white p-0.5 rounded transition-colors"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}
      </div>
    );
  }

  // State 3: Connected (either on correct network or wrong network)
  return (
    <div className={`relative inline-flex items-center gap-2 ${className}`} ref={dropdownRef}>
      {/* Wrong Network Warning Pill & Switch Button */}
      {!isCorrectNetwork && (
        <button
          onClick={handleSwitchNetwork}
          disabled={switchingNetwork}
          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-semibold text-amber-300 bg-amber-500/15 border border-amber-500/40 hover:bg-amber-500/25 transition-all shadow-sm"
          title="Click to switch to Anvil Local (31337)"
        >
          {switchingNetwork ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin text-amber-300" />
          ) : (
            <AlertTriangle className="w-3.5 h-3.5 text-amber-400" />
          )}
          <span>Switch to Anvil</span>
        </button>
      )}

      {/* Correct Network Indicator Badge */}
      {isCorrectNetwork && showNetworkBadge && (
        <div className="hidden sm:inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium text-emerald-300 bg-emerald-500/10 border border-emerald-500/30">
          <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
          <span>Anvil</span>
        </div>
      )}

      {/* Account Address Pill (Toggles Dropdown) */}
      <button
        onClick={() => setDropdownOpen((prev) => !prev)}
        className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-mono font-medium text-slate-200 bg-slate-900/80 border border-slate-700/60 hover:bg-slate-800/80 hover:border-blue-500/40 transition-all shadow-sm"
      >
        {isWalletVerified ? (
          <span title="Verified Account Wallet">
            <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
          </span>
        ) : (
          <span className="w-2 h-2 rounded-full bg-blue-400" />
        )}

        <span>{formatAddress(account)}</span>
        {isWalletVerified && (
          <span className="text-[10px] text-emerald-400 bg-emerald-500/10 px-1 py-0.5 rounded border border-emerald-500/20 font-sans font-medium">
            Verified
          </span>
        )}
        <ChevronDown
          className={`w-3.5 h-3.5 text-slate-400 transition-transform duration-200 ${
            dropdownOpen ? 'rotate-180' : ''
          }`}
        />
      </button>


      {/* Dropdown Card */}
      {dropdownOpen && (
        <div
          className="absolute top-full right-0 mt-2 z-50 w-80 rounded-xl p-4 shadow-2xl border"
          style={{
            background: '#0d1b30',
            borderColor: 'rgba(59,130,246,0.25)',
            boxShadow: '0 20px 40px rgba(0,0,0,0.6)',
          }}
        >
          {/* Header */}
          <div className="flex items-center justify-between pb-3 mb-3 border-b border-slate-800">
            <div className="flex items-center gap-2 text-xs font-semibold text-slate-300">
              <Wallet className="w-4 h-4 text-blue-400" />
              <span>Connected Wallet</span>
            </div>
            <button
              onClick={() => setDropdownOpen(false)}
              className="text-slate-500 hover:text-slate-300 transition-colors"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* Full Address & Copy Button */}
          <div className="mb-3 p-2.5 rounded-lg bg-slate-950/70 border border-slate-800/80">
            <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">
              Account Address
            </div>
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-mono text-slate-200 truncate select-all">
                {account}
              </span>
              <button
                onClick={handleCopy}
                className="p-1 rounded text-slate-400 hover:text-blue-300 hover:bg-blue-500/10 transition-colors"
                title="Copy address"
              >
                {copied ? (
                  <Check className="w-3.5 h-3.5 text-emerald-400" />
                ) : (
                  <Copy className="w-3.5 h-3.5" />
                )}
              </button>
            </div>
          </div>

          {/* Network Row */}
          <div className="mb-4 p-2.5 rounded-lg bg-slate-950/70 border border-slate-800/80">
            <div className="flex items-center justify-between mb-1">
              <span className="text-[10px] uppercase tracking-wider text-slate-500">
                Network
              </span>
              <span className="text-[10px] font-mono text-slate-400">
                Chain ID: {chainId ?? 'Unknown'}
              </span>
            </div>

            {isCorrectNetwork ? (
              <div className="flex items-center gap-2 text-xs text-emerald-300">
                <Radio className="w-3.5 h-3.5 text-emerald-400" />
                <span className="font-medium">Anvil Local (31337)</span>
              </div>
            ) : (
              <div className="flex flex-col gap-2">
                <div className="flex items-center gap-1.5 text-xs text-amber-300">
                  <AlertTriangle className="w-3.5 h-3.5 text-amber-400 flex-shrink-0" />
                  <span>Unsupported Network ({chainId})</span>
                </div>
                <button
                  onClick={handleSwitchNetwork}
                  disabled={switchingNetwork}
                  className="w-full py-1.5 px-3 rounded-lg text-xs font-semibold text-white bg-amber-600 hover:bg-amber-500 disabled:opacity-50 transition-all flex items-center justify-center gap-1.5"
                >
                  {switchingNetwork ? (
                    <Loader2 className="w-3 h-3 animate-spin" />
                  ) : (
                    <Radio className="w-3 h-3" />
                  )}
                  <span>Switch to Anvil (31337)</span>
                </button>
              </div>
            )}
          </div>

          {/* SIWE Wallet Verification Status Box */}
          {user && (
            <div className="mb-4 p-2.5 rounded-lg bg-slate-950/70 border border-slate-800/80">
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-[10px] uppercase tracking-wider text-slate-500">
                  Account Ownership
                </span>
                <span className="text-[10px] font-mono text-slate-400">
                  EIP-4361 SIWE
                </span>
              </div>

              {isWalletVerified ? (
                <div className="flex items-center gap-2 text-xs text-emerald-300 bg-emerald-950/30 p-2 rounded-lg border border-emerald-500/20">
                  <ShieldCheck className="w-4 h-4 text-emerald-400 shrink-0" />
                  <div className="flex-1 min-w-0">
                    <p className="font-semibold text-emerald-200">Verified Account Wallet</p>
                    <p className="text-[10px] text-emerald-400/80 truncate">Cryptographically proven ownership</p>
                  </div>
                </div>
              ) : (
                <div className="flex flex-col gap-2">
                  <div className="flex items-center gap-1.5 text-xs text-amber-300">
                    <ShieldAlert className="w-4 h-4 text-amber-400 shrink-0" />
                    <span className="font-medium">Wallet Not Verified</span>
                  </div>
                  <p className="text-[11px] text-slate-400 leading-relaxed">
                    Prove ownership of this address to link it to your account profile.
                  </p>
                  <button
                    onClick={async () => {
                      await verifyWalletOwnership();
                    }}
                    disabled={isVerifyingWallet || !isCorrectNetwork}
                    className="w-full py-1.5 px-3 rounded-lg text-xs font-semibold text-white bg-blue-600 hover:bg-blue-500 disabled:opacity-50 transition-all flex items-center justify-center gap-1.5 shadow-sm shadow-blue-500/20"
                  >
                    {isVerifyingWallet ? (
                      <>
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        <span>Sign SIWE in MetaMask...</span>
                      </>
                    ) : (
                      <>
                        <Shield className="w-3.5 h-3.5" />
                        <span>Verify Wallet Ownership</span>
                      </>
                    )}
                  </button>
                  {walletVerificationError && (
                    <div className="text-[11px] text-red-300 bg-red-950/70 p-2 rounded border border-red-500/30 flex items-start justify-between gap-1">
                      <span className="flex-1">{walletVerificationError}</span>
                      <button
                        onClick={clearWalletVerificationError}
                        className="text-red-400 hover:text-white p-0.5 rounded"
                      >
                        <X className="w-3 h-3" />
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Disconnect Action */}

          <button
            onClick={() => {
              disconnectWallet();
              setDropdownOpen(false);
            }}
            className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-xs font-semibold text-red-400 hover:text-red-300 bg-red-500/10 hover:bg-red-500/20 border border-red-500/20 transition-all"
          >
            <LogOut className="w-3.5 h-3.5" />
            <span>Disconnect Wallet</span>
          </button>
        </div>
      )}

      {/* Floating error notification */}
      {walletError && (
        <div className="absolute top-full mt-2 right-0 z-50 w-72 p-2.5 rounded-lg bg-red-950/90 border border-red-500/40 text-red-200 text-xs shadow-xl backdrop-blur-md flex items-start justify-between gap-2">
          <span className="flex-1 leading-relaxed">{walletError}</span>
          <button
            onClick={clearWalletError}
            className="text-red-400 hover:text-white p-0.5 rounded transition-colors"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}
    </div>
  );
}

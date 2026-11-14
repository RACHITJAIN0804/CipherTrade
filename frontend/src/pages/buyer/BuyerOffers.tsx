import { useEffect, useState } from 'react';
import { ShieldCheck, Plus, RefreshCw, AlertCircle, Coins } from 'lucide-react';
import { listMyOffers, acceptOffer, rejectOffer } from '../../services/offerService';
import { OfferCard } from '../../components/OfferCard';
import { FundEscrowModal } from '../../components/escrow/FundEscrowModal';
import { Button } from '../../components/ui/Button';
import { Spinner } from '../../components/ui/Spinner';
import { EmptyState } from '../../components/ui/EmptyState';
import type { Offer } from '../../types';

export function BuyerOffers() {
  const [offers, setOffers] = useState<Offer[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [error, setError] = useState<string | null>(null);
  const [actionLoadingId, setActionLoadingId] = useState<string | null>(null);

  // Escrow modal state
  const [fundModalOpen, setFundModalOpen] = useState(false);
  const [selectedOffer, setSelectedOffer] = useState<Offer | null>(null);

  const fetchOffers = async () => {
    try {
      setLoading(true);
      setError(null);
      const params = statusFilter !== 'all' ? { offer_status: statusFilter } : undefined;
      const res = await listMyOffers(params);
      setOffers(res.items || []);
    } catch (err: unknown) {
      setError(
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
          'Failed to load offers.'
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchOffers();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter]);

  const handleAccept = async (offerId: string) => {
    try {
      setActionLoadingId(offerId);
      const updated = await acceptOffer(offerId);
      setOffers((prev) => prev.map((o) => (o.id === offerId ? updated : o)));
      // Open fund escrow modal immediately
      setSelectedOffer(updated);
      setFundModalOpen(true);
    } catch (err: unknown) {
      alert(
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
          'Failed to accept offer.'
      );
    } finally {
      setActionLoadingId(null);
    }
  };

  const handleReject = async (offerId: string) => {
    try {
      setActionLoadingId(offerId);
      const updated = await rejectOffer(offerId);
      setOffers((prev) => prev.map((o) => (o.id === offerId ? updated : o)));
    } catch (err: unknown) {
      alert(
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
          'Failed to reject offer.'
      );
    } finally {
      setActionLoadingId(null);
    }
  };

  const handleFund = (offer: Offer) => {
    setSelectedOffer(offer);
    setFundModalOpen(true);
  };

  const handleOpenManualFund = () => {
    setSelectedOffer(null);
    setFundModalOpen(true);
  };

  const tabs: { label: string; value: string }[] = [
    { label: 'All Offers', value: 'all' },
    { label: 'Pending', value: 'pending' },
    { label: 'Accepted', value: 'accepted' },
    { label: 'Rejected', value: 'rejected' },
  ];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight">Received Offers</h1>
          <p className="text-slate-400 text-sm mt-1">
            Review proposals from sellers and fund smart contract escrow deals.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            onClick={fetchOffers}
            leftIcon={<RefreshCw className="w-4 h-4" />}
          >
            Refresh
          </Button>
          <Button
            size="sm"
            onClick={handleOpenManualFund}
            leftIcon={<ShieldCheck className="w-4 h-4" />}
          >
            Fund New Escrow
          </Button>
        </div>
      </div>

      {/* Filter Tabs */}
      <div className="flex items-center gap-1 border-b border-slate-800 pb-2">
        {tabs.map((tab) => (
          <button
            key={tab.value}
            onClick={() => setStatusFilter(tab.value)}
            className={`px-3 py-1.5 text-sm font-medium rounded-lg transition-colors ${
              statusFilter === tab.value
                ? 'bg-blue-600 text-white'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Error state */}
      {error && (
        <div className="p-4 rounded-lg bg-red-950/40 border border-red-500/20 text-red-300 text-sm flex items-center gap-2">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Loading state */}
      {loading ? (
        <div className="flex justify-center py-16">
          <Spinner size="lg" />
        </div>
      ) : offers.length === 0 ? (
        <EmptyState
          icon={<Coins className="w-10 h-10 text-slate-500" />}
          title="No offers found"
          description={
            statusFilter === 'all'
              ? 'You have not received any seller offers yet. Post a requirement to attract sellers.'
              : `No offers with status "${statusFilter}".`
          }
          action={
            <Button
              variant="secondary"
              size="sm"
              onClick={handleOpenManualFund}
              leftIcon={<Plus className="w-4 h-4" />}
            >
              Direct Smart Contract Escrow
            </Button>
          }
        />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {offers.map((offer) => (
            <OfferCard
              key={offer.id}
              offer={offer}
              viewerRole="buyer"
              onAccept={handleAccept}
              onReject={handleReject}
              onFund={handleFund}
              loading={actionLoadingId === offer.id}
            />
          ))}
        </div>
      )}

      {/* Fund Escrow Modal */}
      <FundEscrowModal
        open={fundModalOpen}
        onClose={() => {
          setFundModalOpen(false);
          setSelectedOffer(null);
        }}
        offerId={selectedOffer?.id}
        sellerId={selectedOffer?.seller_id}
        amountEth={selectedOffer?.price_eth}
        deliveryDeadline={selectedOffer?.delivery_deadline}
        terms={selectedOffer?.terms}
        onSuccess={() => {
          fetchOffers();
        }}
      />
    </div>
  );
}

import { useEffect, useState, type FormEvent } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, Coins, Clock, Tag, DollarSign, AlertCircle } from 'lucide-react';
import { getRequirement } from '../services/requirementService';
import { createOffer, listOffersForRequirement, acceptOffer, rejectOffer } from '../services/offerService';
import { createOrGetConversation } from '../services/conversationService';
import { Card, CardTitle } from '../components/ui/Card';
import { Badge } from '../components/ui/Badge';
import { Button } from '../components/ui/Button';
import { Modal } from '../components/ui/Modal';
import { Spinner } from '../components/ui/Spinner';
import { OfferCard } from '../components/OfferCard';
import { FundEscrowModal } from '../components/escrow/FundEscrowModal';
import { useAuth } from '../context/AuthContext';
import type { Requirement, Offer } from '../types';

export function RequirementDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [req, setReq] = useState<Requirement | null>(null);
  const [offers, setOffers] = useState<Offer[]>([]);
  const [loading, setLoading] = useState(true);
  const [offerModal, setOfferModal] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [offerForm, setOfferForm] = useState({ price_eth: '', delivery_deadline: '', terms: '' });

  // Escrow funding modal state
  const [fundModalOpen, setFundModalOpen] = useState(false);
  const [selectedOfferForFunding, setSelectedOfferForFunding] = useState<Offer | null>(null);
  const [actionLoadingId, setActionLoadingId] = useState<string | null>(null);

  const isSeller = user?.primary_role === 'seller';
  const isBuyer = user?.primary_role === 'buyer';
  const isOwner = isBuyer && req?.buyer_id === user?.id;

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    getRequirement(id)
      .then(async (r) => {
        setReq(r);
        if (isBuyer && r.buyer_id === user?.id) {
          try {
            const o = await listOffersForRequirement(r.id);
            setOffers(o.items);
          } catch {}
        }
      })
      .catch(() => navigate(-1))
      .finally(() => setLoading(false));
  }, [id, isBuyer, navigate, user?.id]);

  async function handleOffer(e: FormEvent) {
    e.preventDefault();
    if (!id || !req) return;
    setSaving(true);
    setError('');
    try {
      const deadline = new Date(offerForm.delivery_deadline).getTime() / 1000;
      await createOffer({
        requirement_id: req.id,
        price_eth: parseFloat(offerForm.price_eth),
        delivery_deadline: Math.floor(deadline),
        terms: offerForm.terms,
      });
      setOfferModal(false);
      setOfferForm({ price_eth: '', delivery_deadline: '', terms: '' });
      // Refresh offers if seller or owner
      const o = await listOffersForRequirement(req.id);
      setOffers(o.items);
    } catch (err: unknown) {
      setError(
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
          'Failed to submit offer.'
      );
    } finally {
      setSaving(false);
    }
  }

  async function handleAccept(offerId: string) {
    try {
      setActionLoadingId(offerId);
      const updated = await acceptOffer(offerId);
      setOffers((prev) => prev.map((o) => (o.id === offerId ? updated : o)));
      // Open fund modal immediately for the accepted offer
      setSelectedOfferForFunding(updated);
      setFundModalOpen(true);
    } catch (err: unknown) {
      alert(
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
          'Failed to accept offer.'
      );
    } finally {
      setActionLoadingId(null);
    }
  }

  async function handleReject(offerId: string) {
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
  }

  function handleTriggerFund(offer: Offer) {
    setSelectedOfferForFunding(offer);
    setFundModalOpen(true);
  }

  async function handleMessage() {
    if (!req) return;
    try {
      const conv = await createOrGetConversation({ recipient_id: req.buyer_id });
      const prefix = user?.primary_role === 'seller' ? '/seller' : '/buyer';
      navigate(`${prefix}/conversations/${conv.id}`);
    } catch {}
  }

  if (loading)
    return (
      <div className="flex justify-center py-20">
        <Spinner size="lg" />
      </div>
    );
  if (!req) return null;

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <button
        onClick={() => navigate(-1)}
        className="flex items-center gap-2 text-slate-400 hover:text-white transition-colors text-sm"
      >
        <ArrowLeft className="w-4 h-4" />
        Back
      </button>

      <Card>
        <div className="flex items-start justify-between gap-4 mb-4">
          <CardTitle className="text-xl">{req.title}</CardTitle>
          <Badge variant={req.status === 'active' ? 'success' : 'default'}>{req.status}</Badge>
        </div>
        <p className="text-slate-300 leading-relaxed mb-5">{req.description}</p>
        <div className="flex flex-wrap gap-4 text-sm">
          <span className="flex items-center gap-1.5 text-slate-400">
            <Tag className="w-4 h-4 text-blue-400" />
            {req.category}
          </span>
          {req.keywords && (
            <span className="flex items-center gap-1.5 text-slate-400">
              <Tag className="w-3 h-3" />
              Keywords: {req.keywords}
            </span>
          )}
          {(req.budget_min != null || req.budget_max != null) && (
            <span className="flex items-center gap-1.5 text-slate-400">
              <DollarSign className="w-4 h-4 text-cyan-400" />
              {req.budget_min != null && req.budget_max != null
                ? `Budget: ${req.budget_min}–${req.budget_max} ETH`
                : req.budget_min != null
                ? `Min: ${req.budget_min} ETH`
                : `Max: ${req.budget_max} ETH`}
            </span>
          )}
          <span className="flex items-center gap-1.5 text-slate-400">
            <Clock className="w-4 h-4" />
            Posted {new Date(req.created_at).toLocaleDateString()}
          </span>
        </div>
        {isSeller && req.status === 'active' && (
          <div className="flex gap-3 mt-6">
            <Button leftIcon={<Coins className="w-4 h-4" />} onClick={() => setOfferModal(true)}>
              Submit Offer
            </Button>
            <Button variant="secondary" onClick={handleMessage}>
              Message Buyer
            </Button>
          </div>
        )}
      </Card>

      {isOwner && offers.length > 0 && (
        <div>
          <h2 className="text-lg font-semibold text-white mb-3">Offers Received ({offers.length})</h2>
          <div className="grid md:grid-cols-2 gap-4">
            {offers.map((o) => (
              <OfferCard
                key={o.id}
                offer={o}
                viewerRole="buyer"
                onAccept={handleAccept}
                onReject={handleReject}
                onFund={handleTriggerFund}
                loading={actionLoadingId === o.id}
              />
            ))}
          </div>
        </div>
      )}

      {/* Submit Offer Modal */}
      <Modal open={offerModal} onClose={() => setOfferModal(false)} title="Submit an Offer" size="md">
        <form onSubmit={handleOffer} className="space-y-4">
          {error && (
            <div className="flex items-start gap-2 text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded px-3 py-2">
              <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
              {error}
            </div>
          )}
          <div>
            <label className="block text-sm font-medium text-slate-300 mb-1.5">Price (ETH) *</label>
            <input
              type="number"
              value={offerForm.price_eth}
              onChange={(e) => setOfferForm((f) => ({ ...f, price_eth: e.target.value }))}
              required
              min="0.001"
              step="0.001"
              placeholder="0.1"
              className="w-full px-3 py-2.5 rounded-lg text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-2 transition-all"
              style={{ background: 'rgba(15,32,68,0.8)', border: '1px solid rgba(59,130,246,0.2)' }}
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-300 mb-1.5">Delivery Deadline *</label>
            <input
              type="date"
              value={offerForm.delivery_deadline}
              onChange={(e) => setOfferForm((f) => ({ ...f, delivery_deadline: e.target.value }))}
              required
              min={new Date(Date.now() + 86400000).toISOString().split('T')[0]}
              className="w-full px-3 py-2.5 rounded-lg text-slate-100 focus:outline-none focus:ring-2 transition-all"
              style={{
                background: 'rgba(15,32,68,0.8)',
                border: '1px solid rgba(59,130,246,0.2)',
                colorScheme: 'dark',
              }}
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-300 mb-1.5">Terms & Proposal *</label>
            <textarea
              value={offerForm.terms}
              onChange={(e) => setOfferForm((f) => ({ ...f, terms: e.target.value }))}
              required
              minLength={10}
              rows={4}
              placeholder="Describe what you'll deliver, your approach, and any conditions…"
              className="w-full px-3 py-2.5 rounded-lg text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-2 resize-y transition-all"
              style={{ background: 'rgba(15,32,68,0.8)', border: '1px solid rgba(59,130,246,0.2)' }}
            />
          </div>
          <div className="flex justify-end gap-3 pt-2">
            <Button variant="secondary" type="button" onClick={() => setOfferModal(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={saving}>
              Submit Offer
            </Button>
          </div>
        </form>
      </Modal>

      {/* Escrow Smart Contract Funding Modal */}
      {selectedOfferForFunding && (
        <FundEscrowModal
          open={fundModalOpen}
          onClose={() => {
            setFundModalOpen(false);
            setSelectedOfferForFunding(null);
          }}
          offerId={selectedOfferForFunding.id}
          sellerId={selectedOfferForFunding.seller_id}
          amountEth={selectedOfferForFunding.price_eth}
          deliveryDeadline={selectedOfferForFunding.delivery_deadline}
          terms={selectedOfferForFunding.terms}
          onSuccess={(result) => {
            console.log('Escrow successfully funded on-chain:', result);
          }}
        />
      )}
    </div>
  );
}

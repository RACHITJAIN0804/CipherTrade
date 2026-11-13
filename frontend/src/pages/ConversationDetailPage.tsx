import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, Send, Lock, AlertTriangle, Flag, Ban } from 'lucide-react';
import { getConversation } from '../services/conversationService';
import { Card } from '../components/ui/Card';
import { Button } from '../components/ui/Button';
import { Spinner } from '../components/ui/Spinner';
import { useAuth } from '../context/AuthContext';
import type { Conversation } from '../types';

// NOTE: Message storage is NOT implemented yet.
// Real-time encrypted messaging is planned for Phase 5 (E2EE).
// This UI is a placeholder for the future messaging interface.

interface LocalMessage { id: string; text: string; fromMe: boolean; timestamp: Date; }

export function ConversationDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [conv, setConv] = useState<Conversation | null>(null);
  const [loading, setLoading] = useState(true);
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [input, setInput] = useState('');
  const [reportOpen, setReportOpen] = useState(false);

  useEffect(() => {
    if (!id) return;
    getConversation(id).then(setConv).catch(() => navigate(-1)).finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  function sendMessage() {
    if (!input.trim()) return;
    setMessages(m => [...m, { id: Date.now().toString(), text: input.trim(), fromMe: true, timestamp: new Date() }]);
    setInput('');
  }

  function handleKey(e: React.KeyboardEvent) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } }

  if (loading) return <div className="flex justify-center py-20"><Spinner size="lg" /></div>;
  if (!conv) return null;

  const otherId = conv.initiator_id === user?.id ? conv.recipient_id : conv.initiator_id;

  return (
    <div className="max-w-3xl mx-auto flex flex-col h-[calc(100vh-7rem)]">
      {/* Header */}
      <div className="flex items-center gap-3 mb-4">
        <button onClick={() => navigate(-1)} className="text-slate-400 hover:text-white transition-colors"><ArrowLeft className="w-5 h-5" /></button>
        <div className="flex-1">
          <h1 className="text-lg font-semibold text-white">Conversation</h1>
          <p className="text-xs text-slate-500">With user {otherId.slice(0, 12)}…</p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => setReportOpen(r => !r)} className="p-2 rounded-lg text-slate-500 hover:text-yellow-400 hover:bg-yellow-500/10 transition-all" title="Report user">
            <Flag className="w-4 h-4" />
          </button>
          <button className="p-2 rounded-lg text-slate-500 hover:text-red-400 hover:bg-red-500/10 transition-all" title="Block user">
            <Ban className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* E2EE Notice */}
      <div className="flex items-start gap-2.5 rounded-lg px-3 py-2.5 mb-3 text-xs" style={{ background: 'rgba(234,179,8,0.08)', border: '1px solid rgba(234,179,8,0.2)', color: '#fbbf24' }}>
        <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
        <span><strong>Notice:</strong> End-to-end encrypted messaging is not yet implemented. Messages shown here are stored locally in this browser session only and are not transmitted or persisted. Full E2EE messaging will be implemented in Phase 5.</span>
      </div>

      {/* Messages */}
      <Card className="flex-1 overflow-y-auto mb-3 p-4 space-y-3" style={{ minHeight: 0 }}>
        {messages.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-center">
            <Lock className="w-10 h-10 text-slate-700 mb-3" />
            <p className="text-sm text-slate-500">No messages yet.</p>
            <p className="text-xs text-slate-600 mt-1 max-w-xs">Messages are stored locally only. E2EE messaging is coming in Phase 5.</p>
          </div>
        ) : (
          messages.map(m => (
            <div key={m.id} className={`flex ${m.fromMe ? 'justify-end' : 'justify-start'}`}>
              <div className={`max-w-[70%] px-3 py-2 rounded-xl text-sm ${m.fromMe ? 'text-white rounded-br-sm' : 'text-slate-200 rounded-bl-sm'}`}
                style={{ background: m.fromMe ? '#2563eb' : 'rgba(15,32,68,0.9)', border: m.fromMe ? 'none' : '1px solid rgba(59,130,246,0.2)' }}>
                <p>{m.text}</p>
                <p className="text-xs opacity-50 mt-0.5 text-right">{m.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</p>
              </div>
            </div>
          ))
        )}
      </Card>

      {/* Input */}
      <div className="flex gap-2">
        <div className="flex-1 relative">
          <textarea value={input} onChange={e => setInput(e.target.value)} onKeyDown={handleKey} placeholder="Type a message… (Enter to send)"
            rows={1} className="w-full px-3 py-2.5 rounded-lg text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-2 resize-none transition-all"
            style={{ background: 'rgba(15,32,68,0.6)', border: '1px solid rgba(59,130,246,0.2)' }} />
        </div>
        <Button onClick={sendMessage} disabled={!input.trim()} leftIcon={<Send className="w-4 h-4" />} className="self-end">Send</Button>
      </div>

      {/* Report Panel */}
      {reportOpen && (
        <div className="mt-3 rounded-xl border p-4" style={{ background: 'rgba(15,32,68,0.6)', borderColor: 'rgba(234,179,8,0.2)' }}>
          <h3 className="text-sm font-semibold text-yellow-400 mb-2">Report User</h3>
          <p className="text-xs text-slate-400 mb-3">Moderation reporting UI — backend integration via POST /api/v1/moderation/reports is ready. UI form coming in next iteration.</p>
          <Button size="sm" variant="secondary" onClick={() => setReportOpen(false)}>Close</Button>
        </div>
      )}
    </div>
  );
}

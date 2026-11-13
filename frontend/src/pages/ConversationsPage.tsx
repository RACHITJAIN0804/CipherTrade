import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { MessageSquare, Clock } from 'lucide-react';
import { listConversations } from '../services/conversationService';
import { Card } from '../components/ui/Card';
import { Spinner } from '../components/ui/Spinner';
import { EmptyState } from '../components/ui/EmptyState';
import { useAuth } from '../context/AuthContext';
import type { Conversation } from '../types';

export function ConversationsPage() {
  const { user } = useAuth();
  const [convs, setConvs] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);
  const prefix = user?.primary_role === 'seller' ? '/seller' : '/buyer';

  useEffect(() => {
    listConversations().then(setConvs).finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="flex justify-center py-20"><Spinner size="lg" /></div>;

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div><h1 className="text-2xl font-bold text-white">Messages</h1><p className="text-slate-400 text-sm mt-0.5">Private conversations with buyers and sellers.</p></div>
      {convs.length === 0 ? (
        <EmptyState icon={<MessageSquare className="w-10 h-10" />} title="No conversations yet" description="Start a conversation from a requirement listing or offer." />
      ) : (
        <div className="space-y-2">
          {convs.map(c => {
            const otherId = c.initiator_id === user?.id ? c.recipient_id : c.initiator_id;
            return (
              <Link key={c.id} to={`${prefix}/conversations/${c.id}`}>
                <Card hover className="flex items-center gap-4">
                  <div className="w-10 h-10 rounded-full bg-blue-800 flex items-center justify-center text-blue-200 text-sm font-bold flex-shrink-0">
                    <MessageSquare className="w-5 h-5" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-slate-200 truncate">Conversation</p>
                    <p className="text-xs text-slate-500 truncate">ID: {otherId.slice(0, 8)}…</p>
                  </div>
                  <div className="flex items-center gap-1 text-xs text-slate-600 flex-shrink-0">
                    <Clock className="w-3 h-3" />{new Date(c.created_at).toLocaleDateString()}
                  </div>
                </Card>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}

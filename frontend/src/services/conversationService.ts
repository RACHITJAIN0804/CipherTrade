import { apiClient } from './api';
import type { Conversation } from '../types';

export async function createOrGetConversation(data: { recipient_id: string; offer_id?: string }): Promise<Conversation> {
  const res = await apiClient.post<Conversation>('/conversations', data);
  return res.data;
}
export async function listConversations(): Promise<Conversation[]> {
  const res = await apiClient.get<Conversation[]>('/conversations');
  return res.data;
}
export async function getConversation(id: string): Promise<Conversation> {
  const res = await apiClient.get<Conversation>(`/conversations/${id}`);
  return res.data;
}

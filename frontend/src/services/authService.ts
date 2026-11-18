import { apiClient, setToken, clearToken } from './api';
import type { LoginRequest, TokenResponse, User, UserCreate } from '../types';

export async function login(data: LoginRequest): Promise<TokenResponse> {
  const res = await apiClient.post<TokenResponse>('/auth/login', data);
  setToken(res.data.access_token);
  return res.data;
}

export async function register(data: UserCreate): Promise<User> {
  const res = await apiClient.post<User>('/auth/register', data);
  return res.data;
}

export async function getMe(): Promise<User> {
  const res = await apiClient.get<User>('/users/me');
  return res.data;
}

export function logout() { clearToken(); }

export { requestWalletChallenge, verifyWalletSignature } from './walletVerificationService';

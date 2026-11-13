import axios, { AxiosError } from 'axios';

const TOKEN_KEY = 'ct_token';

export const apiClient = axios.create({
  baseURL: (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000') + '/api/v1',
  headers: { 'Content-Type': 'application/json' },
});

apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

apiClient.interceptors.response.use(
  (res) => res,
  (err: AxiosError) => {
    if (err.response?.status === 401) {
      localStorage.removeItem(TOKEN_KEY);
      window.location.href = '/login';
    }
    return Promise.reject(err);
  }
);

export function setToken(token: string) { localStorage.setItem(TOKEN_KEY, token); }
export function clearToken() { localStorage.removeItem(TOKEN_KEY); }
export function getToken() { return localStorage.getItem(TOKEN_KEY); }

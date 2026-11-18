import React, { createContext, useContext, useReducer, useEffect, useCallback } from 'react';
import type { User, UserCreate } from '../types';
import { login as apiLogin, register as apiRegister, logout as apiLogout, getMe } from '../services/authService';
import { getToken } from '../services/api';

interface AuthState { user: User | null; loading: boolean; error: string | null; }
type AuthAction =
  | { type: 'SET_LOADING'; payload: boolean }
  | { type: 'SET_USER'; payload: User | null }
  | { type: 'SET_ERROR'; payload: string | null };

function reducer(state: AuthState, action: AuthAction): AuthState {
  switch (action.type) {
    case 'SET_LOADING': return { ...state, loading: action.payload };
    case 'SET_USER': return { ...state, user: action.payload, loading: false, error: null };
    case 'SET_ERROR': return { ...state, error: action.payload, loading: false };
    default: return state;
  }
}

interface AuthContextValue extends AuthState {
  login: (email: string, password: string) => Promise<void>;
  register: (data: UserCreate) => Promise<void>;
  logout: () => void;
  refreshUser: () => Promise<User | null>;
  setUser: (user: User | null) => void;
  isAuthenticated: boolean;
}


const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, dispatch] = useReducer(reducer, { user: null, loading: true, error: null });

  useEffect(() => {
    const token = getToken();
    if (token) {
      getMe()
        .then((user) => dispatch({ type: 'SET_USER', payload: user }))
        .catch(() => { dispatch({ type: 'SET_USER', payload: null }); });
    } else {
      dispatch({ type: 'SET_LOADING', payload: false });
    }
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    dispatch({ type: 'SET_LOADING', payload: true });
    dispatch({ type: 'SET_ERROR', payload: null });
    try {
      await apiLogin({ email, password });
      const user = await getMe();
      dispatch({ type: 'SET_USER', payload: user });
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Login failed.';
      dispatch({ type: 'SET_ERROR', payload: msg });
      throw err;
    }
  }, []);

  const register = useCallback(async (data: UserCreate) => {
    dispatch({ type: 'SET_LOADING', payload: true });
    dispatch({ type: 'SET_ERROR', payload: null });
    try {
      await apiRegister(data);
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Registration failed.';
      dispatch({ type: 'SET_ERROR', payload: msg });
      throw err;
    } finally {
      dispatch({ type: 'SET_LOADING', payload: false });
    }
  }, []);

  const logout = useCallback(() => {
    apiLogout();
    dispatch({ type: 'SET_USER', payload: null });
  }, []);

  const refreshUser = useCallback(async (): Promise<User | null> => {
    try {
      const user = await getMe();
      dispatch({ type: 'SET_USER', payload: user });
      return user;
    } catch {
      return null;
    }
  }, []);

  const setUser = useCallback((user: User | null) => {
    dispatch({ type: 'SET_USER', payload: user });
  }, []);

  return (
    <AuthContext.Provider
      value={{
        ...state,
        login,
        register,
        logout,
        refreshUser,
        setUser,
        isAuthenticated: !!state.user,
      }}
    >
      {children}
    </AuthContext.Provider>
  );

}

// Re-exported from hooks/useAuth for colocation convenience
export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
  return ctx;
}

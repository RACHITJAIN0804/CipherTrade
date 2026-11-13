import { createBrowserRouter } from 'react-router-dom';
import { ProtectedRoute } from './ProtectedRoute';
import { LandingPage } from '../pages/LandingPage';
import { LoginPage } from '../pages/LoginPage';
import { RegisterPage } from '../pages/RegisterPage';
import { NotFoundPage } from '../pages/NotFoundPage';
import { BuyerLayout } from '../layouts/BuyerLayout';
import { SellerLayout } from '../layouts/SellerLayout';
import { BuyerDashboard } from '../pages/buyer/BuyerDashboard';
import { BuyerRequirements } from '../pages/buyer/BuyerRequirements';
import { BuyerOffers } from '../pages/buyer/BuyerOffers';
import { BuyerTransactions } from '../pages/buyer/BuyerTransactions';
import { MarketplacePage } from '../pages/MarketplacePage';
import { RequirementDetailPage } from '../pages/RequirementDetailPage';
import { SellerDashboard } from '../pages/seller/SellerDashboard';
import { SellerOffers } from '../pages/seller/SellerOffers';
import { SellerTransactions } from '../pages/seller/SellerTransactions';
import { ConversationsPage } from '../pages/ConversationsPage';
import { ConversationDetailPage } from '../pages/ConversationDetailPage';

export const router = createBrowserRouter([
  { path: '/', element: <LandingPage /> },
  { path: '/login', element: <LoginPage /> },
  { path: '/register', element: <RegisterPage /> },
  { path: '*', element: <NotFoundPage /> },
  {
    element: <ProtectedRoute requiredRole="buyer" />,
    children: [
      {
        element: <BuyerLayout />,
        children: [
          { path: '/buyer', element: <BuyerDashboard /> },
          { path: '/buyer/requirements', element: <BuyerRequirements /> },
          { path: '/buyer/offers', element: <BuyerOffers /> },
          { path: '/buyer/transactions', element: <BuyerTransactions /> },
          { path: '/buyer/marketplace', element: <MarketplacePage /> },
          { path: '/buyer/marketplace/:id', element: <RequirementDetailPage /> },
          { path: '/buyer/conversations', element: <ConversationsPage /> },
          { path: '/buyer/conversations/:id', element: <ConversationDetailPage /> },
        ],
      },
    ],
  },
  {
    element: <ProtectedRoute requiredRole="seller" />,
    children: [
      {
        element: <SellerLayout />,
        children: [
          { path: '/seller', element: <SellerDashboard /> },
          { path: '/seller/marketplace', element: <MarketplacePage /> },
          { path: '/seller/marketplace/:id', element: <RequirementDetailPage /> },
          { path: '/seller/offers', element: <SellerOffers /> },
          { path: '/seller/transactions', element: <SellerTransactions /> },
          { path: '/seller/conversations', element: <ConversationsPage /> },
          { path: '/seller/conversations/:id', element: <ConversationDetailPage /> },
        ],
      },
    ],
  },
]);

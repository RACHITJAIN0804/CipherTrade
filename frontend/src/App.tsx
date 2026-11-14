import { RouterProvider } from 'react-router-dom';
import { AuthProvider } from './context/AuthContext';
import { WalletProvider } from './context/WalletContext';
import { router } from './routes';

export default function App() {
  return (
    <AuthProvider>
      <WalletProvider>
        <RouterProvider router={router} />
      </WalletProvider>
    </AuthProvider>
  );
}

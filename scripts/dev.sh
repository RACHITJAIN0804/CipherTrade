#!/usr/bin/env bash
# scripts/dev.sh — starts all local services for development
# Usage: bash scripts/dev.sh
set -euo pipefail

echo "Starting CipherTrade local development stack..."
echo ""
echo "  1. Start Anvil (local blockchain):"
echo "     anvil --chain-id 31337"
echo ""
echo "  2. In a new terminal, start the backend:"
echo "     cd backend && .venv/Scripts/activate && uvicorn asgi:app --reload --port 8000"
echo ""
echo "  3. In a new terminal, start the frontend:"
echo "     cd frontend && npm run dev"
echo ""
echo "  Frontend: http://localhost:5173"
echo "  API:      http://localhost:8000"
echo "  API docs: http://localhost:8000/docs"

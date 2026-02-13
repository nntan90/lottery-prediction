#!/bin/bash
# Helper script to run import with environment variables

# Check if .env exists
if [ ! -f .env ]; then
    echo "❌ .env file not found!"
    echo ""
    echo "Please create .env file with your Supabase credentials:"
    echo "  cp .env.example .env"
    echo "  # Then edit .env with your actual credentials"
    exit 1
fi

# Load .env and run import
echo "�� Loading credentials from .env..."
export $(cat .env | grep -v '^#' | xargs)

echo "🚀 Running import..."
python3 import_historical_data.py

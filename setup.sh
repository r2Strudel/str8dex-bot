#!/usr/bin/env bash
set -e

echo "Setting up Pokemon Price Bot..."

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

echo ""
echo "Setup complete!"
echo "To start the bot, run:"
echo "  source venv/bin/activate && python bot.py"

#!/bin/bash
# One-click setup for memoQ Tag Transfer
# 一键安装脚本

set -e

echo "🔧 Setting up memoQ Tag Transfer..."
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found. Please install Python 3.9+ first."
    echo "   https://www.python.org/downloads/"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "✅ Python $PYTHON_VERSION found"

# Install dependencies
echo ""
echo "📦 Installing dependencies..."
pip3 install -e . --quiet
echo "✅ Dependencies installed"

# Setup .env
if [ ! -f .env ]; then
    cp .env.example .env
    echo ""
    echo "⚠️  Created .env file. You need to add your API key:"
    echo ""
    echo "   1. Get a DeepSeek API key at: https://platform.deepseek.com/"
    echo "   2. Open .env and replace 'sk-your-key-here' with your key"
    echo ""
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "   Run:  open .env"
    else
        echo "   Run:  nano .env"
    fi
else
    echo "✅ .env already exists"
fi

echo ""
echo "🎉 Setup complete!"
echo ""
echo "Quick start:"
echo "  memoq-tag-transfer analyze your_file.mqxlz        # preview tags"
echo "  memoq-tag-transfer transfer your_file.mqxlz       # transfer & generate TMX"

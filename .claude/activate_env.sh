#!/bin/zsh
# Source this file to activate hummingbot conda environment

# Initialize conda
if [ -f "/opt/homebrew/Caskroom/miniconda/base/etc/profile.d/conda.sh" ]; then
    source "/opt/homebrew/Caskroom/miniconda/base/etc/profile.d/conda.sh"
    conda activate hummingbot
    echo "✓ Activated hummingbot conda environment"
    echo "Python: $(which python)"
    echo "Python version: $(python --version)"
else
    echo "Error: Could not find conda installation"
    exit 1
fi

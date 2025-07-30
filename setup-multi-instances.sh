#!/bin/bash

# Setup script for multiple Hummingbot instances
# This script creates separate directories for each bot instance

echo "Setting up multiple Hummingbot instances..."

# Create instance directories
for i in {1..2}; do
    echo "Creating instance$i directories..."

    # Create main directories
    mkdir -p instance$i/{conf,logs,data}
    mkdir -p instance$i/conf/{connectors,strategies,controllers,scripts}

    # Copy only non-encrypted files and structure
    if [ -d "conf" ]; then
        echo "Setting up configuration structure for instance$i..."

        # Copy only Python init files and non-sensitive configs
        find conf -name "__init__.py" -o -name "*.py" | while read file; do
            dest_file="instance$i/$file"
            mkdir -p "$(dirname "$dest_file")"
            cp "$file" "$dest_file" 2>/dev/null || true
        done

        # Copy .gitignore files to maintain structure
        find conf -name ".gitignore" | while read file; do
            dest_file="instance$i/$file"
            mkdir -p "$(dirname "$dest_file")"
            cp "$file" "$dest_file" 2>/dev/null || true
        done

        # Do NOT copy .yml files from connectors as they may be encrypted
        # Users will need to configure connectors separately for each instance

        echo "Instance$i setup complete!"
        echo "Note: You'll need to configure exchange connectors separately for this instance."
    else
        echo "Warning: No existing conf directory found. Creating empty structure."
    fi

    # Set permissions
    chmod -R 755 instance$i/
done

echo ""
echo "Multi-instance setup complete!"
echo ""
echo "IMPORTANT: Each instance needs separate configuration:"
echo "1. Start each instance and create a new password"
echo "2. Configure exchange API keys separately for each instance"
echo "3. Do NOT copy encrypted .yml files between instances"
echo ""
echo "Next steps:"
echo "1. Build: ./manage-instances.sh build"
echo "2. Start: ./manage-instances.sh start"
echo "3. Attach: ./manage-instances.sh attach 1 (or 2)"

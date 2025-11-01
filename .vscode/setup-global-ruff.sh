#!/bin/bash
# Setup Ruff as default for all Python projects on this remote machine

set -e

REMOTE_SETTINGS_DIR="${HOME}/.vscode-server/data/Machine"
REMOTE_SETTINGS_FILE="${REMOTE_SETTINGS_DIR}/settings.json"

echo "================================================"
echo "Setting up Ruff as default for all Python projects"
echo "================================================"
echo ""

# Create directory if it doesn't exist
mkdir -p "${REMOTE_SETTINGS_DIR}"

# Backup existing settings if they exist
if [ -f "${REMOTE_SETTINGS_FILE}" ]; then
    BACKUP_FILE="${REMOTE_SETTINGS_FILE}.backup.$(date +%Y%m%d_%H%M%S)"
    echo "📦 Backing up existing settings to:"
    echo "   ${BACKUP_FILE}"
    cp "${REMOTE_SETTINGS_FILE}" "${BACKUP_FILE}"
    echo ""
fi

# Create the new settings file
echo "✍️  Writing new global settings..."
cat > "${REMOTE_SETTINGS_FILE}" << 'EOF'
{
  // Python interpreter
  "python.defaultInterpreterPath": "${workspaceFolder}/.venv/bin/python",

  // Disable old linters - use Ruff instead
  "python.linting.enabled": false,
  "python.linting.pylintEnabled": false,
  "python.linting.flake8Enabled": false,
  "python.linting.pycodestyleEnabled": false,
  "python.linting.mypyEnabled": false,

  // Disable old formatters - use Ruff instead
  "python.formatting.provider": "none",

  // Use Ruff for all Python files
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff",
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
      "source.organizeImports": "explicit",
      "source.fixAll": "explicit"
    }
  },

  // Ruff configuration (using new native server settings)
  "ruff.enable": true,
  "ruff.lineLength": 120,
  "ruff.organizeImports": true,
  "ruff.fixAll": true,

  // Editor settings
  "editor.rulers": [120],
  "editor.formatOnSave": true,
  "editor.formatOnType": false,
  "editor.formatOnPaste": true,

  // File settings
  "files.trimTrailingWhitespace": true,
  "files.insertFinalNewline": true,
  "files.trimFinalNewlines": true,

  // Python analysis
  "python.analysis.autoImportCompletions": true,
  "python.analysis.typeCheckingMode": "basic"
}
EOF

echo "✅ Global settings updated successfully!"
echo ""
echo "📍 Settings saved to:"
echo "   ${REMOTE_SETTINGS_FILE}"
echo ""
echo "================================================"
echo "Next Steps:"
echo "================================================"
echo ""
echo "1. IMPORTANT - Check for old extension:"
echo "   - Press Ctrl+Shift+P"
echo "   - Type: 'Extensions: Show Installed Extensions'"
echo "   - If you see 'Ruff LSP' or any old ruff-lsp extension:"
echo "     → UNINSTALL it (it's deprecated)"
echo ""
echo "2. Install the official Ruff extension:"
echo "   - Press Ctrl+Shift+P"
echo "   - Type: 'Extensions: Install Extensions'"
echo "   - Search for: 'Ruff'"
echo "   - Install: 'Ruff' by Astral Software"
echo "   - Extension ID: charliermarsh.ruff"
echo "   - Ensure version 2024.32.0 or later"
echo ""
echo "3. Reload VSCode window:"
echo "   - Press Ctrl+Shift+P"
echo "   - Type: 'Developer: Reload Window'"
echo ""
echo "4. Verify it's working:"
echo "   - Open any Python file"
echo "   - Bottom right should show 'Ruff' as formatter"
echo "   - Save file - should auto-format"
echo "   - No warnings about deprecated settings should appear"
echo ""
echo "✨ All future Python projects will use Ruff by default!"
echo ""

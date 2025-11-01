# VSCode Configuration for AEMS

## Setup Instructions

### 1. Install the Ruff Extension

This project uses **Ruff** for both linting and formatting (replacing Pylint, Flake8, Black, and isort).

**Install the Ruff extension:**
1. Open VSCode
2. Press `Ctrl+Shift+X` (or `Cmd+Shift+X` on Mac) to open Extensions
3. Search for "Ruff"
4. Install **"Ruff" by Astral Software** (`charliermarsh.ruff`)

Alternatively, VSCode should prompt you to install recommended extensions when you open this workspace.

### 2. Reload VSCode

After installing the Ruff extension:
1. Press `Ctrl+Shift+P` (or `Cmd+Shift+P` on Mac)
2. Type "Reload Window" and select it
3. Or just close and reopen VSCode

### 3. Verify Configuration

You should now see:
- ✅ Ruff linting errors/warnings in your code
- ✅ Automatic formatting on save using Ruff (120 character line length)
- ✅ Automatic import organization
- ❌ No more Pylint false positives!

### 4. Manual Commands

You can also run Ruff commands manually:

**Format current file:**
- `Ctrl+Shift+P` → "Format Document"
- Or use `Shift+Alt+F`

**Fix all auto-fixable issues:**
- `Ctrl+Shift+P` → "Ruff: Fix all auto-fixable problems"

**Organize imports:**
- `Ctrl+Shift+P` → "Ruff: Organize Imports"

## Configuration Files

This project's Ruff configuration is in:
- **`ruff.toml`** - Primary configuration (line-length: 120)
- **`pyproject.toml`** - Backup configuration (line-length: 120)

VSCode will automatically use these configuration files.

## What Changed

### Before (Old Setup)
- ❌ Pylint (showing false positives like E1120)
- ❌ Flake8 (separate linter)
- ❌ Black (separate formatter)
- ❌ isort (separate import organizer)

### After (New Setup)
- ✅ **Ruff only** - does everything!
  - Linting (replaces Pylint + Flake8)
  - Formatting (replaces Black)
  - Import organizing (replaces isort)
  - Faster and more accurate

## Troubleshooting

### Ruff not working?
1. Make sure the extension is installed: `charliermarsh.ruff`
2. Reload VSCode window
3. Check the Output panel: View → Output → Select "Ruff"

### Still seeing Pylint errors?
1. Check if Pylint is disabled in settings: `"python.linting.pylintEnabled": false`
2. Uninstall the Pylint extension if you have it installed separately
3. Reload VSCode

### Want to run Ruff manually?
```bash
# Format code
make format
# or: .venv/bin/ruff format src/ tests/

# Check linting
make lint
# or: .venv/bin/ruff check src/ tests/

# Fix auto-fixable issues
make fix
# or: .venv/bin/ruff check --fix src/ tests/
```

## Learn More

- [Ruff Documentation](https://docs.astral.sh/ruff/)
- [Ruff VSCode Extension](https://marketplace.visualstudio.com/items?itemName=charliermarsh.ruff)
- Project's [CLAUDE.md](../CLAUDE.md) for development commands

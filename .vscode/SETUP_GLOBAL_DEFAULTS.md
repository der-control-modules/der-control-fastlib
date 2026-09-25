# Setting Up Ruff as Default for All Python Projects

Since you're on a remote system, you can configure VSCode to use Ruff by default for all Python projects on this machine.

## Method 1: Through VSCode UI (Recommended)

1. **Open User Settings (Remote)**
   - Press `Ctrl+Shift+P` (Command Palette)
   - Type: "Preferences: Open Remote Settings (SSH: hostname)"
   - Or: File -> Preferences -> Settings -> Make sure you're on the "Remote" tab

2. **Search and Configure**
   - Search for: `python.linting.pylintEnabled`
   - Uncheck it (set to `false`)

   - Search for: `python.linting.enabled`
   - Uncheck it (set to `false`)

   - Search for: `python.formatting.provider`
   - Set to: `none`

   - Search for: `python default formatter`
   - Set Default Formatter to: `Ruff`

3. **Install Ruff Extension**
   - Extensions -> Search "Ruff" -> Install
   - This installs on the remote server

## Method 2: Edit Settings JSON Directly (Faster)

1. **Open Remote User Settings JSON**
   - Press `Ctrl+Shift+P`
   - Type: "Preferences: Open Remote Settings (JSON)"

2. **Add these settings:**

```json
{
  // Disable old Python linters
  "python.linting.enabled": false,
  "python.linting.pylintEnabled": false,
  "python.linting.flake8Enabled": false,
  "python.linting.pycodestyleEnabled": false,

  // Disable old formatters
  "python.formatting.provider": "none",

  // Use Ruff for all Python projects
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff",
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
      "source.organizeImports": "explicit",
      "source.fixAll": "explicit"
    }
  },

  // Ruff configuration
  "ruff.enable": true,
  "ruff.lint.enable": true,
  "ruff.organizeImports": true,
  "ruff.fixAll": true,

  // Editor defaults for Python
  "editor.rulers": [120],
  "files.trimTrailingWhitespace": true,
  "files.insertFinalNewline": true
}
```

3. **Save and Reload**
   - Save the file
   - Press `Ctrl+Shift+P` -> "Developer: Reload Window"

## Method 3: Create a Python Profile (VSCode Profiles)

VSCode Profiles let you save and reuse settings across projects.

1. **Create a Profile**
   - Press `Ctrl+Shift+P`
   - Type: "Profiles: Create Profile"
   - Name it: "Python with Ruff"
   - Copy from: "Default"

2. **Configure the Profile**
   - While in the new profile, apply all the Ruff settings above
   - Install the Ruff extension

3. **Use the Profile**
   - Click the gear icon in bottom left
   - Select: "Python with Ruff"
   - This profile is now active and can be used in any workspace

## Where Settings Are Stored

**Remote Settings Location:**
```
~/.vscode-server/data/Machine/settings.json
```

**Workspace Settings (Project-specific):**
```
/path/to/project/.vscode/settings.json
```

**Priority Order:**
1. Workspace settings (`.vscode/settings.json`) - Highest priority
2. Remote User settings (`~/.vscode-server/data/Machine/settings.json`)
3. Local User settings (on your local machine)

## Verify It's Working

After setting up, open any Python file and check:

1. **Bottom right corner** should show:
   - Formatter: "Ruff"

2. **Open Command Palette** (`Ctrl+Shift+P`):
   - Type "Format Document"
   - Should say "Format Document with Ruff"

3. **Linting should show Ruff errors**, not Pylint

## Copy Current Project Settings to Global

If you want to use THIS project's exact settings globally:

```bash
# View current project settings
cat /home/volttron/aems-lib-fastapi/.vscode/settings.json

# Copy to remote user settings
cp /home/volttron/aems-lib-fastapi/.vscode/settings.json ~/.vscode-server/data/Machine/settings.json

# Reload VSCode
# Ctrl+Shift+P -> "Developer: Reload Window"
```

**Warning:** This will overwrite your existing global settings!

## Recommended Approach

**For a remote development machine:**
1. Use **Method 2** (Edit Remote Settings JSON)
2. Add the Ruff configuration to Remote User Settings
3. Install Ruff extension once
4. All future Python projects will use Ruff by default

**Benefits:**
- Works for all Python projects on this remote
- Individual projects can override if needed (via workspace settings)
- No need to configure each project
- Consistent development experience

## Testing

Create a test Python file anywhere:
```bash
cd ~
mkdir test-ruff
cd test-ruff
echo 'def test(): return "x" * 200' > test.py
code test.py
```

Open it in VSCode - you should see Ruff linting/formatting working automatically!

# GitHub Actions CI/CD Setup

This repository uses GitHub Actions for automated testing, building, and releasing.

## Workflows

### 1. CI (`ci.yml`)
**Triggers**: Push to main/develop, Pull Requests
- Runs tests on Python 3.10, 3.11, 3.12
- Code formatting checks (Black)
- Linting (Pylint + flake8)
- Security scanning (Safety + Bandit)
- Build verification

### 2. Release (`release.yml`)
**Triggers**: Git tag push (v*)
- Tag verification (matches expected branch)
- Quality checks and build
- GitHub Release creation
- PyPI publishing (final releases only)
- Test server notifications (pre-releases only)

### 3. Dependency Updates (`update-deps.yml`)
**Triggers**: Weekly schedule + manual
- Automated dependency updates
- Pull request creation

## Release Workflow

### Pre-releases (Alpha, Beta, RC)
```bash
# Create and push pre-release from develop branch
git checkout develop
git pull origin develop
./dev.py version alpha
git push origin v1.2.3-alpha.1
```

**What happens**:
1. GitHub Actions builds and tests
2. Creates GitHub pre-release
3. Notifies test servers via webhook
4. Does NOT publish to PyPI

### Final Releases
```bash
# Merge develop to main, then create release
git checkout main
git merge develop
git push origin main
./dev.py version release  # or ./dev.py version 1.2.3
git push origin v1.2.3
```

**What happens**:
1. GitHub Actions builds and tests
2. Creates GitHub release
3. Publishes to PyPI (requires manual approval)
4. Does NOT notify test servers

## Required Secrets

### For PyPI Publishing
- `PYPI_API_TOKEN`: PyPI API token for package publishing

### For Test Server Integration
- `TEST_SERVER_WEBHOOK_URL`: Webhook URL for test server notifications
- `TEST_SERVER_TOKEN`: Authentication token for test server

## Branch Strategy

- **`main`**: Production-ready code, **final releases only**
- **`develop`**: Integration branch, **pre-releases and testing**
- **Feature branches**: `feature/xyz` -> merge to `develop`

## Complete Development Workflow

### 1. Feature Development (No Tags Yet)
```bash
# Start from develop branch
git checkout develop
git pull origin develop

# Create feature branch using our fork workflow
./dev.py create-pr-branch feature/version-endpoint

# Do your development work...
git add .
git commit -m "Add version and health endpoints"

# Prepare for PR (runs quality checks, pushes branch)
./dev.py prepare-pr
```

**Result**: Feature branch is ready for PR to `develop`, no tags created yet.

### 2. Merge Feature to Develop (Still No Tags)
```bash
# Via GitHub PR or direct merge
git checkout develop
git merge feature/version-endpoint
git push origin develop
```

**Result**: Feature is now in `develop` branch, ready for pre-release testing, but still no tags.

### 3. Create Pre-release from Develop (First Tag!)
```bash
# Now on develop branch with your feature
git checkout develop
git pull origin develop

# Create pre-release tag for testing
./dev.py version alpha      # Creates v0.2.1-alpha.1
git push origin v0.2.1-alpha.1
```

**What happens**:
- **Tag created**: `v0.2.1-alpha.1`
- GitHub Actions triggers release workflow
- Creates GitHub **pre-release**
- Notifies test servers via webhook
- Does NOT publish to PyPI
- Does NOT affect main branch

### 4. Test and Iterate Pre-releases
```bash
# Fix issues, then create new pre-release
./dev.py version alpha      # Creates v0.2.1-alpha.2
git push origin v0.2.1-alpha.2

# Or move to beta when ready
./dev.py version beta       # Creates v0.2.1-beta.1
git push origin v0.2.1-beta.1
```

### 5. Production Release (Final Tag)
```bash
# When ready for production, merge develop to main
git checkout main
git pull origin main
git merge develop
git push origin main

# Create final production release
./dev.py version release    # v0.2.1-beta.1 -> v0.2.1
# OR
./dev.py version minor      # v0.2.0 -> v0.3.0 (if significant changes)

git push origin v0.3.0
```

**What happens**:
- **Tag created**: `v0.3.0` (final release)
- GitHub Actions triggers release workflow
- Creates GitHub **release** (not pre-release)
- Publishes to PyPI (requires manual approval)
- Does NOT notify test servers (production is live)

## Tag Strategy Summary

| Branch | Tag Type | Example | Purpose | PyPI | Test Servers |
|--------|----------|---------|---------|------|--------------|
| `feature/*` | None | - | Development | No | No |
| `develop` | Pre-release | `v0.3.0-alpha.1` | Testing | No | Yes |
| `develop` | Pre-release | `v0.3.0-beta.1` | Staging | No | Yes |
| `main` | Final | `v0.3.0` | Production | Yes | No |

## Real-World Example: Adding Version Endpoints

Let's trace through exactly what we just did:

### Step 1: Feature Development (Completed)
```bash
# We added version endpoints to the code
# We have fork management commands
# We enhanced the development workflow
# Current state: All changes are in main branch, no new tags yet
```

### Step 2: Current Situation
```bash
git tag -l --sort=-version:refname
# Shows: v0.2.0 (latest tag)
# But our code has new features since v0.2.0!
```

### Step 3: What We Should Do Next
Since we have significant new features (version endpoints, fork management), we should create a new tag:

```bash
# Option A: Direct production release (if confident)
./dev.py version minor      # v0.2.0 -> v0.3.0
git push origin v0.3.0

# Option B: Start with pre-release testing (safer)
./dev.py version alpha      # v0.2.0 -> v0.2.1-alpha.1
git push origin v0.2.1-alpha.1
```

### Step 4: After New Tag is Created
- `/version` endpoint will return the new version
- GitHub Actions will trigger
- Release will be created with our new features
- Version detection works perfectly

## Key Insight: Tags Follow Features, Not Branches

- **Wrong thinking**: "I need a tag to merge to develop"
- **Correct thinking**: "I have new features, so I need a tag to release them"

**Your version detection is working perfectly!** It's showing `v0.2.0` because that's the latest tag, and we haven't tagged our new features yet.

## Test Server Integration

The system can automatically notify test servers when pre-releases are created:

```bash
# Manual trigger (useful for testing)
./dev.py trigger-update https://test.example.com/webhook optional-token
```

**Webhook Payload**:
```json
{
  "action": "deploy",
  "version": "1.2.3-alpha.1",
  "repository": "der-control-fastlib",
  "timestamp": "2025-08-01T10:30:00+00:00"
}
```

## Environment Protection

The `pypi` environment is protected and requires manual approval for final releases, preventing accidental production deployments.

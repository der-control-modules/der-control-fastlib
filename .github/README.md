# GitHub Actions CI/CD Setup

This repository uses GitHub Actions for automated testing, building, and releasing.

## Workflows

### 1. CI (`ci.yml`)
**Triggers**: Push to main/develop, Pull Requests
- ✅ Runs tests on Python 3.10, 3.11, 3.12
- ✅ Code formatting checks (Black)
- ✅ Linting (Pylint + flake8)
- ✅ Security scanning (Safety + Bandit)
- ✅ Build verification

### 2. Release (`release.yml`)
**Triggers**: Git tag push (v*)
- ✅ Tag verification (matches expected branch)
- ✅ Quality checks and build
- ✅ GitHub Release creation
- ✅ PyPI publishing (final releases only)
- ✅ Test server notifications (pre-releases only)

### 3. Dependency Updates (`update-deps.yml`)
**Triggers**: Weekly schedule + manual
- ✅ Automated dependency updates
- ✅ Pull request creation

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

- **`main`**: Production-ready code, final releases only
- **`develop`**: Integration branch, pre-releases
- **Feature branches**: `feature/xyz` → merge to `develop`

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
  "repository": "aems-lib-fastapi",
  "timestamp": "2025-08-01T10:30:00+00:00"
}
```

## Environment Protection

The `pypi` environment is protected and requires manual approval for final releases, preventing accidental production deployments.

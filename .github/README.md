# GitHub Actions CI/CD Setup

This repository uses GitHub Actions for automated testing, building, and releasing.

## Workflows

### 1. CI (`ci.yml`)
**Triggers**: Push to main/develop, Pull Requests
- Runs tests on Python 3.10, 3.11, 3.12
- Code formatting checks (ruff)
- Linting (ruff)
- Security scanning (pip-audit + Bandit)
- Build verification

### 2. Release (`release.yml`)
**Triggers**: Git tag push (v*)
- Tag verification (matches expected branch)
- Quality checks and build
- GitHub Release creation
- PyPI publishing (final releases only)

### 3. Dependency Updates (`update-deps.yml`)
**Triggers**: Weekly schedule + manual
- Automated dependency updates
- Pull request creation

## Release Workflow

The package version is derived from git tags (`setuptools_scm`); there is no
version number stored in the source tree. Cutting a release means pushing a
tag in the `vX.Y.Z[-alpha.N|-beta.N|-rc.N]` format.

### Pre-releases (Alpha, Beta, RC)
```bash
git checkout develop
git pull origin develop
git tag v1.2.3-alpha.1
git push origin v1.2.3-alpha.1
```

**What happens**:
1. GitHub Actions builds and tests
2. Creates GitHub pre-release
3. Does NOT publish to PyPI

### Final Releases
```bash
git checkout main
git merge develop
git push origin main
git tag v1.2.3
git push origin v1.2.3
```

**What happens**:
1. GitHub Actions builds and tests
2. Creates GitHub release
3. Publishes to PyPI (requires manual approval)

## Required Secrets

### For PyPI Publishing
- `PYPI_API_TOKEN`: PyPI API token for package publishing

## Branch Strategy

- **`main`**: Production-ready code, **final releases only**
- **`develop`**: Integration branch, **pre-releases and testing**
- **Feature branches**: `feature/xyz` -> merge to `develop`

## Tag Strategy Summary

| Branch | Tag Type | Example | Purpose | PyPI |
|--------|----------|---------|---------|------|
| `feature/*` | None | - | Development | No |
| `develop` | Pre-release | `v0.3.0-alpha.1` | Testing | No |
| `develop` | Pre-release | `v0.3.0-beta.1` | Staging | No |
| `main` | Final | `v0.3.0` | Production | Yes |

## Environment Protection

The `pypi` environment is protected and requires manual approval for final releases, preventing accidental production deployments.

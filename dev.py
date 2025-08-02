#!/usr/bin/env python3
"""
Simple development helper script - Poetry-like commands for pip
Usage: python dev.py <command>
"""

import subprocess
import sys
import re
import urllib.request
import urllib.parse
import json

try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # Fallback for older Python versions
    except ImportError:
        tomllib = None


def trigger_update(server_url=None, token=None):
    """Trigger an update on a test server via webhook."""
    if not server_url:
        print("❌ Error: No server URL provided")
        print("Usage: ./dev.py trigger-update <server_url> [token]")
        print("Example: ./dev.py trigger-update https://test-server.com/webhook optional-token")
        return False

    try:
        # Get current version for the webhook payload
        current_version = get_current_version()

        # Prepare webhook payload
        payload = {
            "action": "deploy",
            "version": current_version,
            "repository": "aems-lib-fastapi",
            "timestamp": subprocess.run("date -Iseconds", shell=True, capture_output=True, text=True).stdout.strip()
        }

        # Add authentication if token provided
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "aems-dev-script"
        }

        if token:
            headers["Authorization"] = f"Bearer {token}"

        # Prepare request
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(server_url, data=data, headers=headers, method='POST')

        print(f"🔄 Triggering update on {server_url}...")
        print(f"📦 Version: {current_version}")

        # Send webhook
        with urllib.request.urlopen(req, timeout=30) as response:
            response_data = response.read().decode('utf-8')
            if response.status == 200:
                print("✅ Update triggered successfully!")
                if response_data:
                    print(f"📝 Response: {response_data}")
                return True
            else:
                print(f"⚠️ Warning: Server responded with status {response.status}")
                if response_data:
                    print(f"📝 Response: {response_data}")
                return False

    except urllib.error.HTTPError as e:
        print(f"❌ HTTP Error: {e.code} - {e.reason}")
        try:
            error_response = e.read().decode('utf-8')
            print(f"📝 Error details: {error_response}")
        except:
            pass
        return False
    except urllib.error.URLError as e:
        print(f"❌ URL Error: {e.reason}")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def get_current_version():
    """Get the current version from git tags."""
    # Get all tags sorted by version
    result = subprocess.run(
        "git tag -l --sort=-version:refname",
        shell=True,
        capture_output=True,
        text=True
    )

    if result.returncode != 0 or not result.stdout.strip():
        # No tags found, start with 0.0.0
        return "0.0.0"

    # Get the most recent tag (first in the sorted list)
    latest_tag = result.stdout.strip().split('\n')[0]
    return latest_tag.lstrip('v')


def parse_version(version_str):
    """Parse a version string into major, minor, patch components with optional pre-release."""
    # Match semantic version with optional pre-release (alpha, beta, rc)
    match = re.match(r'^v?(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc)\.?(\d+))?', version_str)
    if not match:
        raise ValueError(f"Invalid version format: {version_str}")

    major, minor, patch = int(match.group(1)), int(match.group(2)), int(match.group(3))
    prerelease_type = match.group(4)  # alpha, beta, rc
    prerelease_num = int(match.group(5)) if match.group(5) else None

    return major, minor, patch, prerelease_type, prerelease_num


def increment_version(version_str, bump_type):
    """Increment version based on bump type (major, minor, patch, alpha, beta, rc)."""
    major, minor, patch, pre_type, pre_num = parse_version(version_str)

    if bump_type == "major":
        return f"{major + 1}.0.0"
    elif bump_type == "minor":
        return f"{major}.{minor + 1}.0"
    elif bump_type == "patch":
        return f"{major}.{minor}.{patch + 1}"
    elif bump_type == "alpha":
        if pre_type == "alpha":
            # Increment existing alpha
            return f"{major}.{minor}.{patch}-alpha.{(pre_num or 0) + 1}"
        else:
            # Create new alpha from current version
            return f"{major}.{minor}.{patch + 1}-alpha.1"
    elif bump_type == "beta":
        if pre_type == "beta":
            # Increment existing beta
            return f"{major}.{minor}.{patch}-beta.{(pre_num or 0) + 1}"
        elif pre_type == "alpha":
            # Promote alpha to beta
            return f"{major}.{minor}.{patch}-beta.1"
        else:
            # Create new beta from current version
            return f"{major}.{minor}.{patch + 1}-beta.1"
    elif bump_type == "rc":
        if pre_type == "rc":
            # Increment existing rc
            return f"{major}.{minor}.{patch}-rc.{(pre_num or 0) + 1}"
        elif pre_type in ["alpha", "beta"]:
            # Promote alpha/beta to rc
            return f"{major}.{minor}.{patch}-rc.1"
        else:
            # Create new rc from current version
            return f"{major}.{minor}.{patch + 1}-rc.1"
    elif bump_type == "release":
        if pre_type:
            # Promote pre-release to final release
            return f"{major}.{minor}.{patch}"
        else:
            raise ValueError("Current version is already a release version")
    else:
        raise ValueError(f"Invalid bump type: {bump_type}")


def version(version_arg=None):
    """Manage package versioning with git tags."""
    if version_arg is None:
        # Show current version
        current = get_current_version()
        print(f"Current version: {current}")
        return True

    current_version = get_current_version()

    # Handle semantic version bumps including pre-releases
    valid_bump_types = ["major", "minor", "patch", "alpha", "beta", "rc", "release"]
    if version_arg in valid_bump_types:
        try:
            new_version = increment_version(current_version, version_arg)
            print(f"Bumping {version_arg} version: {current_version} → {new_version}")
        except ValueError as e:
            print(f"❌ Error: {e}")
            return False
    else:
        # Handle explicit version number
        try:
            # Validate the version format
            parse_version(version_arg)
            new_version = version_arg.lstrip('v')
            print(f"Setting version: {current_version} → {new_version}")
        except ValueError as e:
            print(f"❌ Error: {e}")
            print("Version should be in format: major.minor.patch[-prerelease.num] (e.g., 1.2.3, 1.2.3-alpha.1)")
            return False

    # Create git tag
    tag_name = f"v{new_version}"

    # Check if tag already exists
    check_result = subprocess.run(
        f"git tag -l {tag_name}",
        shell=True,
        capture_output=True,
        text=True
    )

    if check_result.stdout.strip():
        print(f"❌ Error: Tag {tag_name} already exists")
        return False

    # Create the tag
    tag_success = run_command(
        f"git tag {tag_name}",
        f"Creating git tag {tag_name}"
    )

    if tag_success:
        print(f"✅ Version {new_version} tagged successfully!")
        if "alpha" in new_version or "beta" in new_version or "rc" in new_version:
            print(f"🚀 Pre-release version created. To publish: git push origin {tag_name}")
        else:
            print(f"💡 To publish: git push origin {tag_name}")
        return True

    return False


def get_dev_dependencies():
    """Get list of dev dependencies from pyproject.toml."""
    if tomllib is None:
        print("⚠️ Warning: tomllib/tomli not available, using fallback dependency list")
        return ["black", "pylint", "flake8", "flake8-pyproject", "pytest", "pytest-cov", "build"]

    try:
        with open("pyproject.toml", "rb") as f:
            data = tomllib.load(f)

        dev_deps = data.get("project", {}).get("optional-dependencies", {}).get("dev", [])

        # Extract package names (remove version specs like >=1.0.0)
        package_names = []
        for dep in dev_deps:
            # Split on common version operators and take the first part
            name = dep.split(">=")[0].split("==")[0].split("~=")[0].split(">")[0].split("<")[0]
            package_names.append(name.strip())

        return package_names
    except Exception as e:
        print(f"⚠️ Warning: Could not read dev dependencies from pyproject.toml: {e}")
        # Fallback to hardcoded list
        return ["black", "pylint", "flake8", "flake8-pyproject", "pytest", "pytest-cov", "build"]


def run_command(cmd, description=None):
    """Run a shell command and handle errors."""
    if description:
        print(f"🔄 {description}...")

    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"❌ Error: {result.stderr}")
        return False

    if result.stdout:
        print(result.stdout)

    return True


def setup_upstream(upstream_url=None):
    """Set up upstream remote for fork workflow."""
    if not upstream_url:
        print("❌ Error: No upstream URL provided")
        print("Usage: ./dev.py setup-upstream <upstream_url>")
        print("Example: ./dev.py setup-upstream https://github.com/VOLTTRON/aems-lib-fastapi.git")
        return False

    # Check if upstream already exists
    result = subprocess.run(
        "git remote get-url upstream",
        shell=True,
        capture_output=True,
        text=True
    )

    if result.returncode == 0:
        current_upstream = result.stdout.strip()
        print(f"📍 Upstream already configured: {current_upstream}")

        if current_upstream != upstream_url:
            print(f"🔄 Updating upstream URL from {current_upstream} to {upstream_url}")
            return run_command(f"git remote set-url upstream {upstream_url}", "Updating upstream URL")
        else:
            print("✅ Upstream is already correctly configured")
            return True
    else:
        print(f"🔄 Adding upstream remote: {upstream_url}")
        return run_command(f"git remote add upstream {upstream_url}", "Adding upstream remote")


def sync_fork():
    """Sync fork with upstream repository."""
    print("🔄 Syncing fork with upstream...")

    # Check if upstream remote exists
    result = subprocess.run(
        "git remote get-url upstream",
        shell=True,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        print("❌ Error: No upstream remote configured")
        print("💡 Run: ./dev.py setup-upstream <upstream_url> first")
        return False

    # Fetch upstream changes
    if not run_command("git fetch upstream", "Fetching upstream changes"):
        return False

    # Get current branch
    result = subprocess.run(
        "git branch --show-current",
        shell=True,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        print("❌ Error: Could not determine current branch")
        return False

    current_branch = result.stdout.strip()

    # Sync main branch
    if current_branch != "main":
        if not run_command("git checkout main", "Switching to main branch"):
            return False

    if not run_command("git merge upstream/main", "Merging upstream/main"):
        return False

    if not run_command("git push origin main", "Pushing updated main to fork"):
        return False

    # Sync develop branch if it exists
    result = subprocess.run(
        "git branch -r | grep origin/develop",
        shell=True,
        capture_output=True,
        text=True
    )

    if result.returncode == 0:
        if not run_command("git checkout develop", "Switching to develop branch"):
            return False

        if not run_command("git merge upstream/develop", "Merging upstream/develop"):
            return False

        if not run_command("git push origin develop", "Pushing updated develop to fork"):
            return False

    # Return to original branch
    if current_branch not in ["main", "develop"]:
        run_command(f"git checkout {current_branch}", f"Returning to {current_branch}")

    print("✅ Fork synced successfully!")
    return True


def create_pr_branch(branch_name=None):
    """Create a new branch for pull request from up-to-date develop."""
    if not branch_name:
        print("❌ Error: No branch name provided")
        print("Usage: ./dev.py create-pr-branch <branch_name>")
        print("Example: ./dev.py create-pr-branch feature/new-feature")
        return False

    print(f"🌿 Creating PR branch: {branch_name}")

    # Ensure we're synced first
    print("🔄 Syncing with upstream first...")
    if not sync_fork():
        print("⚠️ Warning: Could not sync fork, proceeding with current state")

    # Switch to develop (or main if no develop)
    base_branch = "develop"
    result = subprocess.run(
        "git branch -r | grep origin/develop",
        shell=True,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        base_branch = "main"

    if not run_command(f"git checkout {base_branch}", f"Switching to {base_branch}"):
        return False

    # Create and switch to new branch
    if not run_command(f"git checkout -b {branch_name}", f"Creating branch {branch_name}"):
        return False

    print(f"✅ Created branch '{branch_name}' from '{base_branch}'")
    print(f"💡 When ready, push with: git push -u origin {branch_name}")
    return True


def prepare_pr():
    """Prepare current branch for pull request."""
    print("🔄 Preparing branch for pull request...")

    # Get current branch
    result = subprocess.run(
        "git branch --show-current",
        shell=True,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        print("❌ Error: Could not determine current branch")
        return False

    current_branch = result.stdout.strip()

    if current_branch in ["main", "develop"]:
        print("❌ Error: Cannot prepare main/develop branch for PR")
        print("💡 Create a feature branch first: ./dev.py create-pr-branch feature/my-feature")
        return False

    # Run quality checks
    print("🧪 Running quality checks...")
    if not check():
        print("❌ Quality checks failed! Fix issues before creating PR")
        return False

    # Check if branch has commits
    result = subprocess.run(
        "git log origin/develop..HEAD --oneline 2>/dev/null || git log origin/main..HEAD --oneline",
        shell=True,
        capture_output=True,
        text=True
    )

    if not result.stdout.strip():
        print("❌ Error: No commits found on this branch")
        return False

    commits = result.stdout.strip().split('\n')
    print(f"📝 Found {len(commits)} commit(s) on this branch:")
    for commit in commits[:5]:  # Show first 5 commits
        print(f"  • {commit}")

    if len(commits) > 5:
        print(f"  ... and {len(commits) - 5} more")

    # Push branch if not already pushed
    result = subprocess.run(
        f"git ls-remote --heads origin {current_branch}",
        shell=True,
        capture_output=True,
        text=True
    )

    if not result.stdout.strip():
        print("🚀 Pushing branch to origin...")
        if not run_command(f"git push -u origin {current_branch}", f"Pushing {current_branch}"):
            return False
    else:
        print("🔄 Updating remote branch...")
        if not run_command("git push", "Pushing latest changes"):
            return False

    print("✅ Branch is ready for pull request!")
    print(f"💡 Create PR at: https://github.com/VOLTTRON/aems-lib-fastapi/compare/{current_branch}")
    return True


def install():
    """Install the package in development mode with dev dependencies."""
    return run_command(
        "pip install -e .[dev]", "Installing development dependencies"
    )


def install_prod():
    """Install only production dependencies and remove dev tools."""
    print("🔄 Installing production dependencies and cleaning dev tools...")

    # Get dev packages dynamically from pyproject.toml
    dev_packages = get_dev_dependencies()

    for pkg in dev_packages:
        run_command(f"pip uninstall -y {pkg}", f"Removing {pkg}")

    # Then install only production dependencies
    return run_command(
        "pip install -e .", "Installing production dependencies only"
    )


def update():
    """Update all development dependencies to latest compatible versions."""
    print("🔄 Updating dependencies...")

    # Get current dev dependencies dynamically from pyproject.toml
    deps = get_dev_dependencies()

    for dep in deps:
        print(f"  📦 Updating {dep}...")
        run_command(f"pip install --upgrade {dep}")

    print("✅ All dependencies updated!")
    return True


def format_code():
    """Format code with Black."""
    return run_command("black src/ tests/", "Formatting code")


def lint():
    """Run linting checks."""
    print("🔍 Running linting checks...")
    pylint_ok = run_command("pylint src/", "Running Pylint")
    flake8_ok = run_command("flake8 src/", "Running flake8")
    return pylint_ok and flake8_ok


def test():
    """Run tests."""
    return run_command("pytest", "Running tests")


def security():
    """Run security scans."""
    print("🔒 Running security scans...")
    
    # Run pip-audit for dependency vulnerability scanning
    pip_audit_ok = run_command("pip-audit", "Scanning dependencies with pip-audit")
    
    # Run bandit for code security analysis
    bandit_ok = run_command("bandit -r src/ -f json -o bandit-report.json", "Running Bandit security analysis")
    
    if bandit_ok:
        print("📄 Bandit report saved to bandit-report.json")
        # Also run bandit with console output for immediate feedback
        run_command("bandit -r src/", "Bandit security summary")
    
    return pip_audit_ok and bandit_ok


def build():
    """Build wheel package."""
    print("🔨 Building wheel package...")

    # Clean previous builds
    clean_ok = run_command(
        "rm -rf build/ dist/ *.egg-info/", "Cleaning previous builds"
    )
    if not clean_ok:
        return False

    # Build wheel
    build_ok = run_command("python -m build", "Building wheel")
    if build_ok:
        print("✅ Wheel built successfully! Check dist/ folder")

    return build_ok


def check():
    """Run format, lint, and test."""
    print("🧪 Running full check suite...")
    format_ok = format_code()
    lint_ok = lint()
    test_ok = test()

    if format_ok and lint_ok and test_ok:
        print("✅ All checks passed!")
        return True
    else:
        print("❌ Some checks failed!")
        return False


def show_help():
    """Show available commands."""
    print("""
🚀 Development Helper Commands:

  install         Install development dependencies (default for developers)
  install-prod    Install production dependencies only (removes dev tools)
  update          Update all dependencies to latest
  format          Format code with Black
  lint            Run Pylint and flake8
  test            Run tests
  security        Run security scans (pip-audit + bandit)
  build           Build wheel package
  check           Run format + lint + test
  version         Show current version or set new version
  trigger-update  Trigger deployment update on test server

Fork Management:
  setup-upstream  Configure upstream remote for fork workflow
  sync-fork       Sync fork with upstream repository
  create-pr-branch Create new branch for pull request
  prepare-pr      Prepare current branch for pull request

  help            Show this help

Version Management:
  ./dev.py version                    # Show current version
  ./dev.py version 1.2.3              # Set specific version
  ./dev.py version 1.2.3-alpha.1      # Set specific pre-release version

  Semantic Version Bumps:
  ./dev.py version major              # 1.0.0 → 2.0.0
  ./dev.py version minor              # 1.0.0 → 1.1.0
  ./dev.py version patch              # 1.0.0 → 1.0.1

  Pre-release Versions:
  ./dev.py version alpha              # 1.0.0 → 1.0.1-alpha.1
  ./dev.py version beta               # 1.0.0-alpha.1 → 1.0.0-beta.1
  ./dev.py version rc                 # 1.0.0-beta.1 → 1.0.0-rc.1
  ./dev.py version release            # 1.0.0-rc.1 → 1.0.0

Server Integration:
  ./dev.py trigger-update <url>       # Trigger update on test server
  ./dev.py trigger-update <url> <tok> # With authentication token

Fork Workflow:
  ./dev.py setup-upstream https://github.com/VOLTTRON/aems-lib-fastapi.git
  ./dev.py sync-fork                  # Sync with upstream changes
  ./dev.py create-pr-branch feature/my-feature # Create feature branch
  ./dev.py prepare-pr                 # Quality check and push for PR

Examples:
  ./dev.py install       # For development setup
  ./dev.py install-prod  # For production deployment
  ./dev.py update
  ./dev.py build
  ./dev.py check
  ./dev.py version alpha # Create alpha release
  ./dev.py trigger-update https://test.example.com/webhook
""")


def main():
    if len(sys.argv) < 2:
        show_help()
        return

    command = sys.argv[1].lower()

    # Handle version command with optional argument
    if command == "version":
        version_arg = sys.argv[2] if len(sys.argv) > 2 else None
        success = version(version_arg)
        sys.exit(0 if success else 1)

    # Handle trigger-update command with required URL and optional token
    elif command == "trigger-update":
        if len(sys.argv) < 3:
            print("❌ Error: Server URL required")
            print("Usage: ./dev.py trigger-update <server_url> [token]")
            sys.exit(1)

        server_url = sys.argv[2]
        token = sys.argv[3] if len(sys.argv) > 3 else None
        success = trigger_update(server_url, token)
        sys.exit(0 if success else 1)

    # Handle setup-upstream command with required URL
    elif command == "setup-upstream":
        upstream_url = sys.argv[2] if len(sys.argv) > 2 else None
        success = setup_upstream(upstream_url)
        sys.exit(0 if success else 1)

    # Handle create-pr-branch command with required branch name
    elif command == "create-pr-branch":
        branch_name = sys.argv[2] if len(sys.argv) > 2 else None
        success = create_pr_branch(branch_name)
        sys.exit(0 if success else 1)

    commands = {
        'install': install,
        'install-prod': install_prod,
        'update': update,
        'format': format_code,
        'lint': lint,
        'test': test,
        'security': security,
        'build': build,
        'check': check,
        'sync-fork': sync_fork,
        'prepare-pr': prepare_pr,
        'help': show_help,
    }

    if command in commands:
        success = commands[command]()
        sys.exit(0 if success else 1)
    else:
        print(f"❌ Unknown command: {command}")
        show_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

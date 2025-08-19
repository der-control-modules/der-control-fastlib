#!/usr/bin/env python3
"""
Development Helper Script for ANY python Library

This script provides a set of command-line utilities for managing the AEMS library
development lifecycle. It aims to provide Poetry-like functionality while using pip
under the hood, making the development workflow more streamlined.

Features include:
- Package installation (dev and production modes)
- Code formatting, linting and testing
- Version management with semantic versioning support
- Git workflow helpers (fork syncing, PR preparation)
- Security scanning
- Build tools

Usage:
    python dev.py <command> [arguments]
    ./dev.py <command> [arguments]

Run `dev.py help` for a full list of available commands.
"""

import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request

# Import tomllib (Python 3.11+) or tomli as a fallback for older Python versions
try:
    import tomllib  # Standard library in Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # Fallback for Python 3.10 and older
    except ImportError:
        tomllib = None  # Fallback to hardcoded defaults when no TOML parser is available


def trigger_update(server_url=None, token=None):
    """
    Trigger an update on a test server via webhook.

    This function sends a webhook request to a specified server URL with
    payload information about the current version. It can be used to trigger
    automatic deployments or updates on test environments.

    Args:
        server_url (str): The URL of the webhook endpoint to call
        token (str, optional): Authentication token to include in the request

    Returns:
        bool: True if the update was successfully triggered, False otherwise

    Examples:
        >>> trigger_update("https://test-server.com/webhook")
        >>> trigger_update("https://test-server.com/webhook", "secret-token")
    """
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
            "timestamp": subprocess.run("date -Iseconds", shell=True, capture_output=True, text=True).stdout.strip(),
        }

        # Add authentication if token provided
        headers = {"Content-Type": "application/json", "User-Agent": "aems-dev-script"}

        if token:
            headers["Authorization"] = f"Bearer {token}"

        # Prepare request
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(server_url, data=data, headers=headers, method="POST")

        print(f"🔄 Triggering update on {server_url}...")
        print(f"📦 Version: {current_version}")

        # Send webhook
        with urllib.request.urlopen(req, timeout=30) as response:
            response_data = response.read().decode("utf-8")
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
            error_response = e.read().decode("utf-8")
            print(f"📝 Error details: {error_response}")
        except Exception:
            pass
        return False
    except urllib.error.URLError as e:
        print(f"❌ URL Error: {e.reason}")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def get_current_version():
    """
    Get the current version from git tags.

    Retrieves the most recent version tag from git and returns it in a format
    suitable for semantic versioning. If no tags are found, defaults to '0.0.0'.

    Returns:
        str: The current version string without the 'v' prefix
    """
    # Get all tags sorted by version
    result = subprocess.run("git tag -l --sort=-version:refname", shell=True, capture_output=True, text=True)

    if result.returncode != 0 or not result.stdout.strip():
        # No tags found, start with 0.0.0
        return "0.0.0"

    # Get the most recent tag (first in the sorted list)
    latest_tag = result.stdout.strip().split("\n")[0]
    return latest_tag.lstrip("v")


def parse_version(version_str):
    """
    Parse a version string into its semantic versioning components.

    Breaks down a version string into major, minor, patch components
    and optional pre-release information (type and number).

    Args:
        version_str (str): Version string to parse (e.g., '1.2.3', '1.2.3-alpha.1')

    Returns:
        tuple: (major, minor, patch, prerelease_type, prerelease_num)

    Raises:
        ValueError: If the version string format is invalid

    Examples:
        >>> parse_version('1.2.3')
        (1, 2, 3, None, None)
        >>> parse_version('1.2.3-alpha.1')
        (1, 2, 3, 'alpha', 1)
    """
    # Match semantic version with optional pre-release (alpha, beta, rc)
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc)\.?(\d+))?", version_str)
    if not match:
        raise ValueError(f"Invalid version format: {version_str}")

    major, minor, patch = int(match.group(1)), int(match.group(2)), int(match.group(3))
    prerelease_type = match.group(4)  # alpha, beta, rc
    prerelease_num = int(match.group(5)) if match.group(5) else None

    return major, minor, patch, prerelease_type, prerelease_num


def increment_version(version_str, bump_type):
    """
    Increment version based on bump type according to Semantic Versioning rules.

    Takes a current version string and a bump type, and returns a new version
    string that has been incremented according to SemVer rules. Handles major,
    minor, and patch level increments as well as pre-release versions (alpha,
    beta, release candidate).

    Args:
        version_str (str): Current version string
        bump_type (str): Type of version bump to perform:
                        'major', 'minor', 'patch', 'alpha', 'beta',
                        'rc', or 'release'

    Returns:
        str: New version string after applying the bump

    Raises:
        ValueError: If the bump type is invalid or if trying to promote
                    a release version with 'release' bump type

    Versioning Rules:
        - major: Increments the major version, resets minor and patch to 0
        - minor: Increments the minor version, resets patch to 0
        - patch: Increments the patch version only
        - alpha: Creates or increments an alpha pre-release
        - beta: Creates, increments, or promotes to beta pre-release
        - rc: Creates, increments, or promotes to release candidate
        - release: Promotes a pre-release to a final release
    """
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
    """
    Manage package versioning with git tags.

    This function either displays the current version or creates a new git tag
    with an updated version. It supports semantic versioning increments (major,
    minor, patch) and pre-release types (alpha, beta, rc).

    Args:
        version_arg (str, optional):
            - None: Show current version
            - "major", "minor", "patch": Increment specific version component
            - "alpha", "beta", "rc": Create or increment pre-release version
            - "release": Promote pre-release to final release
            - Any other string: Treated as an explicit version number

    Returns:
        bool: True if the operation was successful, False otherwise

    Examples:
        >>> version()               # Display current version
        >>> version("minor")        # Bump minor version (1.0.0 -> 1.1.0)
        >>> version("beta")         # Create beta (1.0.0 -> 1.0.1-beta.1)
        >>> version("1.5.0-rc.2")   # Set specific version
    """
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
            new_version = version_arg.lstrip("v")
            print(f"Setting version: {current_version} → {new_version}")
        except ValueError as e:
            print(f"❌ Error: {e}")
            print("Version should be in format: major.minor.patch[-prerelease.num] (e.g., 1.2.3, 1.2.3-alpha.1)")
            return False

    # Create git tag
    tag_name = f"v{new_version}"

    # Check if tag already exists
    check_result = subprocess.run(f"git tag -l {tag_name}", shell=True, capture_output=True, text=True)

    if check_result.stdout.strip():
        print(f"❌ Error: Tag {tag_name} already exists")
        return False

    # Create the tag
    tag_success = run_command(f"git tag {tag_name}", f"Creating git tag {tag_name}")

    if tag_success:
        print(f"✅ Version {new_version} tagged successfully!")
        if "alpha" in new_version or "beta" in new_version or "rc" in new_version:
            print(f"🚀 Pre-release version created. To publish: git push origin {tag_name}")
        else:
            print(f"💡 To publish: git push origin {tag_name}")
        return True

    return False


def get_dev_dependencies():
    """
    Get list of development dependencies from pyproject.toml.

    Reads the pyproject.toml file to extract the development dependencies.
    If tomllib/tomli is not available or the file cannot be read, falls
    back to a hardcoded list of common development dependencies.

    Returns:
        list: A list of development dependency package names (without version specifiers)
    """
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
    """
    Run a shell command and handle errors.

    Executes a shell command, optionally with a description, and handles
    any errors or output. Captures both stdout and stderr.

    Args:
        cmd (str): The shell command to execute
        description (str, optional): A description of what the command does

    Returns:
        bool: True if the command executed successfully (returncode 0), False otherwise

    Notes:
        - Stdout is printed if available
        - Error messages with stderr are printed if the command fails
    """
    if description:
        print(f"🔄 {description}...")

    # Run the command and capture output
    process = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if process.returncode != 0:
        print("❌ Error:")
        if process.stderr:
            print(process.stderr)
        return False

    if process.stdout:
        print(process.stdout)

    return process.returncode == 0


def setup_upstream(upstream_url=None):
    """
    Set up upstream remote for fork workflow.

    Configures or updates the 'upstream' git remote to point to the original
    repository. This is a common setup for fork-based workflows where you want
    to keep your fork in sync with the original repository.

    Args:
        upstream_url (str): The URL of the original repository to set as upstream

    Returns:
        bool: True if the upstream was configured successfully, False otherwise

    Examples:
        >>> setup_upstream("https://github.com/VOLTTRON/aems-lib-fastapi.git")
    """
    if not upstream_url:
        print("❌ Error: No upstream URL provided")
        print("Usage: ./dev.py setup-upstream <upstream_url>")
        print("Example: ./dev.py setup-upstream https://github.com/VOLTTRON/aems-lib-fastapi.git")
        return False

    # Check if upstream already exists
    result = subprocess.run("git remote get-url upstream", shell=True, capture_output=True, text=True)

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
    """
    Sync fork with upstream repository.

    Keeps your fork up-to-date with the original repository by fetching
    changes from upstream and merging them into your local branches.
    Automatically handles syncing both main and develop branches if they exist.

    Returns:
        bool: True if the sync was successful, False otherwise

    Notes:
        - Requires upstream remote to be configured first
        - Syncs main branch and develop branch (if it exists)
        - Returns to the original branch after syncing
    """
    print("🔄 Syncing fork with upstream...")

    # Check if upstream remote exists
    result = subprocess.run("git remote get-url upstream", shell=True, capture_output=True, text=True)

    if result.returncode != 0:
        print("❌ Error: No upstream remote configured")
        print("💡 Run: ./dev.py setup-upstream <upstream_url> first")
        return False

    # Fetch upstream changes
    if not run_command("git fetch upstream", "Fetching upstream changes"):
        return False

    # Get current branch
    result = subprocess.run("git branch --show-current", shell=True, capture_output=True, text=True)

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
    result = subprocess.run("git branch -r | grep origin/develop", shell=True, capture_output=True, text=True)

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
    """
    Create a new branch for pull request from up-to-date develop branch.

    Creates a new branch from the develop branch (or main if develop doesn't exist)
    for developing a new feature or fix. Automatically syncs with upstream first
    to ensure the branch is created from the latest code.

    Args:
        branch_name (str): Name for the new branch (e.g., 'feature/new-feature')

    Returns:
        bool: True if the branch was created successfully, False otherwise

    Examples:
        >>> create_pr_branch("feature/add-new-api")
        >>> create_pr_branch("fix/issue-123")
    """
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
    result = subprocess.run("git branch -r | grep origin/develop", shell=True, capture_output=True, text=True)

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
    """
    Prepare current branch for pull request.

    Runs quality checks, ensures there are commits on the branch,
    and pushes the branch to the remote repository if needed.
    This helps ensure the branch is ready for a pull request.

    Returns:
        bool: True if the branch is ready for PR, False otherwise

    Notes:
        - Won't allow preparing main or develop branches
        - Runs quality checks (format, lint, test)
        - Verifies there are commits on the branch
        - Pushes the branch to remote if needed
    """
    print("🔄 Preparing branch for pull request...")

    # Get current branch
    result = subprocess.run("git branch --show-current", shell=True, capture_output=True, text=True)

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
        text=True,
    )

    if not result.stdout.strip():
        print("❌ Error: No commits found on this branch")
        return False

    commits = result.stdout.strip().split("\n")
    print(f"📝 Found {len(commits)} commit(s) on this branch:")
    for commit in commits[:5]:  # Show first 5 commits
        print(f"  • {commit}")

    if len(commits) > 5:
        print(f"  ... and {len(commits) - 5} more")

    # Push branch if not already pushed
    result = subprocess.run(
        f"git ls-remote --heads origin {current_branch}", shell=True, capture_output=True, text=True
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
    """
    Install the package in development mode with dev dependencies.

    Installs the package using pip's editable mode (-e) with development
    dependencies included. This is equivalent to `pip install -e .[dev]`.

    Returns:
        bool: True if installation was successful, False otherwise
    """
    return run_command("pip install -e .[dev]", "Installing development dependencies")


def install_prod():
    """
    Install only production dependencies and remove dev tools.

    Removes all development dependencies and installs only the production
    dependencies. This is useful for testing a clean production environment
    or preparing for deployment.

    Returns:
        bool: True if installation was successful, False otherwise
    """
    print("🔄 Installing production dependencies and cleaning dev tools...")

    # Get dev packages dynamically from pyproject.toml
    dev_packages = get_dev_dependencies()

    for pkg in dev_packages:
        run_command(f"pip uninstall -y {pkg}", f"Removing {pkg}")

    # Then install only production dependencies
    return run_command("pip install -e .", "Installing production dependencies only")


def update():
    """
    Update all development dependencies to latest compatible versions.

    Iterates through the development dependencies and updates each one
    to the latest version compatible with the specified constraints.

    Returns:
        bool: True if all updates were successful, False otherwise
    """
    print("🔄 Updating dependencies...")

    # Get current dev dependencies dynamically from pyproject.toml
    deps = get_dev_dependencies()

    for dep in deps:
        print(f"  📦 Updating {dep}...")
        run_command(f"pip install --upgrade {dep}")

    print("✅ All dependencies updated!")
    return True


def format_code():
    """
    Format code with Black.

    Runs the Black code formatter on the source and test directories to
    ensure consistent code style across the project.

    Returns:
        bool: True if formatting was successful, False otherwise
    """
    return run_command("black src/ tests/", "Formatting code")


def lint():
    """
    Run linting checks.

    Executes both Pylint and flake8 on the source code to identify
    potential issues, bugs, and stylistic problems.

    Returns:
        bool: True if all linting checks passed, False otherwise
    """
    print("🔍 Running linting checks...")

    print("🔄 Running Pylint...")
    pylint_process = subprocess.run("pylint src/", shell=True, text=True)
    pylint_ok = pylint_process.returncode == 0

    print("\n🔄 Running flake8...")
    flake8_process = subprocess.run("flake8 src/", shell=True, text=True)
    flake8_ok = flake8_process.returncode == 0

    if pylint_ok and flake8_ok:
        print("✅ Linting passed!")
        return True
    else:
        print("❌ Linting checks failed!")
        return False


def test():
    """
    Run tests.

    Executes the project's test suite using pytest to verify that
    the code functions correctly.

    Returns:
        bool: True if all tests passed, False otherwise
    """
    return run_command("pytest", "Running tests")


def coverage():
    """
    Run tests with coverage reporting.

    Executes the project's test suite using pytest with the coverage plugin
    to measure code coverage and generate a detailed report with visual progress.

    Returns:
        bool: True if all tests passed, False otherwise
    """
    print("🔄 Running tests with coverage and visual progress...")

    # Run pytest directly (without capture_output) to show real-time progress
    # The -v flag makes pytest output each test name as it runs
    result = subprocess.run(
        ["python", "-m", "pytest", "-v", "--cov=src", "--cov-report=term", "--cov-report=html"],
        text=True,
    )

    if result.returncode != 0:
        print("❌ Error in test execution!")
        return False

    print("✅ Coverage tests completed successfully!")
    return True


def security():
    """
    Run security scans.

    Performs security scanning on both the code and dependencies to
    identify potential security vulnerabilities.

    Returns:
        bool: True if all security checks passed, False otherwise

    Notes:
        - Uses pip-audit to scan dependencies for vulnerabilities
        - Uses bandit to scan the codebase for security issues
        - Generates a JSON report in bandit-report.json
    """
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
    """
    Build wheel package.

    Creates a Python wheel package by cleaning previous builds and
    running the build process. The resulting wheel is placed in the
    dist/ directory.

    Returns:
        bool: True if the build was successful, False otherwise
    """
    print("🔨 Building wheel package...")

    # Clean previous builds
    clean_ok = run_command("rm -rf build/ dist/ *.egg-info/", "Cleaning previous builds")
    if not clean_ok:
        return False

    # Build wheel
    build_ok = run_command("python -m build", "Building wheel")
    if build_ok:
        print("✅ Wheel built successfully! Check dist/ folder")

    return build_ok


def check():
    """
    Run format, lint, and test.

    Comprehensive quality check that runs code formatting,
    linting, and tests in sequence. This is useful before
    committing code or preparing a pull request.

    Returns:
        bool: True if all checks passed, False otherwise
    """
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
    """
    Show available commands.

    Displays comprehensive help information about all available
    commands, their purposes, and examples of how to use them.
    """
    print(
        """
🚀 Development Helper Commands:

  install         Install development dependencies (default for developers)
  install-prod    Install production dependencies only (removes dev tools)
  update          Update all dependencies to latest
  format          Format code with Black
  lint            Run Pylint and flake8
  test            Run tests
  coverage        Run tests with coverage reporting and visual progress
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
"""
    )


def main():
    """
    Main entry point for the development helper script.

    Parses command line arguments and dispatches to the appropriate
    function based on the command. Handles special cases for commands
    that require additional arguments.

    Command dispatch logic:
    1. No command or 'help': Show help information
    2. Special case commands (version, trigger-update, etc.): Handle with args
    3. Standard commands: Dispatch to corresponding function

    Exit codes:
    - 0: Command executed successfully
    - 1: Command failed or invalid command
    """
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

    # Map command names to their corresponding functions
    commands = {
        "install": install,
        "install-prod": install_prod,
        "update": update,
        "format": format_code,
        "lint": lint,
        "test": test,
        "coverage": coverage,
        "security": security,
        "build": build,
        "check": check,
        "sync-fork": sync_fork,
        "prepare-pr": prepare_pr,
        "help": show_help,
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

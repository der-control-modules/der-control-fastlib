# config_store.py - Enhanced to support different formats

import csv
import datetime
import io
import json
import logging
import os
import threading
import uuid
from copy import deepcopy
from typing import Any, Union

from aems.server.models import Message, MessageBus

_log = logging.getLogger(__name__)

# Config reference resolution constants (matching VOLTTRON)
LINK_PREFIX = "config://"


def strip_config_name(config_name):
    """Strip whitespace and path separators from config name."""
    from string import whitespace

    return config_name.strip(whitespace + r"\\/")


def check_for_config_link(value):
    """Check if a value is a config:// reference and return the referenced config name."""
    if isinstance(value, str) and value.startswith(LINK_PREFIX):
        config_name = value.replace(LINK_PREFIX, "", 1)
        config_name = strip_config_name(config_name)
        return config_name.lower()
    return None


class ConfigStore:
    """
    A service for storing and retrieving agent configurations.
    Configurations are stored as JSON files in a directory structure.
    This follows VOLTTRON's config store pattern of centralized configuration management.
    """

    def __init__(self, base_dir: str = None, messagebus: MessageBus = None):
        """
        Initialize the config store with a base directory.

        By default, uses VOLTTRON_HOME/aems_config_store if VOLTTRON_HOME is set,
        otherwise falls back to ~/.aems/config_store
        """
        if base_dir is None:
            # Try to use VOLTTRON_HOME environment variable
            volttron_home = os.environ.get("VOLTTRON_HOME")
            if volttron_home:
                base_dir = os.path.join(volttron_home, "aems_config_store")
            else:
                # Fall back to ~/.aems/config_store if VOLTTRON_HOME not set
                home_dir = os.path.expanduser("~")
                base_dir = os.path.join(home_dir, ".aems", "config_store")

        self.base_dir = base_dir
        self.lock = threading.RLock()  # For thread safety
        self.messagebus = messagebus

        # Create the base directory if it doesn't exist
        os.makedirs(base_dir, exist_ok=True)

        # Note: File watching removed - all changes go through API in our implementation
        # In VOLTTRON, file watching is for external changes (manual edits, vctl config, etc.)
        # Since our implementation only uses API endpoints, we don't need file watching

        _log.info(f"ConfigStore initialized with base directory: {base_dir}")

    def store(
        self, agent_id: str, config_name: str, config_data: Any, config_type: str = "json", send_update: bool = True
    ) -> bool:
        """
        Store a configuration entry for an agent.

        Args:
            agent_id: The identity of the agent
            config_name: The name of the configuration
            config_data: The configuration data
            config_type: The type of configuration ("json" or "csv")

        Returns
        -------
            bool: True if successful, False otherwise
        """
        # Validate inputs to prevent errors
        if not agent_id or not config_name:
            _log.error(f"Cannot store config: Invalid agent_id='{agent_id}' or config_name='{config_name}'")
            return False

        with self.lock:
            # Check if config already exists before storing (to determine NEW vs UPDATE)
            config_exists = self.exists(agent_id, config_name)

            agent_dir = os.path.join(self.base_dir, agent_id)
            os.makedirs(agent_dir, exist_ok=True)

            # Store metadata about the config
            self._store_metadata(agent_dir, config_name, config_type)

            # Store the actual config data
            result = False
            if config_type == "json":
                result = self._store_json(agent_dir, config_name, config_data)
            elif config_type == "csv":
                result = self._store_csv(agent_dir, config_name, config_data)
            else:
                _log.error(f"Unsupported config type: {config_type}")
                return False

            # Log successful storage at INFO level
            if result:
                _log.info(f"Config store updated: {agent_id}/{config_name} ({config_type})")

                # Send notifications for API changes
                # This emulates the behavior of vctl config and other external tools
                # Note: send_update=True by default for external changes, but can be overridden
                full_name = f"{agent_id}/{config_name}"
                action = "UPDATE" if config_exists else "NEW"

                self.notify_change(full_name, action, config_data, send_update=send_update)

            return result

    def _store_metadata(self, agent_dir: str, config_name: str, config_type: str):
        """Store metadata about a configuration."""
        metadata_file = os.path.join(agent_dir, f"{config_name}.metadata")
        # Create parent directories if config_name contains slashes
        metadata_dir = os.path.dirname(metadata_file)
        if metadata_dir and metadata_dir != agent_dir:
            os.makedirs(metadata_dir, exist_ok=True)
        metadata = {
            "type": config_type,
            "created": datetime.datetime.now().isoformat(),
            "last_updated": datetime.datetime.now().isoformat(),
        }
        with open(metadata_file, "w") as f:
            json.dump(metadata, f, indent=2)

    def _update_metadata(self, agent_dir: str, config_name: str):
        """Update the last_updated field in metadata."""
        metadata_file = os.path.join(agent_dir, f"{config_name}.metadata")
        if os.path.exists(metadata_file):
            try:
                with open(metadata_file) as f:
                    metadata = json.load(f)
                metadata["last_updated"] = datetime.datetime.now().isoformat()
                with open(metadata_file, "w") as f:
                    json.dump(metadata, f, indent=2)
            except Exception as e:
                _log.error(f"Error updating metadata: {e}")

    def _store_json(self, agent_dir: str, config_name: str, config_data: Any) -> bool:
        """Store a JSON configuration."""
        config_file = os.path.join(agent_dir, f"{config_name}")

        try:
            # Create parent directories if config_name contains slashes
            config_dir = os.path.dirname(config_file)
            if config_dir and config_dir != agent_dir:
                os.makedirs(config_dir, exist_ok=True)
            with open(config_file, "w") as f:
                json.dump(config_data, f, indent=2)
            self._update_metadata(agent_dir, config_name)
            return True
        except Exception as e:
            _log.error(f"Error storing JSON config {config_name}: {e}")
            return False

    def _store_csv(self, agent_dir: str, config_name: str, csv_data: Union[str, list[list[str]]]) -> bool:
        """Store a CSV configuration."""
        config_file = os.path.join(agent_dir, f"{config_name}")

        try:
            # Create parent directories if config_name contains slashes
            config_dir = os.path.dirname(config_file)
            if config_dir and config_dir != agent_dir:
                os.makedirs(config_dir, exist_ok=True)
            # If csv_data is a string, write it directly
            if isinstance(csv_data, str):
                with open(config_file, "w", newline="") as f:
                    f.write(csv_data)
            # If csv_data is a list of lists, convert to CSV format
            elif isinstance(csv_data, list):
                with open(config_file, "w", newline="") as f:
                    writer = csv.writer(f)
                    for row in csv_data:
                        writer.writerow(row)
            else:
                raise ValueError(f"Unsupported CSV data type: {type(csv_data)}")

            self._update_metadata(agent_dir, config_name)
            return True
        except Exception as e:
            _log.error(f"Error storing CSV config {config_name}: {e}")
            return False

    def retrieve(
        self, agent_id: str, config_name: str, raw: bool = False, resolve_references: bool = True
    ) -> Any | None:
        """
        Retrieve a configuration entry for an agent.

        Args:
            agent_id: The identity of the agent
            config_name: The name of the configuration
            raw: If True, return the raw file content, otherwise parse based on type
            resolve_references: If True, resolve config:// references (default: True)

        Returns
        -------
            The configuration data or None if not found
        """
        with self.lock:
            agent_dir = os.path.join(self.base_dir, agent_id)
            config_file = os.path.join(agent_dir, config_name)
            metadata_file = os.path.join(agent_dir, f"{config_name}.metadata")

            if not os.path.exists(config_file):
                return None

            # If raw, just return the file contents
            if raw:
                try:
                    with open(config_file) as f:
                        return f.read()
                except Exception as e:
                    _log.error(f"Error reading raw config {config_name}: {e}")
                    return None

            # Otherwise, parse based on type
            config_type = "json"  # Default type

            # Try to get the type from metadata
            if os.path.exists(metadata_file):
                try:
                    with open(metadata_file) as f:
                        metadata = json.load(f)
                    config_type = metadata.get("type", "json")
                except Exception as e:
                    _log.error(f"Error reading metadata for {config_name}: {e}")

            # Parse based on type
            config_data = None
            if config_type == "json":
                config_data = self._retrieve_json(config_file)
            elif config_type == "csv":
                config_data = self._retrieve_csv(config_file)
            else:
                _log.error(f"Unsupported config type: {config_type}")
                return None

            # Resolve config:// references if requested and we have parsed data
            if config_data is not None and resolve_references:
                config_data = self._process_config_links(config_data, agent_id)

            return config_data

    def _retrieve_json(self, config_file: str) -> Any | None:
        """Retrieve a JSON configuration."""
        try:
            with open(config_file) as f:
                return json.load(f)
        except Exception as e:
            _log.error(f"Error retrieving JSON config from {config_file}: {e}")
            return None

    def _retrieve_csv(self, config_file: str) -> list[list[str]] | None:
        """Retrieve a CSV configuration as a list of rows."""
        try:
            with open(config_file, newline="") as f:
                reader = csv.reader(f)
                return list(reader)
        except Exception as e:
            _log.error(f"Error retrieving CSV config from {config_file}: {e}")
            return None

    def exists(self, agent_id: str, config_name: str) -> bool:
        """
        Check if a configuration exists for an agent.

        Args:
            agent_id: The identity of the agent
            config_name: The name of the configuration

        Returns
        -------
            bool: True if the configuration exists, False otherwise
        """
        # Validate inputs to prevent errors
        if not agent_id or not config_name:
            return False

        config_file = os.path.join(self.base_dir, agent_id, config_name)
        return os.path.exists(config_file)

    def list_configs(self, agent_id: str | None = None) -> dict[str, list[dict[str, Any]]]:
        """
        List available configurations.

        Args:
            agent_id: Optional agent identity to filter by

        Returns
        -------
            A dictionary mapping agent IDs to lists of config names and metadata
        """
        with self.lock:
            result = {}

            if agent_id:
                # list configs for a specific agent
                agent_dir = os.path.join(self.base_dir, agent_id)
                if os.path.exists(agent_dir) and os.path.isdir(agent_dir):
                    result[agent_id] = self._list_agent_configs(agent_dir)
            else:
                # list configs for all agents
                if os.path.exists(self.base_dir):
                    agent_dirs = [d for d in os.listdir(self.base_dir) if os.path.isdir(os.path.join(self.base_dir, d))]

                    for agent_dir_name in agent_dirs:
                        agent_dir_path = os.path.join(self.base_dir, agent_dir_name)
                        result[agent_dir_name] = self._list_agent_configs(agent_dir_path)

            return result

    def _list_agent_configs(self, agent_dir: str) -> list[dict[str, Any]]:
        """List configurations for a specific agent directory."""
        configs = []

        # Recursively find all config files
        for root, _dirs, files in os.walk(agent_dir):
            for filename in files:
                # Skip metadata files
                if filename.endswith(".metadata"):
                    continue

                # Get the full path and compute relative path from agent_dir
                full_path = os.path.join(root, filename)
                rel_path = os.path.relpath(full_path, agent_dir)

                # The config name is the relative path
                config_name = rel_path

                metadata_path = os.path.join(agent_dir, f"{config_name}.metadata")

                config_info = {"name": config_name, "type": "json"}  # Default type

                # Try to get metadata if available
                if os.path.exists(metadata_path):
                    try:
                        with open(metadata_path) as f:
                            metadata = json.load(f)
                        config_info.update(metadata)
                    except Exception:
                        pass

                configs.append(config_info)

        return configs

    def delete_config(self, agent_id: str, config_name: str, send_update: bool = True) -> bool:
        """
        Delete a configuration entry for an agent.

        Args:
            agent_id: The identity of the agent
            config_name: The name of the configuration

        Returns
        -------
            bool: True if successful, False otherwise
        """
        with self.lock:
            config_file = os.path.join(self.base_dir, agent_id, config_name)
            metadata_file = os.path.join(self.base_dir, agent_id, f"{config_name}.metadata")

            success = True

            # Delete the config file
            if os.path.exists(config_file):
                try:
                    os.remove(config_file)
                except Exception as e:
                    _log.error(f"Error deleting config {config_name}: {e}")
                    success = False
            else:
                success = False

            # Delete the metadata file if it exists
            if os.path.exists(metadata_file):
                try:
                    os.remove(metadata_file)
                except Exception as e:
                    _log.error(f"Error deleting metadata for {config_name}: {e}")
                    # Don't set success to False here, as long as the main config was deleted

            if success:
                # Log successful deletion at INFO level
                _log.info(f"Config deleted from store: {agent_id}/{config_name}")

                # External deletions should notify agents (emulates vctl config delete behavior)
                full_name = f"{agent_id}/{config_name}"
                self.notify_change(full_name, "DELETE", None, send_update=send_update)

            return success

    def csv_to_json(self, csv_data: Union[str, list[list[str]]]) -> dict[str, Any]:
        """
        Convert CSV data to a JSON object.

        Args:
            csv_data: CSV data as a string or list of rows

        Returns
        -------
            A JSON-compatible dictionary
        """
        # If csv_data is a string, parse it
        if isinstance(csv_data, str):
            reader = csv.reader(io.StringIO(csv_data))
            rows = list(reader)
        else:
            rows = csv_data

        if not rows:
            return {}

        # Use the first row as headers
        headers = rows[0]
        result = []

        # Convert each row to a dictionary
        for row in rows[1:]:
            if len(row) != len(headers):
                # Skip rows that don't match header length
                continue

            row_dict = {}
            for i, header in enumerate(headers):
                # Try to convert values to appropriate types
                value = row[i]
                try:
                    # Try to convert to a number if appropriate
                    if value.lower() == "true":
                        row_dict[header] = True
                    elif value.lower() == "false":
                        row_dict[header] = False
                    elif value.isdigit():
                        row_dict[header] = int(value)
                    elif value.replace(".", "", 1).isdigit() and value.count(".") == 1:
                        row_dict[header] = float(value)
                    else:
                        row_dict[header] = value
                except (ValueError, AttributeError):
                    row_dict[header] = value

            result.append(row_dict)

        return result

    def notify_change(self, config_name: str, action: str, value: Any | None = None, send_update: bool = True):
        """
        Notify agents about configuration changes using RPC calls (VOLTTRON-style).

        Args:
            config_name: Name of the configuration that changed (format: agent_id/config_name)
            action: Type of change ('NEW', 'UPDATE', or 'DELETE')
            value: The new configuration value (None for DELETE actions)
        """
        if self.messagebus is None:
            _log.warning("Config change not published: No message bus provided")
            return

        # Respect send_update flag (matches VOLTTRON behavior)
        if not send_update:
            _log.debug(f"Skipping config update notification for {config_name} (send_update=False)")
            return

        # Ensure we have valid inputs
        if not config_name:
            _log.warning("Config change not published: Missing config name")
            return

        if action not in ("NEW", "UPDATE", "DELETE"):
            _log.warning(f"Config change not published: Invalid action {action}")
            return

        try:
            # Extract agent_id from config_name (format: agent_id/config_name)
            if "/" not in config_name:
                _log.warning(
                    f"Config change not sent: Invalid config name format '{config_name}' "
                    f"(expected agent_id/config_name)"
                )
                return

            agent_id = config_name.split("/", 1)[0]
            config_short_name = config_name.split("/", 1)[1]

            _log.info(f"Sending config update to agent {agent_id}: {config_short_name} ({action})")

            # Get all active connections from the manager
            if not hasattr(self.messagebus, "manager"):
                _log.warning("Config change not sent: Message bus has no manager attribute")
                return

            if not hasattr(self.messagebus.manager, "active_connections"):
                _log.warning("Config change not sent: Manager has no active_connections attribute")
                return

            active_connections = self.messagebus.manager.active_connections

            # Check if the target agent is connected
            if not active_connections or agent_id not in active_connections:
                _log.debug(f"Agent {agent_id} not currently connected. Configuration update not sent.")
                return

            # Create RPC message to call config.update on the target agent
            # This matches VOLTTRON's approach: platform calls config.update RPC method on agents
            message = Message(
                peer=agent_id,  # Target the specific agent
                subsystem="rpc",
                data={
                    "method": "config.update",
                    "args": [action, config_short_name],
                    "kwargs": {
                        "contents": value,
                        "trigger_callback": True,  # Always trigger callbacks for external changes (like vctl config)
                    },
                    "msg_id": str(uuid.uuid4()),
                },
            )

            self.messagebus.send_vip_message(message)
            _log.info(f"Sent config.update RPC call to agent {agent_id}: {config_short_name} ({action})")

        except Exception as e:
            _log.error(f"Error sending config change notification: {e}")

    def _process_config_links(self, config_contents, agent_id, already_resolved=None):
        """
        Process config:// references in configuration data (matching VOLTTRON behavior).

        Args:
            config_contents: Configuration data to process
            agent_id: Agent identity for resolving references
            already_resolved: Set of already resolved references to prevent circular references

        Returns
        -------
            Configuration with resolved references
        """
        if already_resolved is None:
            already_resolved = set()

        # Work with a deep copy to avoid modifying the original
        result = deepcopy(config_contents)

        if isinstance(result, dict):
            for key, value in result.items():
                if isinstance(value, dict | list):
                    result[key] = self._process_config_links(value, agent_id, already_resolved)
                elif isinstance(value, str):
                    config_ref = check_for_config_link(value)
                    if config_ref is not None:
                        resolved_config = self._resolve_config_reference(agent_id, config_ref, already_resolved)
                        result[key] = resolved_config
        elif isinstance(result, list):
            for i, value in enumerate(result):
                if isinstance(value, dict | list):
                    result[i] = self._process_config_links(value, agent_id, already_resolved)
                elif isinstance(value, str):
                    config_ref = check_for_config_link(value)
                    if config_ref is not None:
                        resolved_config = self._resolve_config_reference(agent_id, config_ref, already_resolved)
                        result[i] = resolved_config

        return result

    def _resolve_config_reference(self, agent_id, config_name, already_resolved):
        """
        Resolve a config:// reference to actual configuration data.

        Args:
            agent_id: Agent identity
            config_name: Name of referenced configuration
            already_resolved: Set of already resolved references to prevent circular references

        Returns
        -------
            Resolved configuration data or None if not found
        """
        # Prevent circular references
        if config_name in already_resolved:
            _log.warning(f"Circular config reference detected: {config_name}")
            return None

        already_resolved.add(config_name)

        try:
            # Try to retrieve the referenced configuration
            referenced_config = self.retrieve(agent_id, config_name)
            if referenced_config is None:
                _log.warning(f"Config reference not found: {config_name}")
                return None

            # Recursively process links in the referenced configuration
            return self._process_config_links(referenced_config, agent_id, already_resolved)

        except Exception as e:
            _log.error(f"Error resolving config reference '{config_name}': {e}")
            return None
        finally:
            already_resolved.discard(config_name)

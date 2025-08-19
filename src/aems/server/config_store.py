# config_store.py - Enhanced to support different formats

import csv
import datetime
import io
import json
import logging
import os
import threading
from typing import Any, Optional, Union

from watchdog.observers import Observer

from aems.server.config_store_handler import ConfigFileHandler
from aems.server.models import Message, MessageBus

_log = logging.getLogger(__name__)


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

        # In ConfigStore initialization
        observer = Observer()
        observer.schedule(ConfigFileHandler(self), self.base_dir, recursive=True)
        observer.start()

        _log.info(f"ConfigStore initialized with base directory: {base_dir}")

    def store(self, agent_id: str, config_name: str, config_data: Any, config_type: str = "json") -> bool:
        """
        Store a configuration entry for an agent.

        Args:
            agent_id: The identity of the agent
            config_name: The name of the configuration
            config_data: The configuration data
            config_type: The type of configuration ("json" or "csv")

        Returns:
            bool: True if successful, False otherwise
        """
        with self.lock:
            agent_dir = os.path.join(self.base_dir, agent_id)
            os.makedirs(agent_dir, exist_ok=True)

            # Store metadata about the config
            self._store_metadata(agent_dir, config_name, config_type)

            # Store the actual config data
            if config_type == "json":
                return self._store_json(agent_dir, config_name, config_data)
            elif config_type == "csv":
                return self._store_csv(agent_dir, config_name, config_data)
            else:
                _log.error(f"Unsupported config type: {config_type}")
                return False

    def _store_metadata(self, agent_dir: str, config_name: str, config_type: str):
        """Store metadata about a configuration."""
        metadata_file = os.path.join(agent_dir, f"{config_name}.metadata")
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
                with open(metadata_file, "r") as f:
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

    def retrieve(self, agent_id: str, config_name: str, raw: bool = False) -> Optional[Any]:
        """
        Retrieve a configuration entry for an agent.

        Args:
            agent_id: The identity of the agent
            config_name: The name of the configuration
            raw: If True, return the raw file content, otherwise parse based on type

        Returns:
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
                    with open(config_file, "r") as f:
                        return f.read()
                except Exception as e:
                    _log.error(f"Error reading raw config {config_name}: {e}")
                    return None

            # Otherwise, parse based on type
            config_type = "json"  # Default type

            # Try to get the type from metadata
            if os.path.exists(metadata_file):
                try:
                    with open(metadata_file, "r") as f:
                        metadata = json.load(f)
                    config_type = metadata.get("type", "json")
                except Exception as e:
                    _log.error(f"Error reading metadata for {config_name}: {e}")

            # Parse based on type
            if config_type == "json":
                return self._retrieve_json(config_file)
            elif config_type == "csv":
                return self._retrieve_csv(config_file)
            else:
                _log.error(f"Unsupported config type: {config_type}")
                return None

    def _retrieve_json(self, config_file: str) -> Optional[Any]:
        """Retrieve a JSON configuration."""
        try:
            with open(config_file, "r") as f:
                return json.load(f)
        except Exception as e:
            _log.error(f"Error retrieving JSON config from {config_file}: {e}")
            return None

    def _retrieve_csv(self, config_file: str) -> Optional[list[list[str]]]:
        """Retrieve a CSV configuration as a list of rows."""
        try:
            with open(config_file, "r", newline="") as f:
                reader = csv.reader(f)
                return [row for row in reader]
        except Exception as e:
            _log.error(f"Error retrieving CSV config from {config_file}: {e}")
            return None

    def list_configs(self, agent_id: Optional[str] = None) -> dict[str, list[dict[str, Any]]]:
        """
        list available configurations.

        Args:
            agent_id: Optional agent identity to filter by

        Returns:
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
        """list configurations for a specific agent directory."""
        configs = []

        # Get all files that don't end with .metadata
        all_files = [
            f
            for f in os.listdir(agent_dir)
            if os.path.isfile(os.path.join(agent_dir, f)) and not f.endswith(".metadata")
        ]

        for filename in all_files:
            metadata_path = os.path.join(agent_dir, f"{filename}.metadata")

            config_info = {"name": filename, "type": "json"}  # Default type

            # Try to get metadata if available
            if os.path.exists(metadata_path):
                try:
                    with open(metadata_path, "r") as f:
                        metadata = json.load(f)
                    config_info.update(metadata)
                except Exception:
                    pass

            configs.append(config_info)

        return configs

    def delete_config(self, agent_id: str, config_name: str) -> bool:
        """
        Delete a configuration entry for an agent.

        Args:
            agent_id: The identity of the agent
            config_name: The name of the configuration

        Returns:
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

            return success

    def csv_to_json(self, csv_data: Union[str, list[list[str]]]) -> dict[str, Any]:
        """
        Convert CSV data to a JSON object.

        Args:
            csv_data: CSV data as a string or list of rows

        Returns:
            A JSON-compatible dictionary
        """
        # If csv_data is a string, parse it
        if isinstance(csv_data, str):
            reader = csv.reader(io.StringIO(csv_data))
            rows = [row for row in reader]
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

    def notify_change(self, config_name: str, action: str, value: Optional[Any] = None):
        """
        Notify subscribers about configuration changes using VIP messages.

        Args:
            config_name: Name of the configuration that changed
            action: Type of change ('NEW', 'UPDATE', or 'DELETE')
            value: The new configuration value (None for DELETE actions)
        """
        if self.messagebus is None:
            _log.warning("Config change not published: No message bus provided")
            return

        try:
            # Create payload
            payload = {
                "name": config_name,
                "action": action,
                "timestamp": datetime.datetime.now().isoformat(),
            }

            # Include value for non-DELETE actions
            if action != "DELETE" and value is not None:
                payload["value"] = value

            _log.info(f"Publishing config change: {config_name} ({action})")

            # Format topic for easier subscription matching
            topic = f"config/{config_name}"

            # Get all active connections from the manager
            if hasattr(self.messagebus, "manager") and hasattr(self.messagebus.manager, "active_connections"):  # type: ignore
                # Create and send a message to each connected client
                for client_id in self.messagebus.manager.active_connections:  # type: ignore  The implemented messagebus has this.
                    message = Message(
                        peer=client_id,  # Target specific client
                        subsystem="pubsub",
                        data={
                            "topic": topic,
                            "headers": {},
                            "message": payload,
                            "sender": "configstore",
                        },
                    )
                    self.messagebus.send_vip_message(message)
            else:
                _log.warning("Cannot publish config change: No active connections found")

        except Exception as e:
            _log.error(f"Error publishing config change notification: {e}")

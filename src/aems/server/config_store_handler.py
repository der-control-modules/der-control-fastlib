from watchdog.events import FileSystemEventHandler


class ConfigFileHandler(FileSystemEventHandler):
    def __init__(self, config_store):
        self.config_store = config_store

    def on_created(self, event):
        if not event.is_directory:
            name = self._get_config_name(event.src_path)
            if name:  # Only process if we got a valid config name
                value = self._load_config_file(event.src_path)
                # File changes should notify agents (like external vctl config operations)
                self.config_store.notify_change(name, "NEW", value)

    def on_modified(self, event):
        if not event.is_directory:
            name = self._get_config_name(event.src_path)
            if name:  # Only process if we got a valid config name
                value = self._load_config_file(event.src_path)
                # File changes should notify agents (like external vctl config operations)
                self.config_store.notify_change(name, "UPDATE", value)

    def on_deleted(self, event):
        if not event.is_directory:
            name = self._get_config_name(event.src_path)
            if name:  # Only process if we got a valid config name
                # File deletions should notify agents (like external vctl config operations)
                self.config_store.notify_change(name, "DELETE", None)

    def _get_config_name(self, path):
        """Extract config name from file path.

        Expected path format: /base/dir/agent_id/config_name.json
        Returns: agent_id/config_name
        """
        import os

        try:
            # Get the filename without extension
            filename = os.path.basename(path)
            name_without_ext = os.path.splitext(filename)[0]

            # Skip metadata files
            if filename.endswith(".metadata"):
                return None

            # Get the parent directory (agent_id)
            parent_dir = os.path.basename(os.path.dirname(path))

            # Return agent_id/config_name format
            return f"{parent_dir}/{name_without_ext}"
        except Exception:
            return None

    def _load_config_file(self, path):
        """Load and parse config file.

        Supports JSON, CSV, and YAML formats.
        """
        import json
        import os

        try:
            filename = os.path.basename(path)
            extension = os.path.splitext(filename)[1].lower()

            with open(path, encoding="utf-8") as f:
                if extension == ".json":
                    return json.load(f)
                elif extension == ".csv":
                    # For CSV files, return the raw content for now
                    # The config store will handle CSV parsing
                    return f.read()
                elif extension in [".yaml", ".yml"]:
                    try:
                        import yaml

                        return yaml.safe_load(f)
                    except ImportError:
                        # Fall back to raw content if yaml not available
                        return f.read()
                else:
                    # For unknown file types, return raw content
                    return f.read()
        except Exception:
            return None

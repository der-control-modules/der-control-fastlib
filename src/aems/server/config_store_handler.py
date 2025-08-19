from watchdog.events import FileSystemEventHandler


class ConfigFileHandler(FileSystemEventHandler):
    def __init__(self, config_store):
        self.config_store = config_store

    def on_created(self, event):
        if not event.is_directory:
            name = self._get_config_name(event.src_path)
            value = self._load_config_file(event.src_path)
            self.config_store.notify_change(name, "NEW", value)

    def on_modified(self, event):
        if not event.is_directory:
            name = self._get_config_name(event.src_path)
            value = self._load_config_file(event.src_path)
            self.config_store.notify_change(name, "UPDATE", value)

    def on_deleted(self, event):
        if not event.is_directory:
            name = self._get_config_name(event.src_path)
            self.config_store.notify_change(name, "DELETE", None)

    def _get_config_name(self, path):
        # Extract config name from file path
        # Implementation depends on your file naming scheme
        pass

    def _load_config_file(self, path):
        # Load and parse config file
        # Implementation depends on your file format
        pass

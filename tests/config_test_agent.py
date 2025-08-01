# config_test_agent.py - Updated with cron example

from aems.client.agent import Agent, Core, RPC, AsyncResult
import gevent
import datetime
import json


class ConfigTestAgent(Agent):
    """
    An agent that demonstrates the ConfigStore functionality.
    """

    def __init__(self, identity="config_test", **kwargs):
        # First initialize the base Agent class
        super().__init__(identity=identity, **kwargs)

        # Then initialize our own attributes
        self.default_config = {
            "interval": 60,
            "threshold": 100,
            "enabled": True,
            "targets": ["device1", "device2"],
            "nested": {"setting1": "value1", "setting2": "value2"},
            "use_cron": False,
            "cron_schedule": "*/5 * * * *",  # Every 5 minutes by default
        }

        # Current active configuration
        self._config = self.default_config.copy()

        # Last time the configuration was updated
        self._last_update = None

        # Status information
        self._status = {
            "startup_time": datetime.datetime.now().isoformat(),
            "config_updates": 0,
            "config_errors": 0,
            "last_update": None,
            "process_count": 0,
        }

    @Core.receiver("onstart")
    def _onstart(self, sender=None, **kwargs):
        """Handle startup tasks."""
        print(f"{self.identity} agent starting...")

        # Watch for configuration changes
        self.config.watch("config", self._config_updated)

        # Schedule initial processing task (config should be loaded via onconfigure)
        self._schedule_processing_task()

        # Schedule a daily report task (won't change with config)
        self.core.schedule(
            self._daily_report, "0 0 * * *", name="daily_report"  # Midnight every day
        )

        print(f"{self.identity} agent started!")

    @Core.receiver("onstop")
    def _onstop(self, sender=None, **kwargs):
        """Handle shutdown tasks."""
        print(f"{self.identity} agent stopping...")

    @Core.periodic(10)  # Default to 10 second interval until config is loaded
    def _process_data(self):
        """Periodic task to process data based on configuration."""
        if not hasattr(self, "_config") or not self._config.get("enabled", False):
            return  # Skip processing if not configured or disabled

        # Only process if enabled
        current_time = datetime.datetime.now().isoformat()
        targets = self._config.get("targets", [])
        threshold = self._config.get("threshold", 100)

        print(f"[{current_time}] Processing {len(targets)} targets with threshold {threshold}")
        for target in targets:
            print(f"  - Processing target: {target}")

        # Update status
        self._status["process_count"] += 1
        self._status["last_process_time"] = current_time

        # Check if we need to switch between interval and cron scheduling
        use_cron = self._config.get("use_cron", False)
        current_tasks = self.core.list_events()

        if use_cron:
            # If we're now using cron but we have an interval-based task, switch to cron
            if "_process_data" in current_tasks and "interval" in current_tasks["_process_data"]:
                print("Switching from interval to cron scheduling")
                self.core.cancel("_process_data")
                cron_schedule = self._config.get("cron_schedule", "*/5 * * * *")
                self.core.schedule(self._process_data, cron_schedule, name="_process_data")
                print(f"Now using cron schedule: {cron_schedule}")
        else:
            # If we're using interval but have a cron-based task, switch to interval
            if "_process_data" in current_tasks and "cron" in current_tasks["_process_data"]:
                print("Switching from cron to interval scheduling")
                self.core.cancel("_process_data")
                interval = self._config.get("interval", 60)
                self.core.schedule(self._process_data, interval, name="_process_data")
                print(f"Now using interval: {interval} seconds")
            elif "_process_data" in current_tasks:
                # Using interval - update it if needed
                configured_interval = self._config.get("interval", 60)
                current_interval = current_tasks["_process_data"].get("interval", 0)
                if current_interval != configured_interval:
                    print(
                        f"Updating processing interval from {current_interval} to {configured_interval}"
                    )
                    self.core.update_interval("_process_data", configured_interval)

    @Core.receiver("onconfigure")
    def _onconfigure(self, sender=None, configs=None, **kwargs):
        """
        Handle configuration loading during startup.
        This is called after connection but before onstart.
        """
        print(f"{self.identity} agent configuring...")
        print(f"Available configs: {configs}")

        # Try to get the main configuration
        try:
            config_future = self.config.get("config")
            config = config_future.get(timeout=5)

            if config:
                print(f"Loaded configuration from config store: {config}")
                self._apply_config(config)
            else:
                print("No configuration found in config store, using defaults")
                # Store the default configuration
                self.config.set("config", self.default_config).get(timeout=5)
                print("Default configuration stored in config store")
        except Exception as e:
            print(f"Error loading configuration: {e}")
            self._status["config_errors"] += 1

        print(f"{self.identity} agent configured!")

    def _daily_report(self):
        """Generate a daily report (cron-based task)."""
        current_time = datetime.datetime.now().isoformat()
        print(f"[{current_time}] Generating daily report")
        print(f"  - Process count: {self._status['process_count']}")
        print(f"  - Config updates: {self._status['config_updates']}")
        print(f"  - Config errors: {self._status['config_errors']}")

    def _load_config(self):
        """Load configuration from config store."""
        try:
            # Try to get the configuration from the config store
            config_future = self.config.get("config")
            config = config_future.get(timeout=5)

            if config:
                print(f"Loaded configuration from config store: {config}")
                self._apply_config(config)
            else:
                print("No configuration found in config store, using defaults")
                # Store the default configuration
                self.config.set("config", self.default_config).get(timeout=5)
                print("Default configuration stored in config store")
        except Exception as e:
            print(f"Error loading configuration: {e}")
            self._status["config_errors"] += 1

    def _config_updated(self, config_name, config_data):
        """Handle configuration updates."""
        if config_name == "config":
            if config_data is None:
                print("Configuration was deleted, reverting to defaults")
                self._apply_config(self.default_config.copy())
                # Re-store the default configuration
                self.config.set("config", self.default_config).get(timeout=5)
            else:
                print(f"Configuration updated: {config_data}")
                self._apply_config(config_data)

    def _apply_config(self, config):
        """Apply configuration changes."""
        try:
            # Make sure all required fields are present
            required_fields = ["interval", "threshold", "enabled", "targets"]
            for field in required_fields:
                if field not in config:
                    raise ValueError(f"Missing required field: {field}")

            # Update the configuration
            self._config = config
            self._last_update = datetime.datetime.now().isoformat()
            self._status["config_updates"] += 1
            self._status["last_update"] = self._last_update

            print(f"Applied new configuration: {self._config}")

            # Update scheduling based on configuration
            use_cron = config.get("use_cron", False)
            current_tasks = self.core.list_events()

            if "_process_data" in current_tasks:
                if use_cron:
                    # Switch to cron or update cron schedule
                    cron_schedule = config.get("cron_schedule", "*/5 * * * *")
                    if "cron" in current_tasks["_process_data"]:
                        # Already using cron, check if schedule changed
                        current_cron = current_tasks["_process_data"]["cron"]
                        if current_cron != cron_schedule:
                            print(f"Updating cron schedule from {current_cron} to {cron_schedule}")
                            self.core.update_cron("_process_data", cron_schedule)
                    else:
                        # Switch from interval to cron
                        print(f"Switching from interval to cron schedule: {cron_schedule}")
                        self.core.cancel("_process_data")
                        self.core.schedule(self._process_data, cron_schedule, name="_process_data")
                else:
                    # Switch to interval or update interval
                    interval = config.get("interval", 60)
                    if "interval" in current_tasks["_process_data"]:
                        # Already using interval, check if it changed
                        current_interval = current_tasks["_process_data"]["interval"]
                        if current_interval != interval:
                            print(f"Updating interval from {current_interval} to {interval}")
                            self.core.update_interval("_process_data", interval)
                    else:
                        # Switch from cron to interval
                        print(f"Switching from cron to interval: {interval}")
                        self.core.cancel("_process_data")
                        self.core.schedule(self._process_data, interval, name="_process_data")

        except Exception as e:
            print(f"Error applying configuration: {e}")
            self._status["config_errors"] += 1

    @RPC.export
    def get_config(self):
        """RPC method to get the current configuration."""
        return self._config

    @RPC.export
    def set_config_value(self, key, value):
        """RPC method to set a specific configuration value."""
        try:
            # Create a copy of the current config
            new_config = self._config.copy()

            # Update the value (supporting nested paths like "nested.setting1")
            keys = key.split(".")
            target = new_config
            for k in keys[:-1]:
                if k not in target or not isinstance(target[k], dict):
                    target[k] = {}
                target = target[k]
            target[keys[-1]] = value

            # Store the updated config
            result = self.config.set("config", new_config).get(timeout=5)
            return {"success": True, "message": f"Updated {key} to {value}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @RPC.export
    def reset_config(self):
        """RPC method to reset the configuration to defaults."""
        try:
            result = self.config.set("config", self.default_config.copy()).get(timeout=5)
            return {"success": True, "message": "Configuration reset to defaults"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @RPC.export
    def switch_to_cron(self, cron_schedule=None):
        """RPC method to switch to cron-based scheduling."""
        try:
            new_config = self._config.copy()
            new_config["use_cron"] = True
            if cron_schedule:
                new_config["cron_schedule"] = cron_schedule

            result = self.config.set("config", new_config).get(timeout=5)
            return {
                "success": True,
                "message": f"Switched to cron scheduling with expression: {new_config['cron_schedule']}",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @RPC.export
    def switch_to_interval(self, interval=None):
        """RPC method to switch to interval-based scheduling."""
        try:
            new_config = self._config.copy()
            new_config["use_cron"] = False
            if interval is not None:
                new_config["interval"] = interval

            result = self.config.set("config", new_config).get(timeout=5)
            return {
                "success": True,
                "message": f"Switched to interval scheduling with interval: {new_config['interval']} seconds",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @RPC.export
    def get_status(self):
        """RPC method to get the agent's status."""
        self._status["current_time"] = datetime.datetime.now().isoformat()
        self._status["scheduled_events"] = self.core.list_events()
        return self._status

    def _schedule_processing_task(self):
        """Schedule or reschedule the processing task based on current config."""
        # Cancel any existing task
        if self._process_task_name:
            self.core.cancel(self._process_task_name)

        # Skip scheduling if the agent is disabled
        if not self._config.get("enabled", True):
            print("Agent is disabled, not scheduling processing task")
            return

        # Choose scheduling mode based on configuration
        use_cron = self._config.get("use_cron", False)

        if use_cron:
            # Use cron-based scheduling
            cron_schedule = self._config.get("cron_schedule", "*/5 * * * *")
            task_name = self.core.schedule(self._process_data, cron_schedule, name="process_data")
            print(f"Scheduled processing with cron expression: {cron_schedule}")
        else:
            # Use interval-based scheduling
            interval = self._config.get("interval", 60)
            task_name = self.core.schedule(self._process_data, interval, name="process_data")
            print(f"Scheduled processing with interval: {interval} seconds")

        # Remember the task name for later updates
        self._process_task_name = task_name

    def _process_data(self):
        """Task to process data based on configuration."""
        if not self._config.get("enabled", False):
            print("Processing skipped - agent is disabled")
            return  # Skip processing if disabled

        # Process data
        current_time = datetime.datetime.now().isoformat()
        targets = self._config.get("targets", [])
        threshold = self._config.get("threshold", 100)

        print(f"[{current_time}] Processing {len(targets)} targets with threshold {threshold}")
        for target in targets:
            print(f"  - Processing target: {target}")

        # Update status
        self._status["process_count"] += 1
        self._status["last_process_time"] = current_time


if __name__ == "__main__":
    from agent import run_agent
    import sys

    sys.exit(run_agent(ConfigTestAgent))

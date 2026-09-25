#!/usr/bin/env python3
"""
Test that the config store list method correctly returns cached configurations.
This test verifies that the list method returns configs from the cache instead of querying the server.
"""

import gevent
import pytest

from derhost.client.agent import Agent


class ConfigListTestAgent(Agent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.configs_received = {}

    def config_callback(self, config_name, action, config_value):
        """Callback for configuration updates."""
        print(f"Config callback received: {config_name} ({action}) = {config_value}")
        self.configs_received[config_name] = config_value


def test_config_list_from_cache(message_bus_manager_fixture):
    """Test that the list method returns configurations from the cache."""
    print("Testing that list method returns configurations from the cache...")

    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("list_test_agent", ConfigListTestAgent)

    try:
        print("1. Connecting agent...")
        agent.connect()
        gevent.sleep(1)  # Wait for connection

        print("2. Setting default configurations (local only)...")
        # Set default configurations (these go into the cache but not to the server)
        default_configs = {
            "default_config1": {"source": "default", "value": 1},
            "default_config2": {"source": "default", "value": 2},
        }

        for name, value in default_configs.items():
            agent.config.set_default(name, value)

        print("3. Setting server configurations...")
        # Set server configurations (these go to both the server and the cache)
        server_configs = {
            "server_config1": {"source": "server", "value": 3},
            "server_config2": {"source": "server", "value": 4},
        }

        for name, value in server_configs.items():
            agent.config.set(name, value).get(timeout=5)

        # Allow time for the configurations to be processed
        gevent.sleep(2)

        print("4. Getting configurations list from cache...")
        # Get the list of configurations (should come from cache)
        config_list_result = agent.config.list()
        config_list = config_list_result.get(timeout=5)

        print(f"5. Configuration list: {config_list}")

        # Verify that all configurations are listed
        for config_name in list(default_configs.keys()) + list(server_configs.keys()):
            assert (
                config_name in config_list
            ), f"Config '{config_name}' should be in the list but was not found"

        # Verify that the configs can be retrieved from the cache
        for config_name in config_list:
            config_value = agent.config.get(config_name)
            print(f"Retrieved {config_name}: {config_value}")

            # If it's a default config, verify it matches the default value
            if config_name in default_configs:
                assert (
                    config_value == default_configs[config_name]
                ), f"Default config {config_name} value doesn't match expected"

            # If it's a server config, verify it matches the server value
            if config_name in server_configs:
                assert (
                    config_value == server_configs[config_name]
                ), f"Server config {config_name} value doesn't match expected"

        print("✅ SUCCESS: List method returned all configurations from the cache!")

    finally:
        if hasattr(agent, "connected") and agent.connected:
            agent.disconnect()


def test_merged_config_in_list(message_bus_manager_fixture):
    """Test that configs with both default and server values are correctly merged in the list."""
    print("Testing that merged configurations appear correctly in the list...")

    manager = message_bus_manager_fixture
    manager.start_bus()

    agent = manager.create_agent("merged_list_test_agent", ConfigListTestAgent)

    try:
        print("1. Connecting agent...")
        agent.connect()
        gevent.sleep(1)  # Wait for connection

        print("2. Setting a default configuration...")
        # Set a default configuration
        default_value = {
            "source": "default",
            "only_in_default": True,
            "shared_key": "default_value",
        }
        agent.config.set_default("merged_config", default_value)

        print("3. Setting a server configuration with the same name...")
        # Set a server configuration with the same name (should merge with default)
        server_value = {
            "source": "server",
            "only_in_server": True,
            "shared_key": "server_value",
        }
        agent.config.set("merged_config", server_value).get(timeout=5)

        # Allow time for the configurations to be processed
        gevent.sleep(2)

        print("4. Getting configurations list from cache...")
        # Get the list of configurations
        config_list_result = agent.config.list()
        config_list = config_list_result.get(timeout=5)

        print(f"5. Configuration list: {config_list}")

        # Verify that the merged config is in the list
        assert "merged_config" in config_list, "Merged config should be in the list"

        # Get the merged config and verify it has the correct values
        merged_config = agent.config.get("merged_config")
        print(f"Merged config: {merged_config}")

        # The merged config should have values from both default and server,
        # with server values taking precedence for overlapping keys
        assert (
            merged_config["source"] == "server"
        ), "Server value should override default for 'source'"
        assert (
            merged_config["only_in_default"] is True
        ), "Value from default should be present"
        assert (
            merged_config["only_in_server"] is True
        ), "Value from server should be present"
        assert (
            merged_config["shared_key"] == "server_value"
        ), "Server value should override default for 'shared_key'"

        print("✅ SUCCESS: Merged configuration was correctly returned!")

    finally:
        if hasattr(agent, "connected") and agent.connected:
            agent.disconnect()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

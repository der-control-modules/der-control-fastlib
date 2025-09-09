"""
Test config:// reference resolution functionality.
"""

import pytest
import requests


class TestConfigReferences:
    """Test VOLTTRON-style config:// reference resolution."""

    @pytest.fixture(autouse=True)
    def setup_test(self, message_bus_manager_fixture):
        """Set up test environment."""
        self.manager = message_bus_manager_fixture
        self.manager.start_bus()

        # Create unique test ID
        import uuid

        self.test_id = str(uuid.uuid4())[:8]

        # Store the base URL
        self.base_url = f"http://localhost:{self.manager.port}/config-store"

        yield

        # Cleanup
        for attr in ["test_agent"]:
            if hasattr(self, attr):
                agent = getattr(self, attr)
                if hasattr(agent, "disconnect"):
                    agent.disconnect()

    def test_config_reference_basic(self):
        """Test basic config:// reference resolution."""
        agent_id = f"ref_test_agent_{self.test_id}"

        # Create test agent
        self.test_agent = self.manager.create_connected_agent(agent_id)

        # Create a referenced configuration (database config)
        db_config = {"host": "localhost", "port": 5432, "database": "testdb", "username": "testuser"}

        # Store the database config
        url = f"{self.base_url}/{agent_id}/database"
        response = requests.put(url, json=db_config)
        assert response.status_code == 200

        # Create main configuration that references the database config
        main_config = {"app_name": "test_app", "database_config": "config://database", "debug": True}

        # Store the main config
        url = f"{self.base_url}/{agent_id}/main"
        response = requests.put(url, json=main_config)
        assert response.status_code == 200

        # Retrieve the main config with reference resolution (default)
        url = f"{self.base_url}/{agent_id}/main"
        response = requests.get(url)
        assert response.status_code == 200

        resolved_config = response.json()["data"]

        # The database_config should be resolved to the actual database config
        assert resolved_config["app_name"] == "test_app"
        assert resolved_config["debug"] is True
        assert resolved_config["database_config"] == db_config  # Should be resolved

        # Test without reference resolution
        url = f"{self.base_url}/{agent_id}/main?resolve_references=false"
        response = requests.get(url)
        assert response.status_code == 200

        unresolved_config = response.json()["data"]

        # The database_config should remain as a reference string
        assert unresolved_config["database_config"] == "config://database"

    def test_config_reference_nested(self):
        """Test nested config:// references."""
        agent_id = f"nested_ref_agent_{self.test_id}"

        # Create test agent
        self.test_agent = self.manager.create_connected_agent(agent_id)

        # Create base config
        base_config = {"timeout": 30, "retries": 3}

        # Store base config
        url = f"{self.base_url}/{agent_id}/base"
        response = requests.put(url, json=base_config)
        assert response.status_code == 200

        # Create settings config that references base
        settings_config = {"environment": "test", "base_settings": "config://base", "additional_timeout": 60}

        # Store settings config
        url = f"{self.base_url}/{agent_id}/settings"
        response = requests.put(url, json=settings_config)
        assert response.status_code == 200

        # Create main config that references settings
        main_config = {"app": "nested_test", "config": "config://settings"}

        # Store main config
        url = f"{self.base_url}/{agent_id}/main"
        response = requests.put(url, json=main_config)
        assert response.status_code == 200

        # Retrieve with resolution
        url = f"{self.base_url}/{agent_id}/main"
        response = requests.get(url)
        assert response.status_code == 200

        resolved_config = response.json()["data"]

        # Check nested resolution
        assert resolved_config["app"] == "nested_test"
        config_section = resolved_config["config"]
        assert config_section["environment"] == "test"
        assert config_section["additional_timeout"] == 60

        # The base_settings should be resolved
        base_section = config_section["base_settings"]
        assert base_section["timeout"] == 30
        assert base_section["retries"] == 3

    def test_config_reference_missing(self):
        """Test handling of missing config references."""
        agent_id = f"missing_ref_agent_{self.test_id}"

        # Create test agent
        self.test_agent = self.manager.create_connected_agent(agent_id)

        # Create config with reference to non-existent config
        main_config = {"app": "test_app", "missing_ref": "config://nonexistent"}

        # Store the main config
        url = f"{self.base_url}/{agent_id}/main"
        response = requests.put(url, json=main_config)
        assert response.status_code == 200

        # Retrieve with resolution
        url = f"{self.base_url}/{agent_id}/main"
        response = requests.get(url)
        assert response.status_code == 200

        resolved_config = response.json()["data"]

        # Missing references should resolve to None (VOLTTRON behavior)
        assert resolved_config["app"] == "test_app"
        assert resolved_config["missing_ref"] is None

    def test_config_reference_in_list(self):
        """Test config:// references inside lists."""
        agent_id = f"list_ref_agent_{self.test_id}"

        # Create test agent
        self.test_agent = self.manager.create_connected_agent(agent_id)

        # Create referenced configs
        config1 = {"name": "service1", "port": 8001}
        config2 = {"name": "service2", "port": 8002}

        # Store referenced configs
        requests.put(f"{self.base_url}/{agent_id}/service1", json=config1)
        requests.put(f"{self.base_url}/{agent_id}/service2", json=config2)

        # Create main config with references in a list
        main_config = {"services": ["config://service1", "config://service2"]}

        # Store main config
        url = f"{self.base_url}/{agent_id}/main"
        response = requests.put(url, json=main_config)
        assert response.status_code == 200

        # Retrieve with resolution
        url = f"{self.base_url}/{agent_id}/main"
        response = requests.get(url)
        assert response.status_code == 200

        resolved_config = response.json()["data"]

        # Check that list references are resolved
        services = resolved_config["services"]
        assert len(services) == 2
        assert services[0] == config1
        assert services[1] == config2

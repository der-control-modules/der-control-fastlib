#!/usr/bin/env python3
"""
Test hierarchical config names with slashes (e.g., devices/building1/archive).
"""

import pytest
import requests


class TestHierarchicalConfigNames:
    """Test that config store supports hierarchical names like devices/building1/archive."""

    @pytest.fixture(autouse=True)
    def setup_test(self, message_bus_manager_fixture):
        """Set up test environment."""
        self.manager = message_bus_manager_fixture
        self.manager.start_bus()
        self.base_url = f"http://localhost:{self.manager.port}/config-store"

    def test_store_and_retrieve_hierarchical_config(self):
        """Test storing and retrieving a config with hierarchical name."""
        agent_id = "test_agent"
        config_name = "devices/building1/archive"
        config_data = {"enabled": True, "retention_days": 30, "path": "/var/archive"}

        # Store the config
        url = f"{self.base_url}/{agent_id}/{config_name}"
        response = requests.put(url, json=config_data)
        assert response.status_code == 200

        # Retrieve the config
        response = requests.get(url)
        assert response.status_code == 200
        retrieved = response.json()["data"]
        assert retrieved == config_data

    def test_multiple_hierarchical_configs(self):
        """Test storing multiple configs with hierarchical names."""
        agent_id = "test_agent"
        configs = {
            "devices/building1/archive": {"retention": 30},
            "devices/building1/control": {"mode": "auto"},
            "devices/building2/archive": {"retention": 60},
            "devices/building2/control": {"mode": "manual"},
            "config": {"general": "settings"},
        }

        # Store all configs
        for config_name, config_data in configs.items():
            url = f"{self.base_url}/{agent_id}/{config_name}"
            response = requests.put(url, json=config_data)
            assert response.status_code == 200

        # List all configs
        response = requests.get(f"{self.base_url}/list?agent_id={agent_id}")
        assert response.status_code == 200
        config_list = response.json()["data"][agent_id]

        # Verify all configs are listed
        config_names = [c["name"] for c in config_list]
        for expected_name in configs.keys():
            assert expected_name in config_names

        # Retrieve and verify each config
        for config_name, expected_data in configs.items():
            url = f"{self.base_url}/{agent_id}/{config_name}"
            response = requests.get(url)
            assert response.status_code == 200
            assert response.json()["data"] == expected_data

    def test_delete_hierarchical_config(self):
        """Test deleting a hierarchical config."""
        agent_id = "test_agent"
        config_name = "devices/building1/archive"
        config_data = {"enabled": True}

        # Store the config
        url = f"{self.base_url}/{agent_id}/{config_name}"
        response = requests.put(url, json=config_data)
        assert response.status_code == 200

        # Verify it exists
        response = requests.get(url)
        assert response.status_code == 200

        # Delete it
        response = requests.delete(url)
        assert response.status_code == 200

        # Verify it's gone
        response = requests.get(url)
        assert response.status_code == 404

    def test_hierarchical_config_with_csv(self):
        """Test hierarchical config names with CSV format."""
        agent_id = "test_agent"
        config_name = "devices/building1/points"
        csv_data = "Point,Type,Unit\nTemp,Analog,F\nPressure,Analog,PSI"

        # Store as CSV
        url = f"{self.base_url}/{agent_id}/{config_name}"
        response = requests.put(
            url, data=csv_data, headers={"Content-Type": "text/csv"}
        )
        assert response.status_code == 200

        # Retrieve as CSV (raw response needs special handling)
        response = requests.get(url, params={"raw": "true"})
        assert response.status_code == 200
        # The raw response is wrapped in JSON with status/data
        raw_data = response.json()["data"]
        assert raw_data == csv_data

        # Retrieve as parsed data (CSV is returned as list of rows)
        response = requests.get(url)
        assert response.status_code == 200
        parsed = response.json()["data"]
        # CSV returns list of rows: [header_row, data_row1, data_row2]
        assert len(parsed) == 3
        assert parsed[0] == ["Point", "Type", "Unit"]
        assert parsed[1] == ["Temp", "Analog", "F"]
        assert parsed[2] == ["Pressure", "Analog", "PSI"]

    def test_deep_hierarchy(self):
        """Test deeply nested hierarchical config names."""
        agent_id = "test_agent"
        config_name = "a/b/c/d/e/f/config"
        config_data = {"deep": "nested", "value": 42}

        # Store the config
        url = f"{self.base_url}/{agent_id}/{config_name}"
        response = requests.put(url, json=config_data)
        assert response.status_code == 200

        # Retrieve the config
        response = requests.get(url)
        assert response.status_code == 200
        assert response.json()["data"] == config_data

        # Verify it appears in the list
        response = requests.get(f"{self.base_url}/list?agent_id={agent_id}")
        assert response.status_code == 200
        config_names = [c["name"] for c in response.json()["data"][agent_id]]
        assert config_name in config_names

    def test_hierarchical_config_exists_check(self):
        """Test exists check for hierarchical configs."""
        agent_id = "test_agent"
        config_name = "devices/building1/config"
        config_data = {"test": "data"}

        # Verify it doesn't exist initially
        url = f"{self.base_url}/{agent_id}/{config_name}"
        response = requests.get(url)
        assert response.status_code == 404

        # Store the config
        response = requests.put(url, json=config_data)
        assert response.status_code == 200

        # Verify it exists now
        response = requests.get(url)
        assert response.status_code == 200
        assert response.json()["data"] == config_data

    def test_very_deep_hierarchy(self):
        """Test very deeply nested hierarchical config name as requested."""
        agent_id = "test_agent"
        config_name = "devices/building/status/point/data/version"
        config_data = {
            "version": "1.0.0",
            "timestamp": "2025-10-06",
            "status": "active",
        }

        # Store the config
        url = f"{self.base_url}/{agent_id}/{config_name}"
        response = requests.put(url, json=config_data)
        assert response.status_code == 200

        # Retrieve the config
        response = requests.get(url)
        assert response.status_code == 200
        retrieved = response.json()["data"]
        assert retrieved == config_data

        # Verify it appears in the list
        response = requests.get(f"{self.base_url}/list?agent_id={agent_id}")
        assert response.status_code == 200
        config_names = [c["name"] for c in response.json()["data"][agent_id]]
        assert config_name in config_names


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

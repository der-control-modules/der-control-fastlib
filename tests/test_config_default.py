import gevent
import pytest

from aems.client.agent import Agent, run_agent


class ConfigListTestAgent(Agent):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.configs_received = {}

        abc = {"def": "ghi"}

        # Registers the configstore for the pattern 'config' ('config is default config entry')
        self.vip.config.subscribe(self.update_default, actions=["NEW", "UPDATE"], pattern="config")

        self.vip.config.set_default("config", abc)

    def config_callback(self, config_name, action, config_value):
        """Callback for configuration updates."""
        print(f"Config callback received: {config_name} ({action}) = {config_value}")
        self.configs_received[config_name] = config_value


# class RPCTestAgent(Agent):

#     def __init__(self, *args, **kwargs):
#         super().__init__(*args, **kwargs)

#         self.rpc_requests = {}

#     def rpc_callback(self, request_id, action, request_data):
#         """Callback for RPC requests."""
#         print(f"RPC callback received: {request_id} ({action}) = {request_data}")
#         self.rpc_requests[request_id] = request_data


def test_can_run_agent():
    agent = run_agent(ConfigListTestAgent)
    try:
        assert agent is not None

        configs = agent.vip.config.list()
        assert configs is not None
        assert isinstance(configs, list)
    finally:
        if agent:
            agent.core.stop()
        agent = None

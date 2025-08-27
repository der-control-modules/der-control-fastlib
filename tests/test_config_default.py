from aems.client.agent import Agent


class ConfigListTestAgent(Agent):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.configs_received = {}
        abc = {"def": "ghi"}
        # Registers the configstore for the pattern 'config' ('config is default config entry')
        self.vip.config.subscribe(self.config_callback, actions=["NEW", "UPDATE"], pattern="config")
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
    import gevent

    from .utils import MessageBusManager

    with MessageBusManager() as manager:
        manager.start_bus()

        agent = manager.create_agent("test_agent", ConfigListTestAgent)

        # Ensure agent is an instance of Agent
        assert agent is not None
        assert isinstance(agent, Agent)

        # Connect the agent
        agent.connect()
        gevent.sleep(1)  # Give agent time to start and process config

        # Get the config list (this returns an AsyncResult)
        configs_result = agent.vip.config.list()
        configs = configs_result.get(timeout=5)  # Get the actual list
        assert configs is not None
        assert isinstance(configs, list)

        agent.disconnect()

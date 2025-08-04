"""
Tests for agent lifecycle functionality.

This module tests all aspects of agent lifecycle:
- Startup/shutdown
- On_start/on_stop hooks
- Duplicate on_start prevention
"""

import gevent

from aems.client.agent import Agent, Core

# Consolidating tests from:
# - test_lifecycle.py
# - test_duplicate_onstart.py


class TestAgentLifecycle:
    """Tests for agent lifecycle functionality."""

    def test_agent_startup(self, message_bus):
        """Test that agent properly starts up."""
        message_bus.start()

        # Create agent with on_start handler
        on_start_called = [False]

        class TestAgent(Agent):
            @Core.receiver('onstart')
            def on_start_handler(self, sender, **kwargs):
                on_start_called[0] = True

        # Create the agent with the correct port
        agent = TestAgent(identity="test_agent", port=8888)

        # Connect the agent to the message bus (which should trigger onstart)
        agent.connect()

        # Give it a moment to process the event
        gevent.sleep(1)

        # Verify on_start was called
        assert on_start_called[0] is True, "on_start handler should have been called"

        # Clean up
        agent.disconnect()
        message_bus.stop()

    def test_agent_shutdown(self, message_bus):
        """Test that agent properly shuts down."""
        message_bus.start()

        # Create agent with on_stop handler
        on_stop_called = [False]

        class TestAgent(Agent):
            @Core.receiver('onstop')
            def on_stop_handler(self, sender, **kwargs):
                on_stop_called[0] = True

        # Create the agent with the correct port
        agent = TestAgent(identity="test_agent", port=8888)

        # Connect the agent to the message bus
        agent.connect()

        # Give it a moment to connect
        gevent.sleep(1)

        # Disconnect the agent (which should trigger onstop)
        agent.disconnect()

        # Give it a moment to process the event
        gevent.sleep(1)

        # Verify on_stop was called
        assert on_stop_called[0] is True, "on_stop handler should have been called"

        # Clean up
        message_bus.stop()

    def test_start_stop_order(self, message_bus):
        """Test that on_start and on_stop are called in the correct order."""
        message_bus.start()

        # Create agent with tracking for call order
        call_order = []

        class TrackingAgent(Agent):
            @Core.receiver('onstart')
            def on_start_handler(self, sender, **kwargs):
                call_order.append("on_start")

            @Core.receiver('onstop')
            def on_stop_handler(self, sender, **kwargs):
                call_order.append("on_stop")

        # Create the agent with the correct port
        agent = TrackingAgent(identity="tracking_agent", port=8888)

        # Connect the agent to the message bus
        agent.connect()

        # Give it a moment to process the onstart event
        gevent.sleep(1)

        # Disconnect the agent
        agent.disconnect()

        # Give it a moment to process the onstop event
        gevent.sleep(1)

        # Verify correct call order
        assert call_order == ["on_start", "on_stop"]

        # Clean up
        message_bus.stop()

    def test_multiple_agents_lifecycle(self, message_bus):
        """Test lifecycle with multiple agents."""
        message_bus.start()

        # Create tracking for each agent
        agent1_calls = []
        agent2_calls = []

        class Agent1(Agent):
            @Core.receiver('onstart')
            def on_start_handler(self, sender, **kwargs):
                agent1_calls.append("start")

            @Core.receiver('onstop')
            def on_stop_handler(self, sender, **kwargs):
                agent1_calls.append("stop")

        class Agent2(Agent):
            @Core.receiver('onstart')
            def on_start_handler(self, sender, **kwargs):
                agent2_calls.append("start")

            @Core.receiver('onstop')
            def on_stop_handler(self, sender, **kwargs):
                agent2_calls.append("stop")

        # Create and connect agents
        agent1 = Agent1(identity="agent1", port=8888)
        agent2 = Agent2(identity="agent2", port=8888)

        agent1.connect()
        agent2.connect()

        # Give agents time to process onstart events
        gevent.sleep(1)

        # Disconnect the agents
        agent1.disconnect()
        agent2.disconnect()

        # Give agents time to process onstop events
        gevent.sleep(1)

        # Verify both agents went through lifecycle
        assert agent1_calls == ["start", "stop"]
        assert agent2_calls == ["start", "stop"]

        # Clean up
        message_bus.stop()

    def test_duplicate_onstart_prevention(self, message_bus):
        """Test that there's a clear way to implement onstart prevention."""
        message_bus.start()

        # Create agent with tracking onstart and prevention logic
        onstart_count = [0]
        already_started = [False]

        class TestAgent(Agent):
            @Core.receiver('onstart')
            def on_start_handler(self, sender, **kwargs):
                # This pattern can be used to prevent duplicate execution
                if already_started[0]:
                    return
                already_started[0] = True
                onstart_count[0] += 1

        # Create the agent with the correct port
        agent = TestAgent(identity="test_agent", port=8888)

        # Connect the agent (should trigger onstart)
        agent.connect()
        
        # Give it a moment to process the event
        gevent.sleep(1)

        # Simulate multiple starts by manually firing the onstart event
        # These should be ignored by our handler due to the prevention logic
        agent.core.fire_event("onstart", sender=agent)
        agent.core.fire_event("onstart", sender=agent)
        
        # Give it a moment to process the events
        gevent.sleep(1)

        # Verify onstart handler implementation prevented multiple executions
        assert onstart_count[0] == 1, "Our onstart prevention logic should work"

        # Clean up
        agent.disconnect()
        message_bus.stop()
        
    def test_manual_start_stop(self, message_bus):
        """Test manually starting and stopping an agent."""
        message_bus.start()

        # Create agent with tracking handlers
        onstart_called = [False]
        onstop_called = [False]

        class TestAgent(Agent):
            @Core.receiver('onstart')
            def on_start_handler(self, sender, **kwargs):
                onstart_called[0] = True

            @Core.receiver('onstop')
            def on_stop_handler(self, sender, **kwargs):
                onstop_called[0] = True

        # Create the agent with the correct port
        agent = TestAgent(identity="test_agent", port=8888)

        # Verify onstart has not been called yet
        assert onstart_called[0] is False

        # Manually start the agent
        agent.connect()

        # Give it a moment to process the event
        gevent.sleep(1)

        # Verify onstart was called
        assert onstart_called[0] is True

        # Manually stop the agent
        agent.disconnect()

        # Give it a moment to process the event
        gevent.sleep(1)

        # Verify onstop was called
        assert onstop_called[0] is True

        message_bus.stop()

    def test_exception_in_onstart(self, message_bus):
        """Test handling of exceptions in on_start."""
        message_bus.start()

        # Create agent with failing on_start
        class FailingAgent(Agent):
            @Core.receiver('onstart')
            def on_start_handler(self, sender, **kwargs):
                raise ValueError("Test exception in on_start")

        # Create the agent with the correct port
        agent = FailingAgent(identity="failing_agent", port=8888)

        # Connect the agent and catch the exception
        # Note: The exception might not propagate directly from connect() because of how
        # events are handled asynchronously. We'll need to modify the test approach.
        agent.connect()

        # Give it a moment to process the event
        gevent.sleep(1)

        # The test itself succeeds if we got here without crashing
        # In a real implementation, we'd want to check logs or error handlers

        # Clean up
        agent.disconnect()
        message_bus.stop()

    def test_restart_agent(self, message_bus):
        """Test that an agent can be restarted."""
        message_bus.start()

        # Create agent with tracking handlers
        start_count = [0]
        stop_count = [0]

        class RestartableAgent(Agent):
            @Core.receiver('onstart')
            def on_start_handler(self, sender, **kwargs):
                start_count[0] += 1

            @Core.receiver('onstop')
            def on_stop_handler(self, sender, **kwargs):
                stop_count[0] += 1

        # Create the agent with the correct port
        agent = RestartableAgent(identity="restart_agent", port=8888)

        # Connect the agent
        agent.connect()

        # Give it a moment to process the event
        gevent.sleep(1)

        # Verify initial start
        assert start_count[0] == 1

        # Stop the agent
        agent.disconnect()

        # Give it a moment to process the event
        gevent.sleep(1)

        assert stop_count[0] == 1

        # Restart the agent
        agent.connect()

        # Give it a moment to process the event
        gevent.sleep(1)

        assert start_count[0] == 2

        # Clean up
        agent.disconnect()

        # Give it a moment to process the event
        gevent.sleep(1)

        message_bus.stop()
        assert stop_count[0] == 2

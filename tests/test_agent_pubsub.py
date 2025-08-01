"""
Test agent publish/subscribe functionality using pytest
"""
import pytest
import gevent
from aems.client.agent import Agent


class TestAgentPubSub:
    """Test agent publish/subscribe functionality."""

    @pytest.fixture(autouse=True)
    def setup_agents(self, message_bus):
        """Set up test agents with the running message bus."""
        # Store the message bus reference
        self.message_bus = message_bus
        
        # Create agents with the correct port
        self.publisher = Agent("test_publisher", port=8888)
        self.subscriber1 = Agent("test_subscriber1", port=8888)
        self.subscriber2 = Agent("test_subscriber2", port=8888)
        
        # Connect all agents
        self.publisher.connect()
        self.subscriber1.connect()
        self.subscriber2.connect()
        
        # Wait for connections
        gevent.sleep(1)
        
        yield
        
        # Cleanup
        if hasattr(self, 'publisher'):
            self.publisher.disconnect()
        if hasattr(self, 'subscriber1'):
            self.subscriber1.disconnect()
        if hasattr(self, 'subscriber2'):
            self.subscriber2.disconnect()
        if hasattr(self, 'subscriber2'):
            self.subscriber2.core.stop().get()

    def test_basic_publish_subscribe(self):
        """Test basic publish/subscribe functionality."""
        # Clear any existing messages
        self.subscriber1.clear_received_messages()
        self.subscriber2.clear_received_messages()
        
        # Set up subscriptions
        sub1_result = self.subscriber1.vip.pubsub.subscribe("test/")
        assert sub1_result.get() is not None, "Subscription 1 should succeed"
        
        sub2_result = self.subscriber2.vip.pubsub.subscribe("test/special/")
        assert sub2_result.get() is not None, "Subscription 2 should succeed"
        
        # Wait for subscriptions to be processed
        gevent.sleep(1)
        
        # Publish messages
        pub_result1 = self.publisher.vip.pubsub.publish("", "test/topic1", "Hello from topic1")
        assert pub_result1.get() is True, "Publish 1 should succeed"
        
        pub_result2 = self.publisher.vip.pubsub.publish("", "test/special/topic2", "Hello from special topic2")
        assert pub_result2.get() is True, "Publish 2 should succeed"
        
        pub_result3 = self.publisher.vip.pubsub.publish("", "other/topic3", "Hello from other topic3")
        assert pub_result3.get() is True, "Publish 3 should succeed"
        
        # Wait for message processing
        gevent.sleep(2)
        
        # Verify message reception
        sub1_messages = self.subscriber1.get_received_messages()
        sub2_messages = self.subscriber2.get_received_messages()
        
        # Subscriber1 should receive messages from "test/" prefix
        assert len(sub1_messages) >= 2, f"Subscriber1 should receive at least 2 messages, got {len(sub1_messages)}"
        
        # Subscriber2 should receive messages from "test/special/" prefix only
        assert len(sub2_messages) >= 1, f"Subscriber2 should receive at least 1 message, got {len(sub2_messages)}"

    def test_regex_subscription(self):
        """Test regex pattern subscription."""
        # Clear any existing messages
        self.subscriber1.clear_received_messages()
        
        # Set up regex subscription
        sub_result = self.subscriber1.vip.pubsub.subscribe_regex(r"^pattern/\d+/test$")
        assert sub_result.get() is not None, "Regex subscription should succeed"
        
        # Wait for subscription to be processed
        gevent.sleep(1)
        
        # Publish matching and non-matching messages
        self.publisher.vip.pubsub.publish("", "pattern/123/test", "Should match").get()
        self.publisher.vip.pubsub.publish("", "pattern/abc/test", "Should not match").get()
        self.publisher.vip.pubsub.publish("", "pattern/456/test", "Should match").get()
        
        # Wait for message processing
        gevent.sleep(2)
        
        # Check received messages
        messages = self.subscriber1.get_received_messages()
        
        # Should receive messages that match the pattern
        pubsub_messages = [msg for msg in messages if msg.get("type") == "pubsub"]
        assert len(pubsub_messages) >= 2, f"Should receive at least 2 matching messages, got {len(pubsub_messages)}"

    def test_multiple_subscriptions(self):
        """Test multiple subscriptions on the same agent."""
        # Clear any existing messages
        self.subscriber1.clear_received_messages()
        
        # Set up multiple subscriptions
        sub1 = self.subscriber1.vip.pubsub.subscribe("news/")
        sub2 = self.subscriber1.vip.pubsub.subscribe("alerts/")
        
        assert sub1.get() is not None, "First subscription should succeed"
        assert sub2.get() is not None, "Second subscription should succeed"
        
        # Wait for subscriptions
        gevent.sleep(1)
        
        # Publish to both topics
        self.publisher.vip.pubsub.publish("", "news/weather", "Sunny today").get()
        self.publisher.vip.pubsub.publish("", "alerts/emergency", "Test alert").get()
        self.publisher.vip.pubsub.publish("", "other/random", "Should not receive").get()
        
        # Wait for message processing
        gevent.sleep(2)
        
        # Check received messages
        messages = self.subscriber1.get_received_messages()
        pubsub_messages = [msg for msg in messages if msg.get("type") == "pubsub"]
        
        # Should receive messages from both subscribed topics
        assert len(pubsub_messages) >= 2, f"Should receive at least 2 messages, got {len(pubsub_messages)}"
        
        # Verify topics
        topics = [msg.get("topic", "") for msg in pubsub_messages]
        assert any(topic.startswith("news/") for topic in topics), "Should receive news message"
        assert any(topic.startswith("alerts/") for topic in topics), "Should receive alerts message"

    def test_message_content_integrity(self):
        """Test that message content is preserved correctly."""
        # Clear any existing messages
        self.subscriber1.clear_received_messages()
        
        # Set up subscription
        self.subscriber1.vip.pubsub.subscribe("data/").get()
        gevent.sleep(1)
        
        # Test different types of message content
        test_data = {
            "string": "Hello World",
            "number": 42,
            "float": 3.14,
            "boolean": True,
            "list": [1, 2, 3],
            "dict": {"nested": "value"}
        }
        
        self.publisher.vip.pubsub.publish("", "data/test", test_data).get()
        gevent.sleep(2)
        
        # Verify message content
        messages = self.subscriber1.get_received_messages()
        pubsub_messages = [msg for msg in messages if msg.get("type") == "pubsub"]
        
        assert len(pubsub_messages) >= 1, "Should receive the test message"
        
        received_data = pubsub_messages[0].get("message", {})
        assert received_data == test_data, f"Message content should be preserved: expected {test_data}, got {received_data}"

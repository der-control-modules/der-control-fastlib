# run_tests.py

import asyncio
import sys
import os
from message_bus_test_clients import (
    run_publisher_subscriber_test,
    run_vip_message_test,
    run_complex_test,
)


async def main():
    # Check if an argument was provided
    if len(sys.argv) > 1:
        test_name = sys.argv[1]
        if test_name == "pubsub":
            print("Running Publisher-Subscriber Test")
            await run_publisher_subscriber_test()
        elif test_name == "vip":
            print("Running VIP Message Test")
            await run_vip_message_test()
        elif test_name == "complex":
            print("Running Complex Interaction Test")
            await run_complex_test()
        else:
            print(f"Unknown test: {test_name}")
            print("Available tests: pubsub, vip, complex")
    else:
        # Run all tests
        print("Running all tests")
        print("\n=== Running Publisher-Subscriber Test ===")
        await run_publisher_subscriber_test()

        print("\n=== Running VIP Message Test ===")
        await run_vip_message_test()

        print("\n=== Running Complex Interaction Test ===")
        await run_complex_test()


if __name__ == "__main__":
    asyncio.run(main())

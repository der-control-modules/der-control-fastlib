# run_gevent_tests.py

import sys

from gevent_message_bus_test_clients import (
    run_multi_hop_rpc_test,
    run_publisher_subscriber_test,
    run_rpc_test,
    run_vip_message_test,
)


def main():
    # Check if an argument was provided
    if len(sys.argv) > 1:
        test_name = sys.argv[1]
        if test_name == "pubsub":
            print("Running Publisher-Subscriber Test")
            run_publisher_subscriber_test()
        elif test_name == "vip":
            print("Running VIP Message Test")
            run_vip_message_test()
        elif test_name == "rpc":
            print("Running RPC Test")
            run_rpc_test()
        elif test_name == "multihop":
            print("Running Multi-Hop RPC Test")
            run_multi_hop_rpc_test()
        else:
            print(f"Unknown test: {test_name}")
            print("Available tests: pubsub, vip, rpc, multihop")
    else:
        # Run all tests
        print("Running all tests")
        print("\n=== Running Publisher-Subscriber Test ===")
        run_publisher_subscriber_test()

        print("\n=== Running VIP Message Test ===")
        run_vip_message_test()

        print("\n=== Running RPC Test ===")
        run_rpc_test()

        print("\n=== Running Multi-Hop RPC Test ===")
        run_multi_hop_rpc_test()


if __name__ == "__main__":
    main()

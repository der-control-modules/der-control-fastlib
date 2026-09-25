# Test Fixtures

This directory contains configuration files and test data used by the integration tests.

## BACnet Test Fixtures

### bacnet_proxy.config
Configuration for the BACnet proxy agent used in integration tests. This specifies:
- Device address: `192.168.1.5/24` - The network interface for BACnet communication
- Object ID: 599 - The BACnet device object ID for the proxy
- APDU settings for BACnet protocol communication

### schneider.csv
BACnet device registry configuration for a Schneider Electric controller. This CSV file maps:
- Volttron point names to BACnet object types and indexes
- Read/write permissions and priorities
- Units and metadata for each point

This registry is used to test read_properties calls in the BACnet proxy integration tests.

## Usage

These fixtures are automatically loaded by the BACnet integration tests in `test_bacnet_proxy_integration.py`.

To run BACnet integration tests:
```bash
# Run all BACnet tests
pytest -m bacnet tests/test_bacnet_proxy_integration.py -v

# Run specific test
pytest -m bacnet tests/test_bacnet_proxy_integration.py::TestBACnetProxyIntegration::test_bacnet_proxy_rpc_read_properties -v

# Run without BACnet tests (default)
pytest -m "not bacnet" tests/
```

## Requirements

BACnet integration tests require:
1. A BACnet device or simulator at address `2001:2` (configurable in test code)
2. Network connectivity on the `192.168.1.5/24` subnet
3. The `bacpypes` library installed (`pip install bacpypes`)
4. The VOLTTRON BACnet proxy agent code available

If the BACnet device is not available, tests will be skipped with an appropriate message rather than failing.

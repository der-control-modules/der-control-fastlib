"""
VOLTTRON math_utils compatibility shim.

Provides basic math helper functions for VOLTTRON agents.
These are simple statistical functions that don't require numpy.
"""


def mean(data):
    """Return the sample arithmetic mean of data."""
    n = len(data)
    if n < 1:
        raise ValueError("mean requires at least one data point")
    return sum(data) / n


def _ss(data):
    """Return sum of square deviations of sequence data."""
    c = mean(data)
    ss = sum((x - c) ** 2 for x in data)
    return ss


def pstdev(data):
    """Calculates the population standard deviation."""
    n = len(data)
    if n < 2:
        raise ValueError("variance requires at least two data points")
    ss = _ss(data)
    pvar = ss / n  # the population variance
    return pvar**0.5


def stdev(data):
    """Calculates the sample standard deviation."""
    n = len(data)
    if n < 2:
        raise ValueError("variance requires at least two data points")
    ss = _ss(data)
    pvar = ss / (n - 1)  # sample variance
    return pvar**0.5


__all__ = ["mean", "pstdev", "stdev"]

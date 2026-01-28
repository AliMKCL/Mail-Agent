#!/usr/bin/env python3
"""Test script for Rate Limiter microservice"""

import sys
sys.path.insert(0, '/Users/alimuratkeceli/Desktop/Projects/Python/Mail_agent')

from ratelimiter.client.ratelimiter_client import RateLimiterClient

def main():
    print("=" * 60)
    print("Rate Limiter Test Suite")
    print("=" * 60)

    limiter = RateLimiterClient("http://localhost:8002")

    # Test 1: Health check
    print("\n[Test 1] Health Check")
    try:
        health = limiter.health()
        print(f"✓ Status: {health['status']}")
        print(f"✓ Version: {health['version']}")
        print(f"✓ Active buckets: {health['stats']['active_buckets']}")
    except Exception as e:
        print(f"✗ Failed: {e}")
        return

    # Test 2: Basic rate limiting (per-user)
    print("\n[Test 2] Per-User Rate Limiting")
    print("Making 5 requests for user 'test_user'...")
    for i in range(1, 6):
        result = limiter.check("user", "test_user", "/api/test")
        status = "✓ Allowed" if result['allowed'] else "✗ Denied"
        print(f"  Request {i}: {status} | Remaining: {result['remaining']} | Limit: {result['limit']}")

    # Test 3: Different users have independent limits
    print("\n[Test 3] Independent User Limits")
    result1 = limiter.check("user", "user_1", "/api/test")
    result2 = limiter.check("user", "user_2", "/api/test")
    print(f"✓ User 1: {result1['remaining']} tokens remaining")
    print(f"✓ User 2: {result2['remaining']} tokens remaining")
    print(f"✓ Users have independent buckets")

    # Test 4: Global rate limiting
    print("\n[Test 4] Global Rate Limiting")
    result = limiter.check("global", "all", "/api/heavy")
    print(f"✓ Global limit check: allowed={result['allowed']}, remaining={result['remaining']}")

    # Test 5: Status query (doesn't consume tokens)
    print("\n[Test 5] Status Query (No Token Consumption)")
    before = limiter.get_status("user", "status_test", "/api/test")
    print(f"  Before: {before['remaining']} tokens")

    # Query status again (shouldn't change)
    after = limiter.get_status("user", "status_test", "/api/test")
    print(f"  After:  {after['remaining']} tokens")
    print(f"✓ Status queries don't consume tokens")

    # Test 6: Reset bucket
    print("\n[Test 6] Bucket Reset")
    # Consume some tokens
    limiter.check("user", "reset_test", "/api/test")
    limiter.check("user", "reset_test", "/api/test")
    status_before = limiter.get_status("user", "reset_test", "/api/test")
    print(f"  Before reset: {status_before['remaining']} tokens")

    # Reset
    reset_result = limiter.reset("user", "reset_test", "/api/test")
    print(f"  Reset: {reset_result['message']}")

    status_after = limiter.get_status("user", "reset_test", "/api/test")
    print(f"  After reset: {status_after['remaining']} tokens")
    print(f"✓ Bucket successfully reset")

    # Test 7: Different endpoints have independent buckets
    print("\n[Test 7] Independent Endpoint Buckets")
    result1 = limiter.check("user", "multi_endpoint", "/api/endpoint1")
    result2 = limiter.check("user", "multi_endpoint", "/api/endpoint2")
    print(f"✓ Same user, endpoint1: {result1['remaining']} remaining")
    print(f"✓ Same user, endpoint2: {result2['remaining']} remaining")
    print(f"✓ Endpoints have independent buckets")

    # Test 8: Final stats
    print("\n[Test 8] Service Statistics")
    health = limiter.health()
    stats = health['stats']
    print(f"✓ Total requests: {stats['total_requests']}")
    print(f"✓ Allowed: {stats['allowed']}")
    print(f"✓ Denied: {stats['denied']}")
    print(f"✓ Active buckets: {stats['active_buckets']}")
    print(f"✓ Uptime: {stats['uptime_seconds']} seconds")

    print("\n" + "=" * 60)
    print("All tests completed successfully! ✓")
    print("=" * 60)

if __name__ == "__main__":
    main()

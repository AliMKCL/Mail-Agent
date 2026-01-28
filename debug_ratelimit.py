#!/usr/bin/env python3
"""Debug script to test rate limiting behavior"""

import sys
import time
sys.path.insert(0, '/Users/alimuratkeceli/Desktop/Projects/Python/Mail_agent')

from ratelimiter.client.ratelimiter_client import RateLimiterClient

def main():
    limiter = RateLimiterClient("http://localhost:8002")

    print("=" * 60)
    print("Rate Limiter Debug Test")
    print("=" * 60)
    print("\nMaking 10 rapid requests (no delay)...\n")

    for i in range(1, 11):
        result = limiter.check("user", "1", "/api/emails")

        print(f"Request {i:2d}: allowed={result['allowed']}, remaining={result['remaining']}, limit={result['limit']}")

        # NO DELAY - requests happen instantly

    print("\n" + "=" * 60)
    print("Checking service stats:")
    health = limiter.health()
    print(f"Total requests: {health['stats']['total_requests']}")
    print(f"Allowed: {health['stats']['allowed']}")
    print(f"Denied: {health['stats']['denied']}")
    print(f"Active buckets: {health['stats']['active_buckets']}")
    print("=" * 60)

if __name__ == "__main__":
    main()

"""
Anthropic API smoke test.

Run this BEFORE flipping USE_CLAUDE in mood.py. Confirms three things:
  1. The anthropic Python package is installed
  2. The ANTHROPIC_API_KEY environment variable is set
  3. The key actually works (credits available, not expired, not revoked)

Run from project root:
    python smoke_test_claude.py
"""

import os
import sys


def main():
    print("=" * 60)
    print("  Claudiac — Claude API smoke test")
    print("=" * 60)

    # 1. Check package
    try:
        from anthropic import Anthropic
    except ImportError:
        print("FAIL: anthropic package not installed.")
        print("   Fix: pip install anthropic")
        sys.exit(1)
    print("[ok] anthropic package installed")

    # 2. Check environment variable
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        print("FAIL: ANTHROPIC_API_KEY environment variable not set.")
        print("   Fix (Windows cmd): set ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)
    if not key.startswith("sk-ant-"):
        print(f"FAIL: ANTHROPIC_API_KEY does not look right "
              f"(starts with {key[:8]!r}, expected 'sk-ant-...').")
        sys.exit(1)
    print(f"[ok] ANTHROPIC_API_KEY is set "
          f"(starts {key[:10]}..., ends ...{key[-4:]})")

    # 3. Try a tiny actual API call
    print("[..] sending 'hello' to claude-sonnet-4-5...")
    try:
        client = Anthropic()
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=50,
            messages=[{
                "role": "user",
                "content": "Reply with exactly the word 'pong' and nothing else."
            }],
        )
    except Exception as e:
        print(f"FAIL: API call raised {type(e).__name__}")
        print(f"   {e}")
        print()
        print("   Common causes:")
        print("   - credit_balance_too_low: $5 not actually applied yet")
        print("   - invalid_api_key: key was revoked or pasted wrong")
        print("   - rate limit / org issue: check console.anthropic.com")
        sys.exit(1)

    text = response.content[0].text.strip()
    print(f"[ok] Claude replied: {text!r}")

    # 4. Show usage
    if hasattr(response, "usage"):
        u = response.usage
        print(f"[ok] tokens used: input={u.input_tokens}, "
              f"output={u.output_tokens}")
        # rough cost estimate (Sonnet 4.5: $3/MTok in, $15/MTok out)
        cost_in = u.input_tokens * 3.0 / 1_000_000
        cost_out = u.output_tokens * 15.0 / 1_000_000
        print(f"     estimated cost: ${cost_in + cost_out:.6f}")

    print()
    print("=" * 60)
    print("  ALL CHECKS PASSED.")
    print("  You can now flip USE_CLAUDE = True in algorithms/mood.py")
    print("=" * 60)


if __name__ == "__main__":
    main()

"""
Promote the most recent daq.py capture to the realtime_daq cached fallback.

After you've recorded a clean 30s capture with daq.py (press 's' to save
data/live_ecg.npz), run this script to copy it into
data/scenarios/realtime_daq_cached.npz. That file becomes the safety net
the backend reaches for when a live capture fails its quality gate.

Usage (from project root):
    python tools/promote_cached_daq.py
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE = os.path.join(ROOT, "data", "live_ecg.npz")
CACHED = os.path.join(ROOT, "data", "scenarios", "realtime_daq_cached.npz")


def main():
    if not os.path.exists(LIVE):
        print(f"ERROR: no live capture found at {LIVE}")
        print("       run daq.py and press 's' first.")
        sys.exit(1)

    os.makedirs(os.path.dirname(CACHED), exist_ok=True)
    shutil.copyfile(LIVE, CACHED)

    sz = os.path.getsize(CACHED)
    print(f"OK: promoted live capture to cached fallback")
    print(f"    src: {LIVE}")
    print(f"    dst: {CACHED}")
    print(f"    size: {sz/1024:.1f} KB")
    print()
    print("This recording will now be used as the fallback when:")
    print("  - Live capture detects fewer than 5 R-peaks")
    print("  - Live capture HRV SDNN exceeds 200 ms (noisy)")
    print("  - Live capture file is missing entirely")


if __name__ == "__main__":
    main()

"""
Step-by-step LRU buffer pool simulation for lab reports / demos.
Run: python simulate.py
"""

from __future__ import annotations

import argparse
from typing import List, Optional

from buffer_pool import LRUBufferPool, optimal_hit_ratio, run_reference_string


def format_step(pool: LRUBufferPool, page: int, hit: bool, evicted: Optional[int]) -> str:
    mru_lru = pool.frame_contents_mru_to_lru()
    frames = "[" + ", ".join(str(x) for x in mru_lru) + "]"
    status = "HIT " if hit else "MISS"
    ev = f" evict={evicted}" if evicted is not None else ""
    return f"ref {page:>3} -> {status}{ev:12} frames (MRU..LRU): {frames}"


def main() -> None:
    parser = argparse.ArgumentParser(description="LRU buffer pool trace simulator")
    parser.add_argument(
        "--frames",
        type=int,
        default=3,
        help="Number of buffer frames (default 3)",
    )
    parser.add_argument(
        "--pages",
        type=str,
        default="7,0,1,2,0,3,0,4,2,3,0,3,2,1,2,0,1,7,0,1",
        help="Comma-separated page reference string",
    )
    args = parser.parse_args()
    pages: List[int] = [int(x.strip()) for x in args.pages.split(",") if x.strip()]

    pool, results = run_reference_string(args.frames, pages)

    print("LRU buffer pool simulation (DBMS-style page replacement)")
    print(f"Frames: {args.frames}  |  References: {len(pages)}")
    print("-" * 72)
    pool2 = LRUBufferPool(args.frames)
    for p in pages:
        r = pool2.access(p)
        print(format_step(pool2, p, r.hit, r.evicted_page_id))
    print("-" * 72)
    s = pool.stats
    print(f"Hits: {s.hits}  Misses: {s.misses}  Evictions: {s.evictions}")
    print(f"Hit ratio: {s.hit_ratio:.4f}")
    opt = optimal_hit_ratio(args.frames, pages)
    print(f"Optimal (clairvoyant) hit ratio upper bound: {opt:.4f} (comparison only)")


if __name__ == "__main__":
    main()

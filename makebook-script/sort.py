#!/usr/bin/env python3
"""Sort and normalize a YaneuraOu book DB."""

from __future__ import annotations

import argparse

from yaneuraou_book import read_yaneuraou_book, write_yaneuraou_book


def main() -> None:
    parser = argparse.ArgumentParser(description="Sort and normalize a YaneuraOu book DB.")
    parser.add_argument("src", help="source YaneuraOu book DB")
    parser.add_argument("dst", help="destination YaneuraOu book DB")
    parser.add_argument(
        "--ignore-book-ply",
        action="store_true",
        help="ignore ply when reading source positions",
    )
    args = parser.parse_args()

    book = read_yaneuraou_book(args.src, ignore_book_ply=args.ignore_book_ply)
    write_yaneuraou_book(book, args.dst)
    print(f"positions = {len(book)}")
    print(f"entries   = {sum(len(moves) for moves in book.values())}")
    print(f"wrote     = {args.dst}")


if __name__ == "__main__":
    main()

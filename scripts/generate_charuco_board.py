#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import cv2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a ChArUco board PNG.")
    parser.add_argument("--dictionary", default="DICT_6X6_250")
    parser.add_argument("--squares-x", type=int, default=5)
    parser.add_argument("--squares-y", type=int, default=7)
    parser.add_argument("--square-length-m", type=float, default=0.03)
    parser.add_argument("--marker-length-m", type=float, default=0.015)
    parser.add_argument("--width-px", type=int, default=1200)
    parser.add_argument("--margin-px", type=int, default=40)
    parser.add_argument(
        "--output",
        default=str(Path.home() / "Downloads" / "charuco_board_5x7.png"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dictionary_id = getattr(cv2.aruco, args.dictionary)
    dictionary = cv2.aruco.getPredefinedDictionary(dictionary_id)
    if hasattr(cv2.aruco, "CharucoBoard"):
        board = cv2.aruco.CharucoBoard(
            (args.squares_x, args.squares_y),
            args.square_length_m,
            args.marker_length_m,
            dictionary,
        )
    else:
        board = cv2.aruco.CharucoBoard_create(
            args.squares_x,
            args.squares_y,
            args.square_length_m,
            args.marker_length_m,
            dictionary,
        )

    height_px = int(args.width_px * args.squares_y / args.squares_x)
    if hasattr(board, "generateImage"):
        image = board.generateImage((args.width_px, height_px), marginSize=args.margin_px)
    else:
        image = board.draw((args.width_px, height_px), marginSize=args.margin_px)

    output = Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output), image)
    print(f"Saved ChArUco board to: {output}")
    print(f"dictionary={args.dictionary}")
    print(f"squares_x={args.squares_x}, squares_y={args.squares_y}")
    print(f"square_length_m={args.square_length_m}")
    print(f"marker_length_m={args.marker_length_m}")


if __name__ == "__main__":
    main()

import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "server"))
from app import reorder_moves


def apply(items, moves):
    """YouTube의 ACTION_MOVE_VIDEO_BEFORE를 순서대로 적용한다."""
    items = list(items)
    for moved, successor in moves:
        items.remove(moved)
        items.insert(items.index(successor), moved)
    return items


def test_every_order_of_up_to_five_tracks():
    for n in range(6):
        current = [f"s{i}" for i in range(n)]
        for order in itertools.permutations(current):
            assert apply(current, reorder_moves(current, list(order))) == list(order)


if __name__ == "__main__":
    test_every_order_of_up_to_five_tracks()
    print("ok")

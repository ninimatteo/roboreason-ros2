import numpy as np
import pytest

from robo_reason_perception import rle


@pytest.mark.parametrize('first', [False, True])
def test_round_trip(first):
    rng = np.random.default_rng(0)
    mask = rng.random((37, 53)) > 0.7
    mask[0, 0] = first
    runs = rle.encode(mask)
    assert (runs[0] == 0) == first
    assert np.array_equal(rle.decode(runs, 37, 53), mask)


def test_empty_and_full():
    for mask in (np.zeros((4, 5), bool), np.ones((4, 5), bool)):
        assert np.array_equal(rle.decode(rle.encode(mask), 4, 5), mask)


def test_wrong_size_is_refused():
    with pytest.raises(ValueError):
        rle.decode([3, 4], 3, 3)

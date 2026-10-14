"""Run length encoding of a boolean mask, the form masks travel in over ROS.

Row major over the whole image, starting with a run of False (possibly 0
long), then alternating. Only numpy: the ROS container decodes with it too.
"""
import numpy as np


def encode(mask: np.ndarray) -> list:
    flat = np.asarray(mask, dtype=bool).ravel()
    edges = np.flatnonzero(flat[1:] != flat[:-1]) + 1
    bounds = np.concatenate(([0], edges, [flat.size]))
    runs = np.diff(bounds).tolist()
    return ([0] + runs) if flat.size and flat[0] else runs


def decode(runs, height: int, width: int) -> np.ndarray:
    values = np.arange(len(runs)) % 2 == 1          # False, True, False, ...
    flat = np.repeat(values, np.asarray(runs, dtype=np.int64))
    if flat.size != height * width:
        raise ValueError(f'runs cover {flat.size} pixels, the image has {height * width}')
    return flat.reshape(height, width)

"""The segment service: SAM 2 automatic masks, masks for named things and for
points, on the GPU perception container (ROBOAI-60).

Runs only where torch, SAM 2 and transformers are installed (docker/perception).
"""
import time

import numpy as np
import rclpy
from rclpy.node import Node

from robo_reason_bringup.config import settings
from robo_reason_bringup.interfaces import names
from robo_reason_interfaces.msg import Mask
from robo_reason_interfaces.srv import Segment
from robo_reason_perception import rle


def image_to_rgb(msg) -> np.ndarray:
    if msg.encoding not in ('rgb8', 'bgr8'):
        raise ValueError(f"unsupported image encoding '{msg.encoding}'")
    rows = np.frombuffer(bytes(msg.data), dtype=np.uint8).reshape(msg.height, msg.step)
    rgb = rows[:, :msg.width * 3].reshape(msg.height, msg.width, 3)
    return np.ascontiguousarray(rgb[..., ::-1] if msg.encoding == 'bgr8' else rgb)


def mask_msg(mask: np.ndarray, source: str, prompt: int, score: float, label: str = '') -> Mask:
    ys, xs = np.nonzero(mask)
    bbox = [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())] if xs.size else [0, 0, -1, -1]
    return Mask(source=source, prompt=prompt, label=label, score=score, bbox=bbox, rle=rle.encode(mask))


class SegmentNode(Node):
    def __init__(self):
        # the profile's namespace, like the rest of the stack: /sim for the simulation
        super().__init__('segment_node', namespace=f'/{names.namespace}' if names.namespace else '')
        from robo_reason_perception.models import Segmenter
        t0 = time.monotonic()
        self.segmenter = Segmenter(
            settings.PERCEPTION_SAM2_CONFIG, settings.PERCEPTION_SAM2_CHECKPOINT,
            settings.PERCEPTION_DETECTOR, settings.PERCEPTION_BOX_THRESHOLD,
            settings.PERCEPTION_TEXT_THRESHOLD)
        self.create_service(Segment, names.segment, self._on_segment)
        self.get_logger().info(f'models loaded in {time.monotonic() - t0:.1f} s; serving {names.segment}')

    def _on_segment(self, request, response):
        t0 = time.monotonic()
        try:
            rgb = image_to_rgb(request.image)
            masks = []
            if request.automatic:
                masks += [mask_msg(m, 'auto', -1, s) for m, s in self.segmenter.automatic(rgb)]
            if request.names:
                masks += [mask_msg(m, 'name', k, s, label)
                          for k, label, m, s in self.segmenter.by_names(rgb, list(request.names))]
            if request.points:
                masks += [mask_msg(m, 'point', k, s)
                          for k, m, s in self.segmenter.by_points(rgb, [(p.x, p.y) for p in request.points])]
        except Exception as exc:   # noqa: BLE001 — a bad request must not take the GPU node down
            response.success = False
            response.error_message = f'{type(exc).__name__}: {exc}'
            self.get_logger().error(response.error_message)
            return response
        response.success = True
        response.masks = masks
        response.seconds = time.monotonic() - t0
        self.get_logger().info(
            f'segment: {len(masks)} masks (auto={request.automatic}, names={list(request.names)}, '
            f'points={len(request.points)}) in {response.seconds:.1f} s')
        return response


def main(args=None):
    rclpy.init(args=args)
    node = SegmentNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()

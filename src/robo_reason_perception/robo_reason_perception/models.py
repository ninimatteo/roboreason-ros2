"""SAM 2 and GroundingDINO on the GPU, the three ways the service segments.

Settings are the ones of the ROBOAI-56 spike (~/sam2_spike), so the service
gives the masks the spike gave.
"""
import numpy as np
import torch
from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator
from sam2.build_sam import build_sam2
from sam2.sam2_image_predictor import SAM2ImagePredictor
from torchvision.ops import nms
from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

NMS_IOU = 0.8


class Segmenter:
    def __init__(self, sam2_config, sam2_checkpoint, detector, box_threshold, text_threshold,
                 device='cuda'):
        self.device = device
        sam = build_sam2(sam2_config, sam2_checkpoint, device=device)
        self.predictor = SAM2ImagePredictor(sam)
        self.generator = SAM2AutomaticMaskGenerator(
            sam, points_per_side=32, pred_iou_thresh=0.8, stability_score_thresh=0.92,
            min_mask_region_area=200)
        self.processor = AutoProcessor.from_pretrained(detector)
        self.detector = AutoModelForZeroShotObjectDetection.from_pretrained(detector).to(device)
        self.box_threshold, self.text_threshold = box_threshold, text_threshold

    @torch.inference_mode()
    def automatic(self, rgb: np.ndarray) -> list:
        """(mask, predicted IoU) for every SAM automatic mask."""
        return [(m['segmentation'], float(m['predicted_iou'])) for m in self.generator.generate(rgb)]

    @torch.inference_mode()
    def by_names(self, rgb: np.ndarray, names: list) -> list:
        """(name index or -1, detector phrase, mask, detector score) per detected box."""
        from PIL import Image
        pil = Image.fromarray(rgb)
        inputs = self.processor(images=pil, text='. '.join(names) + '.', return_tensors='pt').to(self.device)
        found = self.processor.post_process_grounded_object_detection(
            self.detector(**inputs), inputs.input_ids, threshold=self.box_threshold,
            text_threshold=self.text_threshold, target_sizes=[pil.size[::-1]])[0]
        boxes, scores, labels = found['boxes'], found['scores'], found['text_labels']
        keep = nms(boxes, scores, NMS_IOU).tolist()
        self.predictor.set_image(rgb)
        out = []
        for i in keep:
            masks, _, _ = self.predictor.predict(box=boxes[i].cpu().numpy(), multimask_output=False)
            index = names.index(labels[i]) if labels[i] in names else -1
            out.append((index, labels[i], masks[0].astype(bool), float(scores[i])))
        return out

    @torch.inference_mode()
    def by_points(self, rgb: np.ndarray, points: list) -> list:
        """(point index, mask, predicted IoU), three masks per (u, v) point."""
        self.predictor.set_image(rgb)
        out = []
        for k, (u, v) in enumerate(points):
            masks, scores, _ = self.predictor.predict(
                point_coords=np.array([[u, v]], dtype=np.float32), point_labels=np.array([1]),
                multimask_output=True)
            out += [(k, m.astype(bool), float(s)) for m, s in zip(masks, scores)]
        return out

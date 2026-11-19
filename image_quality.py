"""Conservative geometric checks for two-date aerial imagery."""

import cv2
import numpy as np
from shapely.geometry import Polygon


def _gray(image):
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def _correlation(first, second, mask):
    a = first[mask].astype(np.float32)
    b = second[mask].astype(np.float32)
    if a.size < 100 or a.std() < 3 or b.std() < 3:
        return 0.0
    a -= a.mean()
    b -= b.mean()
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def refine_translation(old, new, coverage, max_shift=16):
    """Refine small residual offsets after the GeoTIFF world-file transform.

    ECC can fail on changed scenes; in that case the georeferenced alignment is
    retained. Only a bounded shift that measurably improves similarity is used.
    """
    height, width = new.shape[:2]
    scale = min(1.0, 768 / max(height, width))
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    old_small = cv2.resize(_gray(old), size, interpolation=cv2.INTER_AREA)
    new_small = cv2.resize(_gray(new), size, interpolation=cv2.INTER_AREA)
    mask = cv2.resize(coverage, size, interpolation=cv2.INTER_NEAREST)
    mask = cv2.erode(mask, np.ones((5, 5), np.uint8))
    if cv2.countNonZero(mask) < mask.size * 0.35:
        return old, coverage, (0.0, 0.0)
    old_small = cv2.GaussianBlur(old_small, (5, 5), 0)
    new_small = cv2.GaussianBlur(new_small, (5, 5), 0)
    warp = np.eye(2, 3, dtype=np.float32)
    try:
        score, warp = cv2.findTransformECC(
            new_small, old_small, warp, cv2.MOTION_TRANSLATION,
            (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-5), mask, 5,
        )
    except cv2.error:
        return old, coverage, (0.0, 0.0)
    dx, dy = float(warp[0, 2] / scale), float(warp[1, 2] / scale)
    if score < 0.65 or max(abs(dx), abs(dy)) > max_shift:
        return old, coverage, (0.0, 0.0)
    aligned_small = cv2.warpAffine(old_small, warp, size,
                                   flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
    valid = mask > 0
    before = _correlation(old_small, new_small, valid)
    after = _correlation(aligned_small, new_small, valid)
    if after < before + 0.005:
        return old, coverage, (0.0, 0.0)
    full_warp = np.array([[1, 0, dx], [0, 1, dy]], dtype=np.float32)
    aligned = cv2.warpAffine(old, full_warp, (width, height),
                             flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
    aligned_coverage = cv2.warpAffine(coverage, full_warp, (width, height),
                                      flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP)
    return aligned, aligned_coverage, (-dx, -dy)


def nearly_identical(old, new, coverage):
    valid = coverage > 0
    if np.count_nonzero(valid) < coverage.size * 0.35:
        return False
    differences = cv2.absdiff(old, new)
    return float(np.median(differences[valid])) < 2.0 and float(np.mean(differences[valid])) < 4.0


def inspect_candidate(normalized_polygon, old, new, coverage):
    """Return (pixels, decision, reason): accept, review, or reject."""
    height, width = new.shape[:2]
    if not isinstance(normalized_polygon, list) or not 3 <= len(normalized_polygon) <= 100:
        return None, "reject", "外周の点数が不正です"
    try:
        if any(len(pair) != 2 or not all(isinstance(value, int) and 0 <= value <= 1000 for value in pair)
               for pair in normalized_polygon):
            raise ValueError
        pixels = np.array([[round(x * (width - 1) / 1000), round(y * (height - 1) / 1000)]
                           for x, y in normalized_polygon], dtype=np.int32)
        shape = Polygon(pixels)
        if not shape.is_valid or shape.area < 64 or shape.area > width * height * 0.25:
            return None, "reject", "外周の形または面積が不正です"
        if (np.any(pixels[:, 0] <= 1) or np.any(pixels[:, 0] >= width - 2)
                or np.any(pixels[:, 1] <= 1) or np.any(pixels[:, 1] >= height - 2)):
            return None, "reject", "画像の端で建物の外周が切れています"
        # Remove subpixel-scale jagged points only when the roof area is preserved.
        simplified = cv2.approxPolyDP(pixels, 0.8, True).reshape(-1, 2)
        simplified_shape = Polygon(simplified) if len(simplified) >= 3 else None
        if (simplified_shape is not None and simplified_shape.is_valid
                and simplified_shape.symmetric_difference(shape).area <= shape.area * 0.02):
            pixels = simplified
    except (TypeError, ValueError):
        return None, "reject", "外周座標が不正です"
    mask = np.zeros((height, width), np.uint8)
    cv2.fillPoly(mask, [pixels], 255)
    area = cv2.countNonZero(mask)
    if not area or cv2.countNonZero(cv2.bitwise_and(mask, coverage)) / area < 0.98:
        return None, "reject", "過去画像に比較できない部分があります"

    old_gray, new_gray = _gray(old), _gray(new)
    old_edges = cv2.Canny(old_gray, 70, 150)
    new_edges = cv2.Canny(new_gray, 70, 150)
    inside = mask > 0
    old_count = np.count_nonzero(old_edges[inside])
    new_count = np.count_nonzero(new_edges[inside])
    if old_count >= 20 and new_count >= 20:
        kernel = np.ones((5, 5), np.uint8)
        old_near = cv2.dilate(old_edges, kernel) > 0
        new_near = cv2.dilate(new_edges, kernel) > 0
        old_match = np.count_nonzero((old_edges > 0) & new_near & inside) / old_count
        new_match = np.count_nonzero((new_edges > 0) & old_near & inside) / new_count
        if min(old_match, new_match) > 0.78:
            return pixels, "review", "過去と現在の屋根形状が似ています"
    return pixels, "accept", ""


def final_decision(quality, quality_reason, assessment):
    """A candidate is confirmed only when the image checks and second pass agree."""
    assessment = assessment if isinstance(assessment, dict) else {}
    if assessment.get("decision") == "reject":
        return "reject", str(assessment.get("reason") or "再確認で新築ではないと判断")[:240]
    kind = assessment.get("change_kind")
    if kind in ("demolition", "non_building", "unchanged"):
        return "reject", str(assessment.get("reason") or "対象の建物ではありません")[:240]
    verified = assessment.get("decision") == "new" and kind in ("new_build", "rebuild")
    decision = "new" if verified and quality == "accept" else "review"
    reasons = []
    if not verified:
        reasons.append("再確認で新築と断定できません")
    if quality_reason:
        reasons.append(quality_reason)
    if assessment.get("reason"):
        reasons.append(str(assessment["reason"])[:240])
    return decision, " / ".join(reasons) or ("建て替えを確認" if kind == "rebuild" else "過去に屋根がなく現在に新しい屋根を確認")

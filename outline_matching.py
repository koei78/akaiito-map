"""Conservative agreement check between an image roof and a map footprint."""


def footprint_agreement(roof, footprint):
    """Return an IoU score only if both polygons describe substantially the same area."""
    if roof is None or footprint is None or roof.is_empty or footprint.is_empty:
        return None
    if not roof.is_valid or not footprint.is_valid or roof.area <= 0 or footprint.area <= 0:
        return None
    if not roof.intersects(footprint):
        return None
    intersection = roof.intersection(footprint).area
    ratio = footprint.area / roof.area
    if not 0.5 <= ratio <= 2.0 or intersection / roof.area < 0.55 or intersection / footprint.area < 0.55:
        return None
    return intersection / (roof.area + footprint.area - intersection)

"""Legal/illegal judging for detected mining sites.

A site is judged by how much of its AREA lies inside active legal concessions,
not by whether its centre point does. Used by the detection pipeline and by the
`reclassify_legal` command so both always apply the same rule.
"""
import logging

logger = logging.getLogger(__name__)

# A site must be at least this much inside registered concessions to count as legal.
LEGAL_MIN_OVERLAP_PCT = 80.0


def judge_site(site, active_concessions):
    """Return (is_legal, best_concession_or_None, overlap_pct) for a site.

    - overlap >= LEGAL_MIN_OVERLAP_PCT -> legal
    - 0 < overlap < LEGAL_MIN_OVERLAP_PCT -> not legal (partial encroachment; overlap is kept)
    - no overlap -> not legal
    """
    overlap_pct, best = 0.0, None
    try:
        candidates = list(active_concessions.filter(geometry__intersects=site.geometry))
        if candidates and site.geometry.area > 0:
            union = candidates[0].geometry
            for c in candidates[1:]:
                union = union.union(c.geometry)
            overlap_pct = min(100.0, site.geometry.intersection(union).area / site.geometry.area * 100)
            # primary concession = the one covering most of the site
            best = max(candidates, key=lambda c: site.geometry.intersection(c.geometry).area)
    except Exception as exc:
        # Geometry trouble: fall back to the old centre-point rule rather than failing the job.
        logger.warning("Overlap calculation failed (%s); using centre-point rule", exc)
        best = active_concessions.filter(geometry__contains=site.geometry.centroid).first()
        overlap_pct = 100.0 if best else 0.0
    return (best is not None and overlap_pct >= LEGAL_MIN_OVERLAP_PCT), best, overlap_pct

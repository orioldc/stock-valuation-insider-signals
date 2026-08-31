"""Segment base-rate model for insider cluster conviction scoring.

Replaces hand-weighted scores with walk-forward base rates by (sector, size_tier)
plus within-sector size adjustment on logmcap and logval.

The real signal is size within sector: micro-caps beat peers 63%, large-caps 51%.
"""

from .base_rates import score_cluster, get_segment_base_rate, get_tier_benchmark_rates

__all__ = ['score_cluster', 'get_segment_base_rate', 'get_tier_benchmark_rates']

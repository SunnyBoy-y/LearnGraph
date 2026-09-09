"""Fast, deterministic SVG course covers used as the reliable cover workflow.

The function is intentionally side-effect free: it can run in a background
job after graph creation or synchronously as a fallback when a model cover is
unavailable.
"""
from __future__ import annotations

from urllib.parse import quote


def generate_graph_cover(title: str, *, progress: float = 0.0) -> str:
    seed = sum(ord(char) for char in title) % 360
    label = "".join(char for char in title[:10] if char not in "<&>\"'")
    width = max(0.0, min(1.0, progress)) * 560
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 300">
<rect width="640" height="300" fill="hsl({seed} 18% 94%)"/>
<circle cx="510" cy="120" r="82" fill="hsl({seed} 35% 78%)"/>
<path d="M70 230 Q170 90 280 210 T480 180" fill="none" stroke="hsl({seed} 30% 35%)" stroke-width="8"/>
<circle cx="250" cy="130" r="28" fill="hsl({seed} 30% 35%)"/>
<text x="36" y="52" font-family="sans-serif" font-size="24" fill="hsl({seed} 30% 25%)">{label}</text>
<rect x="40" y="260" width="560" height="10" rx="5" fill="#d8dadd"/><rect x="40" y="260" width="{width:.1f}" height="10" rx="5" fill="hsl({seed} 30% 35%)"/>
</svg>'''
    return f"data:image/svg+xml,{quote(svg)}"

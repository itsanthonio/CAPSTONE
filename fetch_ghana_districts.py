"""
fetch_ghana_districts.py
Fetches Ghana's ADM2 (district) boundaries from the GeoBoundaries API
and saves them as a GeoJSON file.

Usage:
    python fetch_ghana_districts.py

Output:
    ghana_districts.geojson  (in the same directory as this script)

Requirements:
    pip install requests
"""

import json
import requests

API_URL = "https://www.geoboundaries.org/api/current/gbOpen/GHA/ADM2/"
OUTPUT_FILE = "ghana_districts.geojson"


def fetch_ghana_districts():
    print("Fetching Ghana ADM2 (district) metadata from GeoBoundaries")
    meta_response = requests.get(API_URL, timeout=30)
    meta_response.raise_for_status()
    meta = meta_response.json()

    geojson_url = meta.get("gjDownloadURL")
    if not geojson_url:
        raise ValueError("No GeoJSON download URL found in API response.")

    print(f"Downloading district boundaries from:\n  {geojson_url}")
    geojson_response = requests.get(geojson_url, timeout=120)
    geojson_response.raise_for_status()
    geojson_data = geojson_response.json()

    feature_count = len(geojson_data.get("features", []))
    print(f"Downloaded {feature_count} district boundaries.")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(geojson_data, f, ensure_ascii=False, indent=2)

    print(f"Saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    fetch_ghana_districts()

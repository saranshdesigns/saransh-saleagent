"""
Portfolio Handler
Primary: Google Drive (via agent/drive_portfolio.py)
Fallback: Local folder E:/Drive/SaranshDesigns/Portfolio

Logo folder structure:
  /Portfolio/Logo/
    [Category folders: Agency, Clothing, FMCG, ...]
    [Uncategorized images directly in root: AL Sultan 1.1.jpg, AL Sultan 1.2.png ...]

Naming convention:
  "Brand 1.1.jpg" + "Brand 1.2.png" = one logo, two presentation views — ALWAYS sent as a pair.

Max 10 images per send (= ~5 logo pairs). Supported: JPG, PNG
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

PORTFOLIO_ROOT = Path(os.getenv("PORTFOLIO_PATH", r"E:\Drive\SaranshDesigns\Portfolio"))
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png"}
MAX_SAMPLES = 10  # max images (pairs count as 2 images)

SERVICE_FOLDER_MAP = {
    "logo": "Logo",
    "packaging": "Packaging",
    "website": "Website"
}


def _drive_active() -> bool:
    try:
        from agent.drive_portfolio import drive_available
        return drive_available()
    except Exception:
        return False


def get_samples(service: str, category: str = None, packaging_type: str = None) -> dict:
    """
    Get curated samples for a service/category.
    Uses Google Drive if configured, otherwise falls back to local folder.
    Returns: { "found": bool, "exact_match": bool, "files": [Path, ...], "message": str }
    """
    # --- Google Drive (primary) ---
    if _drive_active():
        try:
            from agent.drive_portfolio import get_drive_samples
            result = get_drive_samples(service, category, packaging_type)
            if result["found"]:
                return result
        except Exception:
            pass

    # --- Local folder (fallback) ---
    if not PORTFOLIO_ROOT.exists():
        return {"found": False, "exact_match": False, "files": [],
                "message": "Portfolio folder is not set up yet."}

    service_folder = _get_service_folder(service)

    # 1. Try exact category match first
    if category:
        cat_folder = service_folder / category.title()
        if not cat_folder.exists():
            # Try case-insensitive match
            for d in service_folder.iterdir():
                if d.is_dir() and d.name.lower() == category.lower():
                    cat_folder = d
                    break

        if cat_folder.exists():
            files = _flatten_pairs(_get_pairs(cat_folder))[:MAX_SAMPLES]
            if files:
                return {
                    "found": True,
                    "exact_match": True,
                    "files": files,
                    "message": f"Here are some of our {service} samples for {category}:"
                }

    # 2. Mixed portfolio — pick from all category subfolders + uncategorized root images
    files = _get_mixed_samples(service_folder)

    if files:
        msg = (
            f"We don't have samples specifically for '{category}' yet, but here are some of our logo work:"
            if category
            else f"Here are some of our {service} work samples:"
        )
        return {"found": True, "exact_match": False, "files": files, "message": msg}

    return {"found": False, "exact_match": False, "files": [],
            "message": f"We don't have {service} samples yet, but we can definitely create it for you!"}


def _get_mixed_samples(service_folder: Path) -> list:
    """
    Build a mixed portfolio: 1-2 pairs from each category folder + uncategorized root images.
    Always respects the 1.1/1.2 pair rule. Capped at MAX_SAMPLES images.
    """
    if not service_folder.exists():
        return []

    collected = []

    # Uncategorized images directly in the service root folder
    root_pairs = _get_pairs(service_folder, include_subdirs=False)
    for pair in root_pairs:
        collected.extend(pair)
        if len(collected) >= MAX_SAMPLES:
            break

    # Category subfolders — pick 1-2 pairs each
    if len(collected) < MAX_SAMPLES:
        for subdir in sorted(service_folder.iterdir()):
            if not subdir.is_dir():
                continue
            sub_pairs = _get_pairs(subdir)
            taken = 0
            for pair in sub_pairs:
                if len(collected) + len(pair) > MAX_SAMPLES:
                    break
                collected.extend(pair)
                taken += 1
                if taken >= 2:  # max 2 pairs per category for variety
                    break
            if len(collected) >= MAX_SAMPLES:
                break

    return collected[:MAX_SAMPLES]


def _get_pairs(folder: Path, include_subdirs: bool = False) -> list:
    """
    Scan folder for images. Returns list of pairs: [[1.1_file, 1.2_file], [single_file], ...]
    Files named 'Brand 1.1.ext' and 'Brand 1.2.ext' are grouped as one pair.
    """
    if not folder.exists():
        return []

    # Collect all image files in this folder (not subdirs unless requested)
    all_files = {}
    for f in folder.iterdir():
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS:
            all_files[f.stem] = f

    pairs = []
    processed = set()

    for stem in sorted(all_files.keys()):
        if stem in processed:
            continue
        processed.add(stem)
        f = all_files[stem]

        if stem.endswith(" 1.1"):
            base = stem[:-4]
            pair_stem = f"{base} 1.2"
            if pair_stem in all_files:
                processed.add(pair_stem)
                pairs.append([f, all_files[pair_stem]])  # always 1.1 first, then 1.2
            else:
                pairs.append([f])
        elif stem.endswith(" 1.2"):
            # Only reach here if 1.1 wasn't found (orphan 1.2)
            pairs.append([f])
        else:
            # Non-paired image
            pairs.append([f])

    return pairs


def _flatten_pairs(pairs: list) -> list:
    """Flatten list of pairs into a flat list of files."""
    result = []
    for pair in pairs:
        result.extend(pair)
    return result


def list_available_categories(service: str) -> list:
    """List all available category folders for a service."""
    if _drive_active():
        try:
            from agent.drive_portfolio import (
                get_drive_service, find_subfolder, list_subfolders,
                DRIVE_FOLDER_ID, SERVICE_FOLDER_MAP as DRIVE_MAP
            )
            svc = get_drive_service()
            folder_name = DRIVE_MAP.get(service.lower(), service.title())
            folder_id = find_subfolder(svc, DRIVE_FOLDER_ID, folder_name)
            if folder_id:
                return [sf["name"] for sf in list_subfolders(svc, folder_id)]
        except Exception:
            pass

    service_folder = _get_service_folder(service)
    if not service_folder.exists():
        return []
    return [f.name for f in service_folder.iterdir() if f.is_dir()]


def portfolio_folder_exists() -> bool:
    return _drive_active() or PORTFOLIO_ROOT.exists()


def _get_service_folder(service: str) -> Path:
    folder_name = SERVICE_FOLDER_MAP.get(service.lower(), service.title())
    return PORTFOLIO_ROOT / folder_name

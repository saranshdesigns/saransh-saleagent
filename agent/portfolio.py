"""
Portfolio Handler
Primary: Google Drive (via agent/drive_portfolio.py)
Fallback: Local folder E:/Drive/SaranshDesigns/Portfolio

Folder structure: /Portfolio/Logo/[category], /Packaging/[category], /Website
Max 10 samples per send. Supported: JPG, PNG
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

PORTFOLIO_ROOT = Path(os.getenv("PORTFOLIO_PATH", r"E:\Drive\SaranshDesigns\Portfolio"))
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png"}
MAX_SAMPLES = 10

SERVICE_FOLDER_MAP = {
    "logo": "Logo",
    "packaging": "Packaging",
    "website": "Website"
}


def _drive_active() -> bool:
    """Check if Google Drive integration is configured and ready."""
    try:
        from agent.drive_portfolio import drive_available
        return drive_available()
    except Exception:
        return False


def get_samples(service: str, category: str = None, packaging_type: str = None) -> dict:
    """
    Get curated samples for a service/category.
    Uses Google Drive if configured, otherwise falls back to local folder.
    Returns:
    {
        "found": bool,
        "exact_match": bool,
        "files": [Path, ...],  # max 10
        "message": str
    }
    """
    # --- Google Drive (primary) ---
    if _drive_active():
        try:
            from agent.drive_portfolio import get_drive_samples
            result = get_drive_samples(service, category, packaging_type)
            if result["found"] or result["message"] != "Portfolio temporarily unavailable. Please check our links below.":
                return result
        except Exception:
            pass  # Fall through to local

    # --- Local folder (fallback) ---
    if not PORTFOLIO_ROOT.exists():
        return {
            "found": False,
            "exact_match": False,
            "files": [],
            "message": "Portfolio folder is not set up yet."
        }

    if category:
        exact_folder = _get_category_folder(service, category)
        files = _get_image_files(exact_folder)
        if files:
            return {
                "found": True,
                "exact_match": True,
                "files": files[:MAX_SAMPLES],
                "message": f"Here are some of our {service} samples for {category}:"
            }

    service_folder = _get_category_folder(service)
    all_files = []

    if service_folder.exists():
        for subfolder in service_folder.iterdir():
            if subfolder.is_dir():
                sub_files = _get_image_files(subfolder)
                all_files.extend(sub_files[:2])
            elif subfolder.suffix.lower() in SUPPORTED_EXTENSIONS:
                all_files.append(subfolder)
        direct_files = _get_image_files(service_folder)
        all_files = direct_files + all_files

    all_files = all_files[:MAX_SAMPLES]

    if all_files:
        msg = (
            f"We don't have samples specifically for '{category}' yet, but here are some related {service} samples:"
            if category
            else f"Here are some of our {service} work samples:"
        )
        return {
            "found": True,
            "exact_match": False,
            "files": all_files,
            "message": msg
        }

    return {
        "found": False,
        "exact_match": False,
        "files": [],
        "message": f"We don't have {service} samples in that category yet, but we can definitely create it for you!"
    }


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

    # Local fallback
    service_folder = _get_category_folder(service)
    if not service_folder.exists():
        return []
    return [f.name for f in service_folder.iterdir() if f.is_dir()]


def portfolio_folder_exists() -> bool:
    return _drive_active() or PORTFOLIO_ROOT.exists()


# --- Local helpers ---

def _get_category_folder(service: str, category: str = None) -> Path:
    service_folder = SERVICE_FOLDER_MAP.get(service.lower(), service.title())
    base = PORTFOLIO_ROOT / service_folder
    if category:
        return base / category.title()
    return base


def _get_image_files(folder: Path) -> list:
    if not folder.exists():
        return []
    files = [
        f for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    return sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)

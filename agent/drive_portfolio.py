"""
Google Drive Portfolio Handler
Downloads portfolio images from Google Drive to local cache for WhatsApp sending.

Drive folder structure (3-level supported):
  Portfolio/ (GOOGLE_DRIVE_FOLDER_ID)
    ├── Logo/
    │     ├── Clothing/
    │     ├── FMCG/
    │     └── [other categories]
    ├── Packaging/
    │     ├── Box/
    │     ├── Label/
    │     └── Pouch Packet/
    │           ├── Chips/
    │           ├── Namkeen/
    │           └── Spices/
    └── Website/
"""

import io
import os
from pathlib import Path
from dotenv import load_dotenv

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2 import service_account

from modules.logging_config import get_logger

log = get_logger("saransh.agent.drive_portfolio")

load_dotenv()

CREDENTIALS_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials/google_service_account.json")
DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
CACHE_DIR = Path("data/portfolio_cache")
MAX_SAMPLES = 10
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

SERVICE_FOLDER_MAP = {
    "logo": "Logo",
    "packaging": "Packaging",
    "website": "Website"
}

# packaging_type keywords → matches Drive folder names like "Pouch Packet", "Box", "Label"
PACKAGING_TYPE_KEYWORDS = {
    "pouch": ["pouch"],
    "box": ["box", "carton"],
    "label": ["label", "bottle", "jar label"],
    "sachet": ["sachet", "strip"],
    "jar": ["jar"],
}

# product/business category → matches Drive folder names like "Spices", "Chips", "Clothing"
CATEGORY_KEYWORDS = {
    "spices": ["spice", "masala", "spices", "haldi", "mirchi", "jeera", "pepper", "turmeric"],
    "chips": ["chips", "wafer", "kurkure", "crisps"],
    "namkeen": ["namkeen", "snack", "farsan", "bhujia", "mixture", "chiwda"],
    "dry fruits": ["dry fruit", "dryfruit", "kaju", "almond", "cashew", "raisin", "badam"],
    "clothing": ["clothing", "fashion", "apparel", "garment", "wear", "textile", "fabric"],
    "fmcg": ["fmcg", "consumer goods", "consumer product"],
    "food": ["food", "eatery"],
    "beverages": ["juice", "drink", "beverage", "water", "sharbat", "squash", "energy drink"],
    "cosmetics": ["cosmetic", "beauty", "skin", "cream", "lotion", "makeup", "skincare"],
    "pharma": ["pharma", "medicine", "health", "supplement", "ayurvedic", "herbal"],
    "tech": ["tech", "technology", "software", "app", "digital", "it"],
    "restaurant": ["restaurant", "cafe", "dhaba", "hotel", "bakery"],
}

# When an exact category folder is not found, try this parent folder instead.
# Example: Logo/ has "FMCG" folder but not "Spices" → spices → try FMCG
CATEGORY_PARENT = {
    "spices": "fmcg",
    "chips": "fmcg",
    "namkeen": "fmcg",
    "dry fruits": "fmcg",
    "food": "fmcg",
    "beverages": "fmcg",
    "pharma": "fmcg",
    "cosmetics": "fmcg",
    "restaurant": "fmcg",
}


def _folder_matches(folder_name: str, target: str) -> bool:
    """Check if a Drive folder name matches the target concept via substring or alias."""
    folder_lower = folder_name.lower().strip()
    target_lower = target.lower().strip()

    if target_lower in folder_lower or folder_lower in target_lower:
        return True

    for key, keywords in {**PACKAGING_TYPE_KEYWORDS, **CATEGORY_KEYWORDS}.items():
        if target_lower == key or target_lower in keywords:
            for kw in keywords:
                if kw in folder_lower:
                    return True

    return False


def get_drive_service():
    """Authenticate with Service Account and return Drive API client."""
    creds_path = Path(CREDENTIALS_PATH)
    if not creds_path.exists():
        raise FileNotFoundError(f"Credentials not found: {creds_path}")
    credentials = service_account.Credentials.from_service_account_file(
        str(creds_path), scopes=SCOPES
    )
    return build("drive", "v3", credentials=credentials)


def find_subfolder(service, parent_id: str, folder_name: str):
    """Find a subfolder by exact name. Returns folder ID or None."""
    query = (
        f"'{parent_id}' in parents and "
        f"name = '{folder_name}' and "
        f"mimeType = 'application/vnd.google-apps.folder' and "
        f"trashed = false"
    )
    results = service.files().list(q=query, fields="files(id, name)").execute()
    files = results.get("files", [])
    return files[0]["id"] if files else None


def list_images(service, folder_id: str) -> list:
    """List JPG/PNG files in a folder, newest first."""
    query = (
        f"'{folder_id}' in parents and trashed = false and "
        f"(mimeType = 'image/jpeg' or mimeType = 'image/png')"
    )
    results = service.files().list(
        q=query,
        fields="files(id, name, modifiedTime)",
        orderBy="modifiedTime desc"
    ).execute()
    return results.get("files", [])


def list_subfolders(service, folder_id: str) -> list:
    """List all subfolders inside a folder."""
    query = (
        f"'{folder_id}' in parents and "
        f"mimeType = 'application/vnd.google-apps.folder' and "
        f"trashed = false"
    )
    results = service.files().list(q=query, fields="files(id, name)").execute()
    return results.get("files", [])


def _find_matching_folder(service, parent_id: str, target: str):
    """
    Find first subfolder matching the target keyword. Returns folder ID or None.
    If exact match fails, tries the parent category (e.g. spices → fmcg → FMCG folder).
    """
    subfolders = list_subfolders(service, parent_id)

    # Try direct match first
    for sf in subfolders:
        if _folder_matches(sf["name"], target):
            return sf["id"]

    # Try parent category fallback (e.g. "spices" not found → try "fmcg")
    parent = CATEGORY_PARENT.get(target.lower())
    if parent:
        for sf in subfolders:
            if _folder_matches(sf["name"], parent):
                return sf["id"]

    return None


def _apply_pair_rule(files: list) -> list:
    """
    Ensure 1.1 / 1.2 image pairs are always sent together.
    If a file named 'Brand 1.1.ext' is selected, its '1.2' counterpart must follow (and vice versa).
    A pair counts as 2 images toward MAX_SAMPLES.
    """
    name_map = {f["name"]: f for f in files}
    result = []
    seen = set()

    for f in files:
        if f["name"] in seen:
            continue
        seen.add(f["name"])
        result.append(f)

        stem = Path(f["name"]).stem  # e.g. "Brand 1.1"
        if stem.endswith(" 1.1"):
            base = stem[:-4]
            for ext in [".jpg", ".jpeg", ".png"]:
                pair_name = f"{base} 1.2{ext}"
                if pair_name in name_map and pair_name not in seen:
                    seen.add(pair_name)
                    result.append(name_map[pair_name])
                    break
        elif stem.endswith(" 1.2"):
            base = stem[:-4]
            for ext in [".jpg", ".jpeg", ".png"]:
                pair_name = f"{base} 1.1{ext}"
                if pair_name in name_map and pair_name not in seen:
                    seen.add(pair_name)
                    result.insert(len(result) - 1, name_map[pair_name])
                    break

    return result


def _collect_mixed(service, folder_id: str, per_subfolder: int = 2) -> list:
    """
    Collect mixed samples: direct images + up to per_subfolder from each subfolder.
    Handles 2 extra levels deep (for nested structures like Pouch Packet → Spices).
    """
    all_files = []

    direct = list_images(service, folder_id)
    all_files.extend(direct)

    for sf in list_subfolders(service, folder_id):
        sub_images = list_images(service, sf["id"])
        if sub_images:
            all_files.extend(sub_images[:per_subfolder])
        else:
            # Go one more level deep (e.g. Packaging → Pouch Packet → Spices)
            for ssf in list_subfolders(service, sf["id"]):
                ss_images = list_images(service, ssf["id"])
                all_files.extend(ss_images[:per_subfolder])

    return _apply_pair_rule(all_files)[:MAX_SAMPLES]


def download_to_cache(service, file_id: str, file_name: str) -> Path:
    """Download a Drive file to local cache. Skips download if already cached."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    ext = Path(file_name).suffix.lower() or ".jpg"
    cache_path = CACHE_DIR / f"{file_id}{ext}"

    if cache_path.exists():
        return cache_path

    request = service.files().get_media(fileId=file_id)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()

    with open(cache_path, "wb") as f:
        f.write(buffer.getvalue())

    return cache_path


def _to_paths(service, files: list) -> list:
    """Download files to cache and return local Paths."""
    return [download_to_cache(service, f["id"], f["name"]) for f in files]


def _merge_root_images(service, service_folder_id: str, primary_files: list) -> list:
    """
    Append direct (uncategorized) images from service folder root to primary list.
    These are images placed directly inside Logo/ or Packaging/ without any subfolder —
    they belong to misc/one-off projects and should always be included in samples.
    Deduplicates and trims to MAX_SAMPLES.
    """
    root_images = list_images(service, service_folder_id)
    if not root_images:
        return primary_files
    seen_ids = {f["id"] for f in primary_files}
    for f in root_images:
        if f["id"] not in seen_ids:
            primary_files.append(f)
            seen_ids.add(f["id"])
    return primary_files[:MAX_SAMPLES]


def get_drive_samples(service_name: str, category: str = None, packaging_type: str = None) -> dict:
    """
    Smart 3-level portfolio search from Google Drive.

    Packaging flow:
      packaging_type → type folder (e.g. "pouch" → "Pouch Packet")
        → category subfolder (e.g. "spices" → "Spices") → images

    Logo / Website flow:
      category → category folder (e.g. "clothing" → "Clothing") → images

    Falls back to mixed samples from all subfolders if exact match not found.
    """
    if not DRIVE_FOLDER_ID:
        return {"found": False, "exact_match": False, "files": [], "message": "Drive not configured."}

    try:
        svc = get_drive_service()

        service_folder_name = SERVICE_FOLDER_MAP.get(service_name.lower(), service_name.title())
        service_folder_id = find_subfolder(svc, DRIVE_FOLDER_ID, service_folder_name)

        if not service_folder_id:
            return {
                "found": False, "exact_match": False, "files": [],
                "message": f"We don't have {service_name} samples yet, but we can definitely create it for you!"
            }

        # === PACKAGING: 3-level search (Service → Type → Category) ===
        if service_name.lower() == "packaging" and packaging_type:
            type_folder_id = _find_matching_folder(svc, service_folder_id, packaging_type)

            if type_folder_id:
                # Try exact category match inside type folder
                if category:
                    cat_folder_id = _find_matching_folder(svc, type_folder_id, category)
                    if cat_folder_id:
                        files = list_images(svc, cat_folder_id)
                        if files:
                            # Merge uncategorized root images (directly in Packaging/)
                            files = _apply_pair_rule(_merge_root_images(svc, service_folder_id, files))
                            return {
                                "found": True, "exact_match": True,
                                "files": _to_paths(svc, files[:MAX_SAMPLES]),
                                "message": f"Here are our {packaging_type} packaging samples for {category}:"
                            }

                # Category not found (or not specified) → mix from type folder
                mixed = _collect_mixed(svc, type_folder_id)
                if mixed:
                    return {
                        "found": True, "exact_match": False,
                        "files": _to_paths(svc, mixed),
                        "message": f"Here are some of our {packaging_type} packaging samples:"
                    }

        # === LOGO / WEBSITE: 2-level search (Service → Category) ===
        if category:
            cat_folder_id = _find_matching_folder(svc, service_folder_id, category)
            if cat_folder_id:
                files = list_images(svc, cat_folder_id)
                if files:
                    # Merge uncategorized root images (directly in Logo/ or Website/)
                    files = _apply_pair_rule(_merge_root_images(svc, service_folder_id, files))
                    return {
                        "found": True, "exact_match": True,
                        "files": _to_paths(svc, files[:MAX_SAMPLES]),
                        "message": f"Here are our {service_name} samples for {category}:"
                    }

        # === General fallback: mix from all subfolders ===
        mixed = _collect_mixed(svc, service_folder_id)
        if mixed:
            msg = (
                f"We don't have samples for that specific category yet, "
                f"but here are some of our {service_name} work samples:"
                if category
                else f"Here are some of our {service_name} work samples:"
            )
            return {"found": True, "exact_match": False, "files": _to_paths(svc, mixed), "message": msg}

        return {
            "found": False, "exact_match": False, "files": [],
            "message": f"We don't have {service_name} samples in that category yet, but we can definitely create it for you!"
        }

    except Exception as e:
        log.warning("drive.error", error=str(e))
        return {
            "found": False, "exact_match": False, "files": [],
            "message": "Portfolio temporarily unavailable. Please check our links below."
        }


def drive_available() -> bool:
    """True if Drive folder ID is set and credentials file exists."""
    return bool(DRIVE_FOLDER_ID) and Path(CREDENTIALS_PATH).exists()

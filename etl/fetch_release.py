import hashlib
import logging
import os
import subprocess
from pathlib import Path
import httpx
from tqdm import tqdm
from etl.r2_sync import sync_to_r2

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

REPO = "scriptogre/romanian-law-data"
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))


def get_latest_release(repo: str = REPO) -> dict:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    logger.info(f"Fetching latest release metadata from {url}...")
    headers = {"User-Agent": "Antigravity-Lex/1.0", "Accept": "application/vnd.github.v3+json"}
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"

    with httpx.Client(timeout=30.0, headers=headers, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.json()


def download_file(url: str, dest_path: Path, expected_size: int | None = None) -> bool:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")

    headers = {"User-Agent": "Antigravity-Lex/1.0"}
    with httpx.Client(timeout=300.0, headers=headers, follow_redirects=True) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0)) or expected_size or 0
            with (
                open(temp_path, "wb") as f,
                tqdm(
                    total=total,
                    unit="B",
                    unit_scale=True,
                    unit_divisor=1024,
                    desc=dest_path.name,
                    ncols=90,
                ) as pbar,
            ):
                for chunk in resp.iter_bytes(chunk_size=4 * 1024 * 1024):
                    f.write(chunk)
                    pbar.update(len(chunk))

    temp_path.replace(dest_path)
    return True


def verify_sha256(file_path: Path, expected_hash: str) -> bool:
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(4 * 1024 * 1024):
            sha256.update(chunk)
    actual_hash = sha256.hexdigest()
    if actual_hash.lower() != expected_hash.lower():
        logger.error(f"Checksum mismatch for {file_path.name}: expected {expected_hash}, got {actual_hash}")
        return False
    logger.info(f"Verified {file_path.name} (SHA256: {actual_hash[:8]}...)")
    return True


def reassemble_fts_duckdb(data_dir: Path = DATA_DIR):
    part00 = data_dir / "fts.duckdb.zst.part-00"
    part01 = data_dir / "fts.duckdb.zst.part-01"
    output_duckdb = data_dir / "fts.duckdb"

    if not (part00.exists() and part01.exists()):
        logger.warning("FTS parts not found, skipping DuckDB FTS reassembly.")
        return

    logger.info("Reassembling and decompressing DuckDB Full-Text Search index (zstd)...")
    cmd = f"cat '{part00}' '{part01}' | zstd -d -f -o '{output_duckdb}'"
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if res.returncode != 0:
        logger.error(f"Failed to decompress FTS DuckDB: {res.stderr}")
        raise RuntimeError(f"zstd decompression failed: {res.stderr}")
    logger.info(f"DuckDB FTS successfully extracted to {output_duckdb} ({output_duckdb.stat().st_size / (1024*1024):.2f} MB)")


def fetch_and_sync(upload_to_r2: bool = True):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    release = get_latest_release()
    tag_name = release.get("tag_name")
    assets = release.get("assets", [])
    logger.info(f"Found release '{tag_name}' with {len(assets)} assets.")

    # 1. Download documents.sha256 first if present
    sha_asset = next((a for a in assets if a["name"] == "documents.sha256"), None)
    checksums = {}
    if sha_asset:
        sha_file = DATA_DIR / "documents.sha256"
        logger.info(f"Downloading {sha_asset['name']}...")
        download_file(sha_asset["browser_download_url"], sha_file, sha_asset.get("size"))
        with open(sha_file, "r") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 2:
                    checksums[parts[1]] = parts[0]

    # 2. Download all assets
    downloaded_files = []
    for asset in assets:
        filename = asset["name"]
        dest = DATA_DIR / filename
        logger.info(f"Downloading asset: {filename} ({asset.get('size', 0) / (1024*1024):.2f} MB)")
        download_file(asset["browser_download_url"], dest, asset.get("size"))
        downloaded_files.append(dest)

        # Verify checksum if present
        if filename in checksums:
            if not verify_sha256(dest, checksums[filename]):
                raise ValueError(f"Integrity check failed for {filename}")

    # 3. Reassemble FTS DuckDB index
    reassemble_fts_duckdb(DATA_DIR)

    # Copy create_views.sql to root if present in data/
    if (DATA_DIR / "create_views.sql").exists():
        import shutil
        shutil.copy2(DATA_DIR / "create_views.sql", Path("create_views.sql"))

    logger.info("All release assets downloaded and verified successfully!")

    # 4. Upload to Cloudflare R2
    if upload_to_r2:
        logger.info("Initiating upload to Cloudflare R2...")
        sync_to_r2()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Fetch Romanian Law Data release and sync to R2")
    parser.add_argument("--no-upload", action="store_true", help="Download only without uploading to R2")
    args = parser.parse_args()

    fetch_and_sync(upload_to_r2=not args.no_upload)

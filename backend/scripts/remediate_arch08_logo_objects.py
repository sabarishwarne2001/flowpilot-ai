import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings
from app.db.session import SessionLocal

UPLOAD_DIR = settings.UPLOAD_DIR


def main():
    print("=" * 70)
    print("FLOWPILOT AI -- ARCH-08 LOGO OBJECT REMEDIATION SCRIPT")
    print("=" * 70)

    db = SessionLocal()
    remediated = []

    try:
        res = db.execute(
            text(
                "SELECT id, file_path, original_filename, mime_type, file_size, checksum_sha256 "
                "FROM uploaded_files "
                "WHERE file_size = 0 OR checksum_sha256 = '0000000000000000000000000000000000000000000000000000000000000000';"
            )
        )
        rows = res.fetchall()
        print(f"[*] Found {len(rows)} logo record(s) requiring binary remediation.")

        for row in rows:
            file_id, file_path, orig_name, old_mime, old_size, old_sha = row
            disk_path = UPLOAD_DIR / file_path

            if not disk_path.exists():
                print(f"[WARN] File not found on disk: {disk_path}. Skipping.")
                continue

            content = disk_path.read_bytes()
            real_size = len(content)
            real_sha = hashlib.sha256(content).hexdigest()

            # Detect real image MIME type using PIL
            real_mime = old_mime
            try:
                with Image.open(disk_path) as img:
                    fmt = img.format.lower() if img.format else "png"
                    real_mime = f"image/{fmt}" if fmt != "jpeg" else "image/jpeg"
            except Exception:
                pass

            db.execute(
                text(
                    "UPDATE uploaded_files "
                    "SET mime_type = :mime, file_size = :size, checksum_sha256 = :sha "
                    "WHERE id = :id;"
                ),
                {"mime": real_mime, "size": real_size, "sha": real_sha, "id": file_id},
            )

            remediated.append(
                {
                    "id": str(file_id),
                    "file_path": file_path,
                    "old_mime": old_mime,
                    "new_mime": real_mime,
                    "size_bytes": real_size,
                    "sha256": real_sha,
                }
            )

        db.commit()
    finally:
        db.close()

    evidence_dir = Path("arch08_evidence")
    evidence_dir.mkdir(exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    evidence_file = evidence_dir / f"logo-remediation-{timestamp}.json"
    evidence_file.write_text(json.dumps(remediated, indent=2), encoding="utf-8")

    print(f"[PASS] Successfully remediated {len(remediated)} logo object(s). Evidence: {evidence_file}")


if __name__ == "__main__":
    main()
from pathlib import Path
import re
import uuid
from fastapi import UploadFile


SAFE_FILENAME_PATTERN = re.compile(r"[^a-zA-Z0-9_.-]+")


def sanitize_filename(filename: str) -> str:
    """
    Keep filenames safe for local storage.
    Example: 'My Course Notes.pdf' -> 'My_Course_Notes.pdf'
    """
    name = filename.strip().replace(" ", "_")
    name = SAFE_FILENAME_PATTERN.sub("_", name)
    return name or "uploaded_file.pdf"


def create_document_id() -> str:
    return str(uuid.uuid4())


async def save_upload_file(upload_file: UploadFile, destination: Path, max_size_bytes: int) -> int:
    """
    Save an uploaded file in chunks and enforce a maximum size.
    Returns the number of bytes written.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)

    total_size = 0
    chunk_size = 1024 * 1024

    with destination.open("wb") as output_file:
        while True:
            chunk = await upload_file.read(chunk_size)
            if not chunk:
                break

            total_size += len(chunk)
            if total_size > max_size_bytes:
                output_file.close()
                destination.unlink(missing_ok=True)
                raise ValueError("Uploaded file is too large.")

            output_file.write(chunk)

    return total_size

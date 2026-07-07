"""
File validation, sanitization, and path-security utility helpers for FlowPilot AI.

Enforces absolute path boundaries to block directory traversal attacks and handles 
secure, unique disk filename generation.
"""

import os
import uuid
from app.core.config import settings


def initialize_storage() -> None:
    """
    Ensures that the target system storage directory is built and active on disk.
    
    Creates the directory path recursively if it does not exist.
    """
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)


def sanitize_filename(filename: str) -> str:
    """
    Sanitizes raw client-side filenames by removing unsafe path characters.
    
    Extracts the base name to isolate the file from potential relative path arguments.
    """
    # Isolate name from folder paths to block traversal vectors
    base_name = os.path.basename(filename)
    
    # Restrict characters strictly to safe, alphanumeric, or base symbols
    cleaned_name = "".join(c for c in base_name if c.isalnum() or c in "._-")
    return cleaned_name


def generate_secure_filename(original_filename: str) -> str:
    """
    Constructs a cryptographically secure system filename using UUIDv4.
    
    Extracts the lowercase extension from the original file to standardize 
    the system storage key format.
    """
    sanitized = sanitize_filename(original_filename)
    _, ext = os.path.splitext(sanitized)

    if not ext:
        raise ValueError("Uploaded file must have a valid extension.")

    clean_ext = ext.lower()
    return f"{uuid.uuid4()}{clean_ext}"


def get_safe_path(stored_filename: str) -> str:
    """
    Resolves the absolute path for a stored file and verifies directory boundaries.
    
    Guarantees that target operations take place strictly within settings.UPLOAD_DIR.
    
    Raises:
        ValueError: If directory traversal injection or boundary violations are detected.
    """
    # Resolve absolute paths for folder boundaries
    base_dir = os.path.abspath(settings.UPLOAD_DIR)
    target_path = os.path.abspath(os.path.join(base_dir, stored_filename))
    
    # Enforce that the target absolute path starts with the base directory path
    if not target_path.startswith(base_dir + os.sep) and target_path != base_dir:
        raise ValueError("Directory traversal attempt detected.")
        
    return target_path
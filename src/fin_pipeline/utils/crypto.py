import hashlib

def calculate_file_hash(file_path: str) -> str:
    """Generates deterministic SHA256 hashes used to prevent duplicate entries inside database tables."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            sha256.update(chunk)
    return sha256.hexdigest()

"""Compress all files in the data directory using gzip."""

from pathlib import Path
import gzip
import shutil


def gzip_file(path: Path) -> None:
    """Stream `path` into a gzipped copy then remove the original."""
    gz_path = path.with_name(path.name + ".gz")
    if gz_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing file: {gz_path}")

    with path.open("rb") as src, gzip.open(gz_path, "wb") as dst:
        shutil.copyfileobj(src, dst)

    path.unlink()


def main() -> None:
    data_dir = Path("data")
    for path in data_dir.iterdir():
        if path.is_file() and not path.name.endswith(".gz"):
            gzip_file(path)


if __name__ == "__main__":
    main()

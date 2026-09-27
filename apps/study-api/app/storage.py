"""Object storage adapter. Business code uses keys, never filesystem paths.

LocalStorage keeps objects under one directory. Writes go to a temporary file and are
renamed into place, so a reader never sees half an object. An S3/MinIO adapter would
implement the same methods; `local_path` would then download to a temporary file.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile


class StorageError(RuntimeError):
    pass


class LocalStorage:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key):
        clean = PurePosixPath(key)
        if clean.is_absolute() or '..' in clean.parts or not clean.parts:
            raise StorageError('Invalid object key')
        return self.root.joinpath(*clean.parts)

    def exists(self, key):
        return self._path(key).is_file()

    def put_bytes(self, key, data: bytes):
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=target.parent, prefix='.put-')
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(data)
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def put_json(self, key, value):
        self.put_bytes(key, json.dumps(value).encode())

    def move_in(self, source: Path, key):
        """Adopt a finished local file (for example a verified upload) as an object."""
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(source, target)
        except OSError:
            shutil.copyfile(source, target)
            Path(source).unlink(missing_ok=True)

    def copy_in(self, source: Path, key):
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=target.parent, prefix='.put-')
        os.close(fd)
        try:
            shutil.copyfile(source, temporary)
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def read_bytes(self, key):
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError:
            raise StorageError(f'Object is missing: {key}') from None

    def read_text(self, key, encoding='utf-8', errors='strict'):
        return self.read_bytes(key).decode(encoding, errors)

    def read_json(self, key, default=None):
        if not key or not self.exists(key):
            return default
        return json.loads(self.read_bytes(key))

    @contextmanager
    def local_path(self, key):
        path = self._path(key)
        if not path.is_file():
            raise StorageError(f'Object is missing: {key}')
        yield path

    def delete(self, key):
        # Idempotent: deleting a missing object is success.
        self._path(key).unlink(missing_ok=True)

    def staging_file(self):
        directory = self.root/'staging'
        directory.mkdir(parents=True, exist_ok=True)
        fd, path = tempfile.mkstemp(dir=directory, prefix='upload-')
        os.close(fd)
        return Path(path)

    def staging_key(self, path: Path):
        return 'staging/'+Path(path).name

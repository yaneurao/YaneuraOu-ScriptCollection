"""Write a result alongside its destination and publish it only on success."""

from contextlib import contextmanager
import os
from pathlib import Path
import tempfile


@contextmanager
def atomic_binary_output(destination: Path):
    fd, name = tempfile.mkstemp(prefix=f'.{destination.name}.', suffix='.tmp', dir=destination.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            yield stream
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)

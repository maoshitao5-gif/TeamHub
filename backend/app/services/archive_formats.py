"""Explicit archive object classification; Office ZIP packages stay documents."""
from pathlib import PurePath

_SUFFIXES = (
    ('.tar.gz', 'tar.gz'), ('.tar.bz2', 'tar.bz2'), ('.tar.xz', 'tar.xz'),
    ('.tgz', 'tar.gz'), ('.tbz2', 'tar.bz2'), ('.tbz', 'tar.bz2'),
    ('.txz', 'tar.xz'), ('.zip', 'zip'), ('.tar', 'tar'),
    ('.7z', '7z'), ('.rar', 'rar'), ('.gz', 'gz'), ('.bz2', 'bz2'), ('.xz', 'xz'),
)
ARCHIVE_SUFFIXES = frozenset(suffix for suffix, _ in _SUFFIXES)


def archive_kind(filename):
    if not isinstance(filename, str):
        return None
    lowered = filename.casefold()
    return next((kind for suffix, kind in _SUFFIXES if lowered.endswith(suffix)), None)


def standalone_member_name(filename):
    name = PurePath(filename).name
    return name.rsplit('.', 1)[0]

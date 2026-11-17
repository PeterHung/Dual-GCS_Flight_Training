"""Private, persistent snapshots for an install/update rollback."""
from datetime import datetime
import json
import os
from pathlib import Path
import stat
import tempfile

BACKUP_ROOT = Path('/var/backups/gcs')


class Backup:
    def __init__(self, paths, label, root=BACKUP_ROOT):
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory = Path(tempfile.mkdtemp(
            prefix=datetime.now().strftime('%Y%m%d-%H%M%S-') + label + '-', dir=root))
        self.entries = []
        for path in dict.fromkeys(paths):
            path = Path(path)
            if path.is_symlink():
                raise RuntimeError(f'Refusing to replace a symlink: {path}')
            entry = {'path': str(path), 'exists': path.exists()}
            if path.exists():
                info = path.stat()
                saved = self.directory / str(len(self.entries))
                saved.write_bytes(path.read_bytes())
                saved.chmod(0o600)
                entry.update(saved=saved.name, mode=stat.S_IMODE(info.st_mode),
                             uid=info.st_uid, gid=info.st_gid)
            self.entries.append(entry)
        self.save_json('files.json', self.entries)

    def save_json(self, name, value):
        path = self.directory / name
        path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
        path.chmod(0o600)

    def restore(self):
        for entry in self.entries:
            path = Path(entry['path'])
            if not entry['exists']:
                path.unlink(missing_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                try:
                    stream.write((self.directory / entry['saved']).read_bytes())
                    stream.close()
                    temporary.chmod(entry['mode'])
                    os.chown(temporary, entry['uid'], entry['gid'])
                    temporary.replace(path)
                finally:
                    temporary.unlink(missing_ok=True)

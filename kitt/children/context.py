from pathlib import Path

def validate_child_paths(root_dir, allowed_paths):
    root = Path(root_dir).resolve()
    out = []
    for value in allowed_paths:
        target = (root / value).resolve()
        if not target.is_relative_to(root):
            raise ValueError("Child path escapes workspace")
        out.append(str(target.relative_to(root)))
    return out


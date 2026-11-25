"""Removes the original's leftover v2 YAML block from the file it was loaded from.

The edit is textual, so comments, `!secret`, `!include` and the layout of everything else stay as
written. It only goes ahead when the result, parsed again, equals the old file without that one
list item (and without its `camera:` key when nothing else is under it). A backup copy of the file
is written first, next to it.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import yaml

from .const import ORIGINAL_DOMAIN

BACKUP_SUFFIX = ".better-xiaomi-backup-"


class OriginalYamlRemovalError(Exception):
    """The block can't be removed safely; the user removes it by hand."""


@dataclass(frozen=True, slots=True)
class RemovalResult:
    file: str
    backup: str


class _Loader(yaml.SafeLoader):
    """Reads Home Assistant's YAML without resolving its tags (`!secret`, `!include`, ...)."""


def _tagged(loader: _Loader, tag: str, node: yaml.Node) -> tuple[str, Any]:
    if isinstance(node, yaml.MappingNode):
        return (tag, loader.construct_mapping(node, deep=True))
    if isinstance(node, yaml.SequenceNode):
        return (tag, loader.construct_sequence(node, deep=True))
    return (tag, loader.construct_scalar(node))


_Loader.add_multi_constructor("!", _tagged)


def _compose(text: str) -> tuple[_Loader, yaml.Node | None]:
    loader = _Loader(text)
    try:
        return loader, loader.get_single_node()
    except yaml.YAMLError as err:
        raise OriginalYamlRemovalError(f"cannot parse: {err}") from err


def _construct(text: str) -> Any:
    loader, node = _compose(text)
    try:
        return None if node is None else loader.construct_document(node)
    finally:
        loader.dispose()


def _platform(node: yaml.Node) -> str | None:
    if not isinstance(node, yaml.MappingNode):
        return None
    for key, value in node.value:
        if isinstance(key, yaml.ScalarNode) and key.value == "platform" and isinstance(value, yaml.ScalarNode):
            return value.value
    return None


def _items(node: yaml.Node, parent: tuple[yaml.Node, yaml.Node] | None = None):
    """Every (sequence, item, key/value pair holding the sequence) under node."""
    if isinstance(node, yaml.SequenceNode):
        for item in node.value:
            yield node, item, parent
            yield from _items(item)
    elif isinstance(node, yaml.MappingNode):
        for pair in node.value:
            yield from _items(pair[1], pair)


def _mappings(node: yaml.Node):
    if isinstance(node, yaml.MappingNode):
        yield node
        for _, value in node.value:
            yield from _mappings(value)
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            yield from _mappings(item)


def _last_line(node: yaml.Node) -> int:
    """The 0-based line where node's text ends (a block collection's own end mark runs on)."""
    if isinstance(node, yaml.MappingNode) and node.value and not node.flow_style:
        return _last_line(node.value[-1][1])
    if isinstance(node, yaml.SequenceNode) and node.value and not node.flow_style:
        return _last_line(node.value[-1])
    end = node.end_mark
    return end.line - 1 if end.column == 0 and end.line > node.start_mark.line else end.line


def _find(root: yaml.Node, line: int | None, host: Any, token: Any):
    candidates = [
        found for found in _items(root) if _platform(found[1]) == ORIGINAL_DOMAIN
    ]
    if line is not None:
        at_line = [found for found in candidates if found[1].start_mark.line == line - 1]
        if len(at_line) == 1:
            return at_line[0]

    def scalar(item: yaml.MappingNode, key: str) -> str | None:
        for k, v in item.value:
            if k.value == key and isinstance(v, yaml.ScalarNode) and v.tag.startswith("tag:yaml.org"):
                return v.value
        return None

    matching = [
        found for found in candidates
        if (host is not None and scalar(found[1], "host") == str(host))
        or (token is not None and scalar(found[1], "token") == str(token))
    ]
    if len(matching) == 1:
        return matching[0]
    raise OriginalYamlRemovalError("block not found in the file")


def remove_block_text(text: str, line: int | None, host: Any = None, token: Any = None) -> str:
    """The file's text without the original's list item at line (1-based), checked by parsing."""
    loader, root = _compose(text)
    if root is None:
        raise OriginalYamlRemovalError("empty file")
    sequence, item, parent = _find(root, line, host, token)
    lines = text.splitlines(keepends=True)

    start = item.start_mark.line
    before = lines[start][: item.start_mark.column]
    if before.strip() == "":
        # A lone "-" on the line above.
        start -= 1
        if start < 0 or lines[start].strip() != "-":
            raise OriginalYamlRemovalError("unexpected layout")
    elif before.strip() != "-" or sequence.flow_style:
        raise OriginalYamlRemovalError("unexpected layout")
    end = _last_line(item)
    remove = set(range(start, end + 1))

    sequence.value.remove(item)
    root_emptied = False
    if not sequence.value:
        if parent is not None:
            key = parent[0]
            if key.start_mark.line >= start or lines[key.start_mark.line].split("#")[0].strip() != f"{key.value}:":
                raise OriginalYamlRemovalError("unexpected layout of the key")
            remove.add(key.start_mark.line)
            holder = next(found for found in _mappings(root) if parent in found.value)
            holder.value.remove(parent)
        elif sequence is root:
            root_emptied = True
    expected = loader.construct_document(root)
    loader.dispose()

    new = "".join(text_line for index, text_line in enumerate(lines) if index not in remove)
    if root_emptied and not any(
        text_line.strip() and not text_line.lstrip().startswith("#") for text_line in new.splitlines()
    ):
        new = new + ("" if new.endswith("\n") or not new else "\n") + "[]\n"
    # A file left with comments only reads as nothing.
    if (_construct(new) or type(expected)()) != expected:
        raise OriginalYamlRemovalError("the result would differ from the file without the block")
    return new


def remove_block(config_dir: str, file: str, line: int | None, host: Any = None, token: Any = None) -> RemovalResult:
    """Remove the block from file (relative to config_dir), after writing a backup next to it."""
    root = os.path.realpath(config_dir)
    path = os.path.realpath(os.path.join(root, file))
    if os.path.commonpath([root, path]) != root or not os.path.isfile(path):
        raise OriginalYamlRemovalError("file outside the configuration folder")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    new = remove_block_text(text, line, host, token)

    backup = f"{path}{BACKUP_SUFFIX}{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(path, backup)
    directory = os.path.dirname(path)
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".better-xiaomi-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(new)
        shutil.copymode(path, temporary)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise
    return RemovalResult(os.path.relpath(path, root), os.path.relpath(backup, root))

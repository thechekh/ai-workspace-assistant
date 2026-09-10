"""Which files count as documentation and which as source code.

One definition for the upload endpoint, the repository ingester, the folder
CLI and the search tool's "open the file" hint — they used to carry three
slightly different sets, and a suffix accepted by one path was silently
skipped by another.
"""

import posixpath

# Prose the knowledge base is built from.
DOC_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".rst"})

# Source code the sparse lexical vector can genuinely match on (identifiers,
# function names). Config/lockfile formats are left out on purpose: a lockfile
# is thousands of lines nobody asks questions about.
CODE_SUFFIXES = frozenset(
    {
        ".py",
        ".pyi",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".vue",
        ".go",
        ".rs",
        ".rb",
        ".java",
        ".kt",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cs",
        ".php",
        ".sql",
        ".sh",
        ".yml",
        ".yaml",
        ".toml",
    }
)


def suffix_of(path: str) -> str:
    """The lowercased extension of a path, `""` when it has none."""
    return posixpath.splitext(path)[1].lower()


def is_code_path(path: str) -> bool:
    return suffix_of(path) in CODE_SUFFIXES


def is_doc_path(path: str) -> bool:
    return suffix_of(path) in DOC_SUFFIXES

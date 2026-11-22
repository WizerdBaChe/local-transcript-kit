"""The store: this product's own disk vocabulary, and create-only writes.

R8 is the ruling this package exists to honour: the separation from the other
product is ON DISK, not only in code. Not one directory name, subfolder name or
marker filename is shared, and neither product reads the other's tree at runtime.
`layout.py` is the only place those names appear, so the R8 check has one file to
read rather than a codebase to search.
"""

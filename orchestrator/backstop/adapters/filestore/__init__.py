"""File-store adapters: scoped, TTL-bounded, principal-gated artifact bytes.

Houses the local path-jailed adapter
(:class:`~backstop.adapters.filestore.local_filestore_adapter.LocalFileStoreAdapter`)
and the S3 adapter
(:class:`~backstop.adapters.filestore.s3_filestore_adapter.S3FileStoreAdapter`),
both implementing :class:`backstop.ports.file_store_port.FileStorePort`. Artifacts
are handed back as opaque :class:`~backstop.ports.file_store_port.ArtifactRef`
handles -- never a filesystem path -- so no user-controlled path reaches the
store.
"""

from __future__ import annotations

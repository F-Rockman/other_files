//! Blob data container, mirrors `struct fdb_blob` and helpers from
//! `src/fdb_utils.c`.
//!
//! A [`Blob`] owns its read/write buffer and tracks the on-flash location
//! where its data is persisted.  The C API uses a borrowed `void *buf`; the
//! Rust port owns the buffer (`Vec<u8>`) so lifetime management is simpler
//! and safe.

use crate::def::FdbTime;

/// On-flash saved metadata for a blob, mirrors the nested `saved` struct in
/// `struct fdb_blob`.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct BlobSaved {
    /// Saved KV or TSL index address.
    pub meta_addr: u32,
    /// Address where blob data is stored on flash.
    pub addr: u32,
    /// Length of blob data saved on flash.
    pub len: usize,
}

/// Blob object, mirrors `struct fdb_blob`.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Blob {
    /// Owned data buffer.
    pub buf: Vec<u8>,
    /// On-flash saved metadata.
    pub saved: BlobSaved,
}

impl Blob {
    /// Create a new empty blob with the given buffer capacity.
    pub fn with_capacity(cap: usize) -> Self {
        Self {
            buf: Vec::with_capacity(cap),
            saved: BlobSaved::default(),
        }
    }

    /// Create a blob from an existing buffer, mirrors `fdb_blob_make`.
    ///
    /// The blob takes ownership of `buf`; `size` is the logical length
    /// (may be less than `buf.len()` when the buffer has spare capacity).
    pub fn make(buf: Vec<u8>, size: usize) -> Self {
        debug_assert!(
            size <= buf.len(),
            "blob size {size} exceeds buffer length {}",
            buf.len()
        );
        Self {
            buf,
            saved: BlobSaved {
                meta_addr: 0,
                addr: 0,
                len: size,
            },
        }
    }

    /// Logical size of the blob data (not buffer capacity).
    #[inline]
    pub fn size(&self) -> usize {
        self.saved.len
    }

    /// Buffer slice containing the blob data.
    #[inline]
    pub fn data(&self) -> &[u8] {
        &self.buf[..self.saved.len.min(self.buf.len())]
    }

    /// Mutable buffer slice containing the blob data.
    #[inline]
    pub fn data_mut(&mut self) -> &mut [u8] {
        let end = self.saved.len.min(self.buf.len());
        &mut self.buf[..end]
    }

    /// Reset the saved metadata to default (zeroed).
    pub fn reset_saved(&mut self) {
        self.saved = BlobSaved::default();
    }

    /// Resize the buffer, keeping `saved.len` in sync when growing.
    pub fn resize(&mut self, new_size: usize) {
        self.buf.resize(new_size, 0);
        self.saved.len = new_size;
    }

    /// Returns `true` when the blob has no saved on-flash data yet.
    #[inline]
    pub fn is_unsaved(&self) -> bool {
        self.saved.len == 0 && self.saved.addr == 0
    }
}

/// Timestamp sentinel value for unused TSL entries.
///
/// Kept here for backwards compatibility with C callers that referenced
/// `FDB_TIME_UNUSED`; canonical definition lives in [`crate::def`].
#[inline]
pub fn time_unused() -> FdbTime {
    crate::def::TIME_UNUSED
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn make_sets_size_and_owns_buffer() {
        let buf = vec![0u8; 32];
        let blob = Blob::make(buf, 16);
        assert_eq!(blob.size(), 16);
        assert_eq!(blob.data().len(), 16);
        assert_eq!(blob.buf.len(), 32);
    }

    #[test]
    fn with_capacity_starts_empty() {
        let blob = Blob::with_capacity(64);
        assert_eq!(blob.size(), 0);
        assert!(blob.data().is_empty());
        assert!(blob.is_unsaved());
    }

    #[test]
    fn reset_saved_clears_metadata() {
        let mut blob = Blob::make(vec![1, 2, 3, 4], 4);
        blob.saved.addr = 0x1000;
        blob.saved.meta_addr = 0x2000;
        blob.saved.len = 4;
        assert!(!blob.is_unsaved());
        blob.reset_saved();
        assert!(blob.is_unsaved());
    }

    #[test]
    fn resize_grows_and_updates_len() {
        let mut blob = Blob::with_capacity(8);
        blob.resize(16);
        assert_eq!(blob.size(), 16);
        assert_eq!(blob.buf.len(), 16);
        assert_eq!(blob.data().len(), 16);
    }

    #[test]
    fn data_mut_allows_in_place_write() {
        let mut blob = Blob::make(vec![0, 0, 0], 3);
        for b in blob.data_mut() {
            *b = 0xAB;
        }
        assert_eq!(blob.data(), &[0xAB, 0xAB, 0xAB]);
    }
}

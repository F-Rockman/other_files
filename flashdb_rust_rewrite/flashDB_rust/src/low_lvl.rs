//! Low-level helpers mirroring `inc/fdb_low_lvl.h` and `src/fdb_low_lvl.c`.
//!
//! This module hosts flash-adjacent utilities: write-granularity alignment,
//! status table encoding/decoding, and erased-byte constants.  Only the
//! alignment helpers are populated in this pass; status table helpers will
//! be filled in by subsequent focuses.

use crate::def::WRITE_GRAN;

/// Erased flash byte (flash reads as all-ones after erase).
pub const BYTE_ERASED: u8 = 0xFF;
/// Written flash byte (a programmed cell reads as zero).
pub const BYTE_WRITTEN: u8 = 0x00;
/// Sentinel value representing "unused" 32-bit data on flash.
pub const DATA_UNUSED: u32 = 0xFFFF_FFFF;
/// Invalid address sentinel, mirrors `FDB_FAILED_ADDR`.
pub const FAILED_ADDR: u32 = 0xFFFF_FFFF;

/// Return the write-granularity byte width: `(WRITE_GRAN + 7) / 8`.
///
/// For the default `WRITE_GRAN = 1` this is `1`, so all alignment math
/// becomes a no-op, matching the C macro `FDB_WG_ALIGN`.
#[inline]
pub const fn wg_byte_width() -> u32 {
    (WRITE_GRAN + 7) / 8
}

/// Round `size` up to the nearest multiple of `align`, mirroring
/// `FDB_ALIGN(size, align)`.  `align` must be non-zero.
#[inline]
pub const fn align_up(size: u32, align: u32) -> u32 {
    ((size + align - 1) / align) * align
}

/// Round `size` down to the nearest multiple of `align`, mirroring
/// `FDB_ALIGN_DOWN(size, align)`.
#[inline]
pub const fn align_down(size: u32, align: u32) -> u32 {
    (size / align) * align
}

/// Align `size` up by the write granularity, mirrors `FDB_WG_ALIGN(size)`.
#[inline]
pub const fn wg_align(size: u32) -> u32 {
    align_up(size, wg_byte_width())
}

#[inline]
pub const fn fdb_wg_align(size: u32) -> u32 {
    wg_align(size)
}

/// Align `size` down by the write granularity, mirrors
/// `FDB_WG_ALIGN_DOWN(size)`.
#[inline]
pub const fn wg_align_down(size: u32) -> u32 {
    align_down(size, wg_byte_width())
}

/// Number of bytes required to store a status table with `status_number`
/// entries, mirrors `FDB_STATUS_TABLE_SIZE(status_number)`.
///
/// When `WRITE_GRAN == 1` each status occupies one bit, so the formula is
/// `(status_number * gran + 7) / 8`.  For other granularities each status
/// occupies `gran` bits and the formula is `((status_number - 1) * gran + 7)
/// / 8`.
#[inline]
pub const fn status_table_size(status_number: u32) -> u32 {
    if WRITE_GRAN == 1 {
        (status_number * WRITE_GRAN + 7) / 8
    } else {
        ((status_number - 1) * WRITE_GRAN + 7) / 8
    }
}

/// Encode `status_index` into `status_table`, mirrors `_fdb_set_status`.
///
/// Fills the table with `BYTE_ERASED` (0xFF), then for `status_index > 0`
/// clears bits from the MSB to mark the given status level.  Returns the
/// byte index that changed (or `usize::MAX` when no flash write is needed,
/// i.e. status 0 / all-erased).
///
/// # Panics
/// Panics if `status_table` is shorter than `status_table_size(status_num)`.
pub fn set_status(status_table: &mut [u8], status_num: u32, status_index: u32) -> usize {
    let table_size = status_table_size(status_num) as usize;
    assert!(
        status_table.len() >= table_size,
        "status_table too short: {} < {table_size}",
        status_table.len()
    );

    for b in &mut status_table[..table_size] {
        *b = BYTE_ERASED;
    }

    if status_index == 0 {
        return usize::MAX;
    }

    if WRITE_GRAN == 1 {
        let byte_index = ((status_index - 1) / 8) as usize;
        // C: status_table[byte_index] &= (0x00ff >> (status_index % 8))
        // When BYTE_ERASED == 0xFF (our case), we AND with the shifted mask.
        let shift = (status_index % 8) as u32;
        let mask: u8 = 0xFF >> shift;
        status_table[byte_index] &= mask;
        byte_index
    } else {
        let byte_index = ((status_index - 1) * (WRITE_GRAN / 8)) as usize;
        status_table[byte_index] = BYTE_WRITTEN;
        byte_index
    }
}

/// Decode the current status level from `status_table`, mirrors
/// `_fdb_get_status`.
///
/// Scans bits from high index to low, finding the first 0 bit.  The returned
/// value is the status index (0 = all erased, up to `status_num - 1`).
///
/// # Panics
/// Panics if `status_table` is too short to cover `status_num` bits.
pub fn get_status(status_table: &[u8], status_num: u32) -> u32 {
    let status_num_bak = status_num - 1;
    let mut i: u32 = 0;

    // C: while (status_num--) checks positions status_num_bak-1 down to 0.
    for pos in (0..status_num_bak).rev() {
        let byte_idx = (pos / 8) as usize;
        let bit_mask = 0x80u8 >> (pos % 8);
        if status_table[byte_idx] & bit_mask == 0x00 {
            break;
        }
        i += 1;
    }

    status_num_bak - i
}

/// Write a status to flash via the provided `write` closure, mirrors
/// `_fdb_write_status`.
///
/// This is a pure-logic wrapper: it updates `status_table` in memory via
/// [`set_status`], then — if a flash write is needed — invokes `write` with
/// the byte offset relative to `addr` and the byte to program.
///
/// Returns `Ok(())` on success, or the error from `write`.
pub fn write_status_to_flash<F>(
    status_table: &mut [u8],
    status_num: u32,
    status_index: u32,
    addr: u32,
    mut write: F,
) -> Result<(), crate::error::FdbError>
where
    F: FnMut(u32, u8) -> Result<(), crate::error::FdbError>,
{
    assert!(
        status_index < status_num,
        "status_index {status_index} >= status_num {status_num}"
    );

    let byte_index = set_status(status_table, status_num, status_index);

    if byte_index == usize::MAX {
        return Ok(());
    }

    let write_len = if WRITE_GRAN == 1 {
        1
    } else {
        (WRITE_GRAN / 8) as usize
    };

    let offset = byte_index as u32;
    for i in 0..write_len {
        let byte = status_table[byte_index + i];
        write(addr + offset + i as u32, byte)?;
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wg_align_default_gran_is_identity_for_aligned_sizes() {
        // WRITE_GRAN == 1 -> wg_byte_width == 1 -> align is a no-op.
        assert_eq!(wg_align(0), 0);
        assert_eq!(wg_align(1), 1);
        assert_eq!(wg_align(13), 13);
        assert_eq!(wg_align(4096), 4096);
    }

    #[test]
    fn align_up_round_trips() {
        assert_eq!(align_up(13, 4), 16);
        assert_eq!(align_up(16, 4), 16);
        assert_eq!(align_up(0, 4), 0);
    }

    #[test]
    fn align_down_round_trips() {
        assert_eq!(align_down(13, 4), 12);
        assert_eq!(align_down(16, 4), 16);
        assert_eq!(align_down(0, 4), 0);
    }

    #[test]
    fn wg_align_down_rounds_down_to_granularity() {
        // WRITE_GRAN == 1 -> wg_byte_width == 1 -> align_down is identity.
        assert_eq!(wg_align_down(0), 0);
        assert_eq!(wg_align_down(1), 1);
        assert_eq!(wg_align_down(13), 13);
        assert_eq!(wg_align_down(4096), 4096);
    }

    #[test]
    fn status_table_size_for_default_gran() {
        // FDB_SECTOR_STORE_STATUS_NUM == 4, FDB_SECTOR_DIRTY_STATUS_NUM == 4
        assert_eq!(status_table_size(4), 1);
        assert_eq!(status_table_size(6), 1);
        assert_eq!(status_table_size(8), 1);
        assert_eq!(status_table_size(9), 2);
    }

    #[test]
    fn byte_constants_match_c() {
        assert_eq!(BYTE_ERASED, 0xFF);
        assert_eq!(BYTE_WRITTEN, 0x00);
        assert_eq!(DATA_UNUSED, 0xFFFF_FFFF);
        assert_eq!(FAILED_ADDR, 0xFFFF_FFFF);
    }

    #[test]
    fn set_status_zero_returns_max_and_all_erased() {
        let mut table = [0u8; 2];
        let idx = set_status(&mut table, 4, 0);
        assert_eq!(idx, usize::MAX);
        assert_eq!(table[0], BYTE_ERASED);
    }

    #[test]
    fn set_and_get_status_round_trip_four_states() {
        for status_index in 0..4u32 {
            let mut table = [0u8; 2];
            set_status(&mut table, 4, status_index);
            let decoded = get_status(&table, 4);
            assert_eq!(
                decoded, status_index,
                "round-trip failed for status {status_index}"
            );
        }
    }

    #[test]
    fn set_status_one_clears_msb() {
        let mut table = [0u8; 2];
        set_status(&mut table, 4, 1);
        // status 1: byte_index=0, shift=1, mask=0x7F -> 0xFF & 0x7F = 0x7F
        assert_eq!(table[0], 0x7F);
    }

    #[test]
    fn set_status_two_clears_next_bit() {
        let mut table = [0u8; 2];
        set_status(&mut table, 4, 2);
        // status 2: byte_index=0, shift=2, mask=0x3F -> 0xFF & 0x3F = 0x3F
        assert_eq!(table[0], 0x3F);
    }

    #[test]
    fn set_status_three_clears_third_bit() {
        let mut table = [0u8; 2];
        set_status(&mut table, 4, 3);
        // status 3: byte_index=0, shift=3, mask=0x1F -> 0xFF & 0x1F = 0x1F
        assert_eq!(table[0], 0x1F);
    }

    #[test]
    fn write_status_to_flash_skips_write_for_status_zero() {
        let mut table = [0u8; 2];
        let mut written = false;
        write_status_to_flash(&mut table, 4, 0, 0x1000, |_addr, _byte| {
            written = true;
            Ok(())
        })
        .unwrap();
        assert!(!written);
    }

    #[test]
    fn write_status_to_flash_invokes_write_for_nonzero() {
        let mut table = [0u8; 2];
        let mut captured: Option<(u32, u8)> = None;
        write_status_to_flash(&mut table, 4, 2, 0x1000, |addr, byte| {
            captured = Some((addr, byte));
            Ok(())
        })
        .unwrap();
        let (addr, byte) = captured.expect("write should have been called");
        assert_eq!(addr, 0x1000);
        assert_eq!(byte, 0x3F);
    }

    #[test]
    fn write_status_to_flash_propagates_write_error() {
        let mut table = [0u8; 2];
        let err = write_status_to_flash(&mut table, 4, 1, 0x1000, |_, _| {
            Err(crate::error::FdbError::WriteErr)
        });
        assert!(err.is_err());
    }

    #[test]
    fn get_status_all_erased_returns_zero() {
        let table = [BYTE_ERASED; 2];
        assert_eq!(get_status(&table, 4), 0);
    }

    #[test]
    fn get_status_six_states_round_trip() {
        for status_index in 0..6u32 {
            let mut table = [0u8; 2];
            set_status(&mut table, 6, status_index);
            assert_eq!(get_status(&table, 6), status_index);
        }
    }
}

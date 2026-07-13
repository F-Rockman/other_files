use crate::blob::Blob;
use crate::def::{FdbDb, WRITE_GRAN};
use crate::file_backend::file_read;
use crate::low_lvl::{BYTE_ERASED, BYTE_WRITTEN, wg_align};

const CRC32_POLY: u32 = 0xEDB8_8320;

const fn make_crc32_table() -> [u32; 256] {
    let mut table = [0u32; 256];
    let mut i = 0u32;
    while i < 256 {
        let mut crc = i;
        let mut j = 0;
        while j < 8 {
            if crc & 1 != 0 {
                crc = (crc >> 1) ^ CRC32_POLY;
            } else {
                crc >>= 1;
            }
            j += 1;
        }
        table[i as usize] = crc;
        i += 1;
    }
    table
}

const CRC32_TABLE: [u32; 256] = make_crc32_table();

pub fn calc_crc32(crc: u32, data: &[u8]) -> u32 {
    let mut crc = crc ^ 0xFFFF_FFFF;
    for &byte in data {
        let idx = ((crc ^ byte as u32) & 0xFF) as usize;
        crc = CRC32_TABLE[idx] ^ (crc >> 8);
    }
    crc ^ 0xFFFF_FFFF
}

pub fn string_eq(a: &str, b: &str) -> bool {
    a == b
}

pub fn align_down_size(size: u32) -> u32 {
    (size / WRITE_GRAN) * WRITE_GRAN
}

pub fn continue_ff_addr(db: &FdbDb, start: u32, end: u32) -> u32 {
    let mut buf = [0u8; 32];
    let mut last_data: u8 = BYTE_WRITTEN;
    let mut addr = start;
    let mut cur = start;

    while cur < end {
        let read_size = if cur + 32 < end { 32 } else { (end - cur) as usize };
        if file_read(db, cur, &mut buf[..read_size]).is_err() {
            break;
        }
        for i in 0..read_size {
            if last_data != BYTE_ERASED && buf[i] == BYTE_ERASED {
                addr = cur + i as u32;
            }
            last_data = buf[i];
        }
        cur += 32;
    }

    if last_data == BYTE_ERASED {
        wg_align(addr)
    } else {
        end
    }
}

pub fn fdb_blob_read(db: &FdbDb, blob: &mut Blob) -> usize {
    let read_len = blob.buf.len().min(blob.saved.len);
    if file_read(db, blob.saved.addr, &mut blob.buf[..read_len]).is_err() {
        0
    } else {
        read_len
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn crc32_empty_with_zero_init_is_zero() {
        assert_eq!(calc_crc32(0, b""), 0);
    }

    #[test]
    fn crc32_check_value_for_123456789() {
        assert_eq!(calc_crc32(0, b"123456789"), 0xCBF4_3926);
    }

    #[test]
    fn crc32_incremental_matches_single_call() {
        let data = b"hello world";
        let whole = calc_crc32(0, data);
        let mid = calc_crc32(0, &data[..5]);
        let inc = calc_crc32(mid, &data[5..]);
        assert_eq!(whole, inc);
    }
}

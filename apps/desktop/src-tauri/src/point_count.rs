//! Reads the number of points from a file header, without reading the points.
//! gqfilter prints only the output count, so the input count comes from here.
//!
//! - LAS / LAZ: the public header block (LAZ keeps it uncompressed).
//! - E57: the XML section; the sum of `recordCount` of every scan's `points`.

use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;

use serde::Serialize;

const LAS_SIGNATURE: &[u8; 4] = b"LASF";
const LAS_LEGACY_COUNT_AT: usize = 107;
const LAS_COUNT_64_AT: usize = 247;
const LAS_14_HEADER_SIZE: usize = 375;

const E57_SIGNATURE: &[u8; 8] = b"ASTM-E57";
const E57_HEADER_SIZE: usize = 48;
const E57_CRC_SIZE: u64 = 4;
/// The XML of a normal file is small; refuse anything absurd.
const E57_MAX_XML: u64 = 64 * 1024 * 1024;

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct HeaderInfo {
    /// e.g. "LAS 1.4", "LAZ 1.2", "E57"
    pub format: String,
    pub points: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CountError {
    Io(String),
    NotRecognised,
    Damaged(&'static str),
}

impl CountError {
    pub fn message(&self) -> String {
        match self {
            Self::Io(e) => format!("ファイルを読めません: {e}"),
            Self::NotRecognised => "LAS / LAZ / E57 のファイルではないようです。".to_string(),
            Self::Damaged(what) => format!("ファイルの見出しが壊れているようです（{what}）。"),
        }
    }
}

fn io(e: std::io::Error) -> CountError {
    CountError::Io(e.to_string())
}

/// Reads the header of the file at `path`.
pub fn read_header(path: &Path) -> Result<HeaderInfo, CountError> {
    let mut file = File::open(path).map_err(io)?;
    let mut head = [0u8; E57_HEADER_SIZE];
    let n = read_up_to(&mut file, &mut head)?;
    if n >= 8 && &head[..8] == E57_SIGNATURE {
        return e57_points(&mut file);
    }
    if n >= 4 && &head[..4] == LAS_SIGNATURE {
        file.seek(SeekFrom::Start(0)).map_err(io)?;
        let mut header = vec![0u8; LAS_14_HEADER_SIZE];
        let n = read_up_to(&mut file, &mut header)?;
        header.truncate(n);
        let compressed = path
            .extension()
            .and_then(|e| e.to_str())
            .is_some_and(|e| e.eq_ignore_ascii_case("laz"));
        return las_points(&header, compressed);
    }
    Err(CountError::NotRecognised)
}

fn read_up_to(reader: &mut impl Read, buf: &mut [u8]) -> Result<usize, CountError> {
    let mut filled = 0;
    while filled < buf.len() {
        match reader.read(&mut buf[filled..]).map_err(io)? {
            0 => break,
            n => filled += n,
        }
    }
    Ok(filled)
}

fn u16_at(b: &[u8], at: usize) -> Option<u16> {
    Some(u16::from_le_bytes(b.get(at..at + 2)?.try_into().ok()?))
}
fn u32_at(b: &[u8], at: usize) -> Option<u32> {
    Some(u32::from_le_bytes(b.get(at..at + 4)?.try_into().ok()?))
}
fn u64_at(b: &[u8], at: usize) -> Option<u64> {
    Some(u64::from_le_bytes(b.get(at..at + 8)?.try_into().ok()?))
}

/// Point count from a LAS public header block (`header` starts at byte 0).
pub fn las_points(header: &[u8], compressed: bool) -> Result<HeaderInfo, CountError> {
    if header.get(..4) != Some(LAS_SIGNATURE.as_slice()) {
        return Err(CountError::NotRecognised);
    }
    let (major, minor) = match (header.get(24), header.get(25)) {
        (Some(&a), Some(&b)) => (a, b),
        _ => return Err(CountError::Damaged("版")),
    };
    let header_size = u16_at(header, 94).ok_or(CountError::Damaged("見出しの大きさ"))? as usize;
    let legacy = u32_at(header, LAS_LEGACY_COUNT_AT).ok_or(CountError::Damaged("点の数"))?;
    let wide = if minor >= 4 && header_size >= LAS_14_HEADER_SIZE {
        let count = u64_at(header, LAS_COUNT_64_AT).ok_or(CountError::Damaged("点の数"))?;
        Some(count).filter(|&n| n > 0)
    } else {
        None
    };
    let kind = if compressed { "LAZ" } else { "LAS" };
    Ok(HeaderInfo {
        format: format!("{kind} {major}.{minor}"),
        points: wide.unwrap_or(u64::from(legacy)),
    })
}

/// E57: read the XML section (stored in pages that end with a 4-byte CRC).
fn e57_points<R: Read + Seek>(reader: &mut R) -> Result<HeaderInfo, CountError> {
    reader.seek(SeekFrom::Start(0)).map_err(io)?;
    let mut head = [0u8; E57_HEADER_SIZE];
    if read_up_to(reader, &mut head)? < E57_HEADER_SIZE {
        return Err(CountError::Damaged("E57 の見出し"));
    }
    let xml_offset = u64_at(&head, 24).ok_or(CountError::Damaged("XML の場所"))?;
    let xml_length = u64_at(&head, 32).ok_or(CountError::Damaged("XML の長さ"))?;
    let page_size = u64_at(&head, 40).ok_or(CountError::Damaged("ページの大きさ"))?;
    if page_size <= E57_CRC_SIZE || xml_length == 0 || xml_length > E57_MAX_XML {
        return Err(CountError::Damaged("XML の場所"));
    }
    let xml = read_logical(reader, xml_offset, xml_length, page_size)?;
    let points =
        sum_record_counts(&String::from_utf8_lossy(&xml)).ok_or(CountError::Damaged("点の数"))?;
    Ok(HeaderInfo {
        format: "E57".to_string(),
        points,
    })
}

/// Reads `length` payload bytes starting at physical `offset`, skipping page CRCs.
pub fn read_logical<R: Read + Seek>(
    reader: &mut R,
    offset: u64,
    length: u64,
    page_size: u64,
) -> Result<Vec<u8>, CountError> {
    let payload = page_size - E57_CRC_SIZE;
    let mut out = Vec::with_capacity(length as usize);
    let mut pos = offset;
    while (out.len() as u64) < length {
        let in_page = pos % page_size;
        if in_page >= payload {
            pos = pos
                .checked_add(page_size - in_page)
                .ok_or(CountError::Damaged("XML の場所"))?;
            continue;
        }
        let take = (payload - in_page).min(length - out.len() as u64) as usize;
        let mut chunk = vec![0u8; take];
        reader.seek(SeekFrom::Start(pos)).map_err(io)?;
        if read_up_to(reader, &mut chunk)? < take {
            return Err(CountError::Damaged("XML が途中で切れています"));
        }
        out.extend_from_slice(&chunk);
        pos = pos
            .checked_add(take as u64)
            .ok_or(CountError::Damaged("XML の場所"))?;
    }
    Ok(out)
}

/// Sum of `recordCount` on every `<points ...>` element; None if there is none.
pub fn sum_record_counts(xml: &str) -> Option<u64> {
    let mut total: Option<u64> = None;
    let mut rest = xml;
    while let Some(start) = rest.find("<points") {
        let after = &rest[start + "<points".len()..];
        let is_element = after.starts_with(|c: char| c.is_whitespace() || c == '>' || c == '/');
        let end = after.find('>').unwrap_or(after.len());
        if is_element {
            if let Some(count) = attribute(&after[..end], "recordCount") {
                total = Some(total.unwrap_or(0).checked_add(count)?);
            }
        }
        rest = &after[end..];
    }
    total
}

fn attribute(tag: &str, name: &str) -> Option<u64> {
    let key = format!("{name}=");
    let at = tag.find(&key)? + key.len();
    let value = &tag[at..];
    let quote = value.chars().next().filter(|c| *c == '"' || *c == '\'')?;
    let inner = &value[1..];
    let close = inner.find(quote)?;
    inner[..close].trim().parse().ok()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Cursor, Write};

    fn las_header(minor: u8, legacy: u32, wide: u64) -> Vec<u8> {
        let size: u16 = if minor >= 4 { 375 } else { 227 };
        let mut h = vec![0u8; size as usize];
        h[..4].copy_from_slice(b"LASF");
        h[24] = 1;
        h[25] = minor;
        h[94..96].copy_from_slice(&size.to_le_bytes());
        h[107..111].copy_from_slice(&legacy.to_le_bytes());
        if minor >= 4 {
            h[247..255].copy_from_slice(&wide.to_le_bytes());
        }
        h
    }

    #[test]
    fn las_12_uses_legacy_count() {
        let info = las_points(&las_header(2, 7388, 0), false).unwrap();
        assert_eq!(
            info,
            HeaderInfo {
                format: "LAS 1.2".into(),
                points: 7388
            }
        );
    }

    #[test]
    fn las_14_prefers_64_bit_count() {
        let info = las_points(&las_header(4, 0, 5_000_000_000), true).unwrap();
        assert_eq!(info.points, 5_000_000_000);
        assert_eq!(info.format, "LAZ 1.4");
        // A 1.4 file that only fills the legacy field.
        assert_eq!(las_points(&las_header(4, 12, 0), false).unwrap().points, 12);
    }

    #[test]
    fn las_rejects_short_or_foreign_bytes() {
        assert_eq!(las_points(b"NOPE", false), Err(CountError::NotRecognised));
        assert!(matches!(
            las_points(&las_header(2, 1, 0)[..100], false),
            Err(CountError::Damaged(_))
        ));
    }

    #[test]
    fn record_counts_are_summed_over_scans() {
        let xml = r#"<e57Root><data3D type="Vector">
            <vectorChild type="Structure"><points type="CompressedVector" fileOffset="48" recordCount="100"/></vectorChild>
            <vectorChild type="Structure"><points type="CompressedVector" recordCount='23' fileOffset="9"></points></vectorChild>
            <pointsSize>9</pointsSize>
            </data3D></e57Root>"#;
        assert_eq!(sum_record_counts(xml), Some(123));
        assert_eq!(sum_record_counts("<e57Root/>"), None);
    }

    /// Writes `payload` from physical `offset` in pages of `page` bytes, each ending in a CRC.
    fn paged(offset: u64, payload: &[u8], page: u64) -> Vec<u8> {
        let mut file = vec![0u8; offset as usize];
        let mut pos = offset;
        let mut rest = payload;
        while !rest.is_empty() {
            let in_page = pos % page;
            if in_page >= page - 4 {
                file.extend_from_slice(&[0xEE; 4][..(page - in_page) as usize]);
                pos += page - in_page;
                continue;
            }
            let take = ((page - 4 - in_page) as usize).min(rest.len());
            file.extend_from_slice(&rest[..take]);
            rest = &rest[take..];
            pos += take as u64;
        }
        file
    }

    #[test]
    fn logical_read_skips_crc_bytes() {
        let payload: Vec<u8> = (0..200u8).collect();
        let file = paged(10, &payload, 64);
        let got = read_logical(&mut Cursor::new(file), 10, 200, 64).unwrap();
        assert_eq!(got, payload);
    }

    #[test]
    fn e57_file_header_and_xml() {
        let xml = br#"<?xml version="1.0"?><e57Root><data3D><vectorChild><points type="CompressedVector" recordCount="4321"/></vectorChild></data3D></e57Root>"#;
        let page = 128u64;
        let offset = page; // the XML starts on the second page
        let mut file = paged(offset, xml, page);
        file[..8].copy_from_slice(b"ASTM-E57");
        file[24..32].copy_from_slice(&offset.to_le_bytes());
        file[32..40].copy_from_slice(&(xml.len() as u64).to_le_bytes());
        file[40..48].copy_from_slice(&page.to_le_bytes());

        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("scan.e57");
        File::create(&path).unwrap().write_all(&file).unwrap();
        let info = read_header(&path).unwrap();
        assert_eq!(
            info,
            HeaderInfo {
                format: "E57".into(),
                points: 4321
            }
        );
    }

    /// The repository fixture (Git LFS). Skipped when only the LFS pointer is there.
    #[test]
    fn fixture_autzen_small() {
        let path =
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../tests/fixtures/autzen-small.laz");
        match read_header(&path) {
            Ok(info) => assert_eq!(info.points, 7388),
            Err(CountError::NotRecognised) | Err(CountError::Io(_)) => {
                eprintln!("fixture not available, skipped");
            }
            Err(e) => panic!("{e:?}"),
        }
    }

    #[test]
    fn read_header_on_las_file_and_unknown_file() {
        let dir = tempfile::tempdir().unwrap();
        let las = dir.path().join("a.laz");
        File::create(&las)
            .unwrap()
            .write_all(&las_header(2, 99, 0))
            .unwrap();
        assert_eq!(read_header(&las).unwrap().points, 99);

        let txt = dir.path().join("a.txt");
        File::create(&txt).unwrap().write_all(b"hello").unwrap();
        assert_eq!(read_header(&txt), Err(CountError::NotRecognised));
    }
}

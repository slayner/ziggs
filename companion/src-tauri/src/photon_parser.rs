// Photon protocol parser — extracts Albion Online data from UDP packets.
//
// Based on AAT (Triky313/AlbionOnline-StatisticsAnalysis):
//   - Photon header: 12 bytes, big-endian
//   - Command header: 12 bytes, big-endian
//   - Protocol18: little-endian, varint+ZigZag
//   - Albion opcode: param 253 (request/response), param 252 (event)
//
// Relevant opcodes (detected by structure, not number):
//   Join (2):             response with map, name, guild of local player
//   ChangeCluster (41):   response on map change
//   NewCharacter (29):    event when another player appears
//   PartyJoined (231):     event with full party roster
//   PartyPlayerJoined (233): event when someone joins the party
//   PartyPlayerLeft (235):  event when someone leaves the party

use std::{
    collections::{HashMap, VecDeque},
    time::{Duration, Instant},
};

#[derive(Clone, Debug)]
pub enum PhotonValue {
    Bool(bool),
    Byte(u8),
    Short(i16),
    Int(i32),
    Long(i64),
    Float(f32),
    Double(f64),
    String(String),
    Bytes(Vec<u8>),
    Array(Vec<PhotonValue>),
    Dictionary(Vec<(PhotonValue, PhotonValue)>),
    Null,
}

impl PhotonValue {
    pub fn as_string(&self) -> Option<&str> {
        match self {
            PhotonValue::String(s) => Some(s),
            _ => None,
        }
    }
    pub fn as_i64(&self) -> Option<i64> {
        match self {
            PhotonValue::Byte(b) => Some(*b as i64),
            PhotonValue::Short(s) => Some(*s as i64),
            PhotonValue::Int(i) => Some(*i as i64),
            PhotonValue::Long(l) => Some(*l),
            _ => None,
        }
    }
    pub fn as_bytes(&self) -> Option<&[u8]> {
        match self {
            PhotonValue::Bytes(b) => Some(b),
            _ => None,
        }
    }
    pub fn as_array(&self) -> Option<&[PhotonValue]> {
        match self {
            PhotonValue::Array(a) => Some(a),
            _ => None,
        }
    }
}

#[derive(Clone, Debug)]
pub struct ParsedOperation {
    pub message_type: u8, // 2=Request, 3=Response, 4=Event
    pub albion_code: i16, // opcode extracted from param 252/253
    pub parameters: HashMap<u8, PhotonValue>,
}

const MAX_FRAGMENT_LENGTH: usize = 1_048_576;
const MAX_PENDING_FRAGMENT_BYTES: usize = 4_194_304;
const FRAGMENT_TTL: Duration = Duration::from_secs(30);

struct PendingFragment {
    buffer: Vec<u8>,
    received: Vec<bool>,
    received_bytes: usize,
    updated_at: Instant,
}

pub struct PhotonParser {
    fragments: HashMap<i32, PendingFragment>,
    pending_fragment_bytes: usize,
}

impl PhotonParser {
    pub fn new() -> Self {
        Self {
            fragments: HashMap::new(),
            pending_fragment_bytes: 0,
        }
    }

    fn expire_fragments(&mut self, now: Instant) {
        let mut released = 0;
        self.fragments.retain(|_, fragment| {
            let expired = now.duration_since(fragment.updated_at) >= FRAGMENT_TTL;
            if expired {
                released += fragment.buffer.len();
            }
            !expired
        });
        self.pending_fragment_bytes -= released;
    }

    /// Parse a complete UDP datagram. Returns extracted operations.
    pub fn parse(&mut self, data: &[u8]) -> Vec<ParsedOperation> {
        let mut ops = Vec::new();
        if data.len() < 12 {
            return ops;
        }

        let mut offset = 0;
        // Photon header (12 bytes, big-endian)
        let _peer_id = read_i16_be(data, &mut offset);
        let flags = data[offset];
        offset += 1;
        let command_count = data[offset];
        offset += 1;
        let _crc = read_i32_be(data, &mut offset);
        let _user_data = read_i32_be(data, &mut offset);

        // flags == 1 = encrypted → skip
        if flags == 0x01 {
            return ops;
        }

        // CRC check if flags == 0xCC
        if flags == 0xCC {
            // 0xCC flag: 4 extra CRC32 bytes after the standard 12-byte header.
            // Skip them to avoid misaligning command offsets.
            let _crc_value = read_i32_be(data, &mut offset);
        }

        for _ in 0..command_count {
            if offset + 12 > data.len() {
                break;
            }
            let cmd_start = offset;
            let command_type = data[offset];
            offset += 1;
            offset += 3; // skip 3 bytes
            let command_length = read_i32_be(data, &mut offset) as usize;
            let _seq = read_i32_be(data, &mut offset);

            // command_length includes the 12-byte header; smaller = misaligned read
            if command_length < 12 {
                break;
            }
            let cmd_end = cmd_start + command_length;
            if cmd_end > data.len() {
                break;
            }
            let payload = &data[offset..cmd_end];

            match command_type {
                4 => { /* Disconnect: skip */ }
                6 => {
                    /* SendReliable */
                    self.parse_message(payload, &mut ops);
                }
                7 => {
                    /* SendUnreliable: skip 4 bytes, then same as SendReliable */
                    if payload.len() > 4 {
                        self.parse_message(&payload[4..], &mut ops);
                    }
                }
                8 => {
                    /* SendFragment */
                    self.parse_fragment(payload, &mut ops);
                }
                _ => {}
            }

            offset = cmd_end;
        }

        ops
    }

    fn parse_message(&mut self, payload: &[u8], ops: &mut Vec<ParsedOperation>) {
        if payload.len() < 2 {
            return;
        }
        let mut offset = 0;
        offset += 1; // skip 1 byte
        let message_type = payload[offset];
        offset += 1;
        let op_data = &payload[offset..];

        match message_type {
            2 => {
                /* Request */
                if op_data.is_empty() {
                    return;
                }
                let mut cursor = 0;
                // Real opcode is the header byte (as in AAT). Param 253 is a
                // redundant copy not always present; fall back to header when absent.
                let photon_opcode = op_data[cursor] as i16;
                cursor += 1;
                let params = deserialize_param_table(op_data, &mut cursor);
                let albion_code = params
                    .get(&253)
                    .and_then(|v| v.as_i64())
                    .map(|c| c as i16)
                    .unwrap_or(photon_opcode);
                ops.push(ParsedOperation {
                    message_type,
                    albion_code,
                    parameters: params,
                });
            }
            3 => {
                /* Response */
                if op_data.len() < 3 {
                    return;
                }
                let mut cursor = 0;
                let photon_opcode = op_data[cursor] as i16;
                cursor += 1;
                let _return_code = read_i16_le(op_data, &mut cursor);
                // debug message (type-prefixed)
                if cursor < op_data.len() {
                    let _type_code = op_data[cursor];
                    cursor += 1;
                    // skip string value
                    let _ = deserialize_value(op_data, &mut cursor, _type_code);
                }
                let params = deserialize_param_table(op_data, &mut cursor);
                let albion_code = params
                    .get(&253)
                    .and_then(|v| v.as_i64())
                    .map(|c| c as i16)
                    .unwrap_or(photon_opcode);
                ops.push(ParsedOperation {
                    message_type,
                    albion_code,
                    parameters: params,
                });
            }
            4 => {
                /* Event */
                if op_data.is_empty() {
                    return;
                }
                let mut cursor = 0;
                let photon_code = op_data[cursor] as i16;
                cursor += 1;
                let params = deserialize_param_table(op_data, &mut cursor);
                let albion_code = params
                    .get(&252)
                    .and_then(|v| v.as_i64())
                    .map(|c| c as i16)
                    .unwrap_or(photon_code);
                ops.push(ParsedOperation {
                    message_type,
                    albion_code,
                    parameters: params,
                });
            }
            _ => {}
        }
    }

    fn parse_fragment(&mut self, payload: &[u8], ops: &mut Vec<ParsedOperation>) {
        if payload.len() < 20 {
            return;
        }
        let mut offset = 0;
        let start_seq = read_i32_be(payload, &mut offset);
        let _ = read_i32_be(payload, &mut offset);
        let _ = read_i32_be(payload, &mut offset);
        let total_length = read_i32_be(payload, &mut offset);
        let fragment_offset = read_i32_be(payload, &mut offset);
        let fragment_data = &payload[offset..];
        let now = Instant::now();
        self.expire_fragments(now);

        if total_length <= 0 || fragment_offset < 0 {
            return;
        }
        let total_length = total_length as usize;
        let fragment_offset = fragment_offset as usize;
        let Some(fragment_end) = fragment_offset.checked_add(fragment_data.len()) else {
            return;
        };
        if total_length > MAX_FRAGMENT_LENGTH || fragment_end > total_length {
            return;
        }

        if !self.fragments.contains_key(&start_seq) {
            let Some(pending_bytes) = self.pending_fragment_bytes.checked_add(total_length) else {
                return;
            };
            if pending_bytes > MAX_PENDING_FRAGMENT_BYTES {
                return;
            }
            self.fragments.insert(
                start_seq,
                PendingFragment {
                    buffer: vec![0u8; total_length],
                    received: vec![false; total_length],
                    received_bytes: 0,
                    updated_at: now,
                },
            );
            self.pending_fragment_bytes = pending_bytes;
        }

        let entry = self.fragments.get_mut(&start_seq).expect("fragmento pendente");
        if entry.buffer.len() != total_length {
            return;
        }
        entry.buffer[fragment_offset..fragment_end].copy_from_slice(fragment_data);
        for received in &mut entry.received[fragment_offset..fragment_end] {
            if !*received {
                *received = true;
                entry.received_bytes += 1;
            }
        }
        entry.updated_at = now;

        if entry.received_bytes == total_length {
            let complete = self.fragments.remove(&start_seq).expect("fragmento pendente");
            self.pending_fragment_bytes -= complete.buffer.len();
            self.parse_message(&complete.buffer, ops);
        }
    }
}

// ─── Protocol18 deserialization ──────────────────────────────────────────────

/// Advance cursor through a parameter table without storing values.
/// Used to skip nested operations (24/25/26) without misaligning the cursor.
fn skip_param_table(data: &[u8], cursor: &mut usize) {
    if *cursor >= data.len() {
        return;
    }
    let count = data[*cursor];
    *cursor += 1;
    for _ in 0..count {
        if *cursor + 2 > data.len() {
            return;
        }
        *cursor += 1; // param id
        let tc = data[*cursor];
        *cursor += 1;
        let _ = deserialize_value(data, cursor, tc);
    }
}

fn deserialize_param_table(data: &[u8], cursor: &mut usize) -> HashMap<u8, PhotonValue> {
    if *cursor >= data.len() {
        return HashMap::new();
    }
    let count = data[*cursor];
    *cursor += 1;
    let mut params = HashMap::with_capacity(count as usize);
    for _ in 0..count {
        if *cursor + 2 > data.len() {
            break;
        }
        let key = data[*cursor];
        *cursor += 1;
        let type_code = data[*cursor];
        *cursor += 1;
        let value = deserialize_value(data, cursor, type_code);
        params.insert(key, value);
    }
    params
}

fn deserialize_value(data: &[u8], cursor: &mut usize, type_code: u8) -> PhotonValue {
    match type_code {
        0 => PhotonValue::Null,
        2 => {
            // Boolean
            if *cursor >= data.len() {
                return PhotonValue::Null;
            }
            let v = data[*cursor] != 0;
            *cursor += 1;
            PhotonValue::Bool(v)
        }
        3 => {
            // Byte
            if *cursor >= data.len() {
                return PhotonValue::Null;
            }
            let v = data[*cursor];
            *cursor += 1;
            PhotonValue::Byte(v)
        }
        4 => {
            // Short (LE)
            let v = read_i16_le(data, cursor);
            PhotonValue::Short(v)
        }
        5 => {
            // Float (LE)
            if *cursor + 4 > data.len() {
                return PhotonValue::Null;
            }
            let v = f32::from_le_bytes([
                data[*cursor],
                data[*cursor + 1],
                data[*cursor + 2],
                data[*cursor + 3],
            ]);
            *cursor += 4;
            PhotonValue::Float(v)
        }
        6 => {
            // Double (LE)
            if *cursor + 8 > data.len() {
                return PhotonValue::Null;
            }
            let v = f64::from_le_bytes([
                data[*cursor],
                data[*cursor + 1],
                data[*cursor + 2],
                data[*cursor + 3],
                data[*cursor + 4],
                data[*cursor + 5],
                data[*cursor + 6],
                data[*cursor + 7],
            ]);
            *cursor += 8;
            PhotonValue::Double(v)
        }
        7 => {
            // String
            let len = read_varint_u32(data, cursor) as usize;
            if *cursor + len > data.len() {
                return PhotonValue::Null;
            }
            let s = String::from_utf8_lossy(&data[*cursor..*cursor + len]).into_owned();
            *cursor += len;
            PhotonValue::String(s)
        }
        8 => {
            // Null
            PhotonValue::Null
        }
        9 => {
            // CompressedInt (varint + ZigZag → i32)
            let raw = read_varint_u32(data, cursor);
            let v = decode_zigzag_32(raw);
            PhotonValue::Int(v)
        }
        10 => {
            // CompressedLong (varint + ZigZag → i64)
            let raw = read_varint_u64(data, cursor);
            let v = decode_zigzag_64(raw);
            PhotonValue::Long(v)
        }
        11 => {
            // Int1 (1 byte, positive)
            if *cursor >= data.len() {
                return PhotonValue::Null;
            }
            let v = data[*cursor] as i32;
            *cursor += 1;
            PhotonValue::Int(v)
        }
        12 => {
            // Int1Negative
            if *cursor >= data.len() {
                return PhotonValue::Null;
            }
            let v = -(data[*cursor] as i32);
            *cursor += 1;
            PhotonValue::Int(v)
        }
        13 => {
            // Int2 (2 bytes LE, positive)
            if *cursor + 2 > data.len() {
                return PhotonValue::Null;
            }
            let v = u16::from_le_bytes([data[*cursor], data[*cursor + 1]]) as i32;
            *cursor += 2;
            PhotonValue::Int(v)
        }
        14 => {
            // Int2Negative
            if *cursor + 2 > data.len() {
                return PhotonValue::Null;
            }
            let v = -(u16::from_le_bytes([data[*cursor], data[*cursor + 1]]) as i32);
            *cursor += 2;
            PhotonValue::Int(v)
        }
        15 => {
            // Long1 (1 byte, positive)
            if *cursor >= data.len() {
                return PhotonValue::Null;
            }
            let v = data[*cursor] as i64;
            *cursor += 1;
            PhotonValue::Long(v)
        }
        16 => {
            // Long1Negative
            if *cursor >= data.len() {
                return PhotonValue::Null;
            }
            let v = -(data[*cursor] as i64);
            *cursor += 1;
            PhotonValue::Long(v)
        }
        17 => {
            // Long2 (2 bytes LE, positive)
            if *cursor + 2 > data.len() {
                return PhotonValue::Null;
            }
            let v = u16::from_le_bytes([data[*cursor], data[*cursor + 1]]) as i64;
            *cursor += 2;
            PhotonValue::Long(v)
        }
        18 => {
            // Long2Negative
            if *cursor + 2 > data.len() {
                return PhotonValue::Null;
            }
            let v = -(u16::from_le_bytes([data[*cursor], data[*cursor + 1]]) as i64);
            *cursor += 2;
            PhotonValue::Long(v)
        }
        19 => {
            // Custom
            if *cursor >= data.len() {
                return PhotonValue::Null;
            }
            let _type_code = data[*cursor];
            *cursor += 1;
            let len = read_varint_u32(data, cursor) as usize;
            if *cursor + len > data.len() {
                return PhotonValue::Null;
            }
            let bytes = data[*cursor..*cursor + len].to_vec();
            *cursor += len;
            PhotonValue::Bytes(bytes)
        }
        20 => {
            // Dictionary
            if *cursor + 2 > data.len() {
                return PhotonValue::Null;
            }
            let key_type = data[*cursor];
            *cursor += 1;
            let val_type = data[*cursor];
            *cursor += 1;
            let size = read_varint_u32(data, cursor) as usize;
            let mut entries = Vec::with_capacity(size.min(data.len()));
            for _ in 0..size {
                let key = if key_type == 0 {
                    if *cursor >= data.len() {
                        break;
                    }
                    let tc = data[*cursor];
                    *cursor += 1;
                    deserialize_value(data, cursor, tc)
                } else {
                    deserialize_value(data, cursor, key_type)
                };
                let val = if val_type == 0 {
                    if *cursor >= data.len() {
                        break;
                    }
                    let tc = data[*cursor];
                    *cursor += 1;
                    deserialize_value(data, cursor, tc)
                } else {
                    deserialize_value(data, cursor, val_type)
                };
                entries.push((key, val));
            }
            PhotonValue::Dictionary(entries)
        }
        21 => {
            // Hashtable
            let size = read_varint_u32(data, cursor) as usize;
            let mut entries = Vec::with_capacity(size.min(data.len()));
            for _ in 0..size {
                if *cursor >= data.len() {
                    break;
                }
                let key_type = data[*cursor];
                *cursor += 1;
                let key = deserialize_value(data, cursor, key_type);
                if *cursor >= data.len() {
                    break;
                }
                let val_type = data[*cursor];
                *cursor += 1;
                let val = deserialize_value(data, cursor, val_type);
                entries.push((key, val));
            }
            PhotonValue::Dictionary(entries)
        }
        23 => {
            // ObjectArray
            let size = read_varint_u32(data, cursor) as usize;
            let mut arr = Vec::with_capacity(size.min(data.len()));
            for _ in 0..size {
                if *cursor >= data.len() {
                    break;
                }
                let tc = data[*cursor];
                *cursor += 1;
                arr.push(deserialize_value(data, cursor, tc));
            }
            PhotonValue::Array(arr)
        }
        24 => {
            // OperationRequest (nested) — reads opcode + param table, discards
            if *cursor >= data.len() {
                return PhotonValue::Null;
            }
            let _op = data[*cursor];
            *cursor += 1;
            skip_param_table(data, cursor);
            PhotonValue::Null
        }
        25 => {
            // OperationResponse (nested)
            if *cursor >= data.len() {
                return PhotonValue::Null;
            }
            let _op = data[*cursor];
            *cursor += 1;
            let _ret = read_i16_le(data, cursor);
            if *cursor >= data.len() {
                return PhotonValue::Null;
            }
            let tc = data[*cursor];
            *cursor += 1;
            let _ = deserialize_value(data, cursor, tc);
            skip_param_table(data, cursor);
            PhotonValue::Null
        }
        26 => {
            // EventData (nested)
            if *cursor >= data.len() {
                return PhotonValue::Null;
            }
            let _ev = data[*cursor];
            *cursor += 1;
            skip_param_table(data, cursor);
            PhotonValue::Null
        }
        27 => PhotonValue::Bool(false), // BooleanFalse
        28 => PhotonValue::Bool(true),  // BooleanTrue
        29 => PhotonValue::Short(0),    // ShortZero
        30 => PhotonValue::Int(0),      // IntZero
        31 => PhotonValue::Long(0),     // LongZero
        32 => PhotonValue::Float(0.0),  // FloatZero
        33 => PhotonValue::Double(0.0), // DoubleZero
        34 => PhotonValue::Byte(0),     // ByteZero
        64 => {
            // Array (array of arrays)
            let size = read_varint_u32(data, cursor) as usize;
            let mut arr = Vec::with_capacity(size.min(data.len()));
            for _ in 0..size {
                if *cursor >= data.len() {
                    break;
                }
                let tc = data[*cursor];
                *cursor += 1;
                arr.push(deserialize_value(data, cursor, tc));
            }
            PhotonValue::Array(arr)
        }
        66 => {
            // BooleanArray — bit-packed (8 bools per byte)
            let size = read_varint_u32(data, cursor) as usize;
            let mut arr = Vec::with_capacity(size.min(data.len()));
            let full = size / 8;
            for _ in 0..full {
                if *cursor >= data.len() {
                    break;
                }
                let v = data[*cursor];
                *cursor += 1;
                for bit in 0..8 {
                    arr.push(PhotonValue::Bool((v >> bit) & 1 != 0));
                }
            }
            let rest = size % 8;
            if rest > 0 && *cursor < data.len() {
                let v = data[*cursor];
                *cursor += 1;
                for bit in 0..rest {
                    arr.push(PhotonValue::Bool((v >> bit) & 1 != 0));
                }
            }
            PhotonValue::Array(arr)
        }
        67 => {
            // ByteArray
            let len = read_varint_u32(data, cursor) as usize;
            if *cursor + len > data.len() {
                return PhotonValue::Null;
            }
            let bytes = data[*cursor..*cursor + len].to_vec();
            *cursor += len;
            PhotonValue::Bytes(bytes)
        }
        68 => {
            // ShortArray
            let size = read_varint_u32(data, cursor) as usize;
            let mut arr = Vec::with_capacity(size.min(data.len()));
            for _ in 0..size {
                arr.push(PhotonValue::Short(read_i16_le(data, cursor)));
            }
            PhotonValue::Array(arr)
        }
        69 => {
            // FloatArray
            let size = read_varint_u32(data, cursor) as usize;
            let mut arr = Vec::with_capacity(size.min(data.len()));
            for _ in 0..size {
                if *cursor + 4 > data.len() {
                    break;
                }
                let v = f32::from_le_bytes([
                    data[*cursor],
                    data[*cursor + 1],
                    data[*cursor + 2],
                    data[*cursor + 3],
                ]);
                *cursor += 4;
                arr.push(PhotonValue::Float(v));
            }
            PhotonValue::Array(arr)
        }
        70 => {
            // DoubleArray
            let size = read_varint_u32(data, cursor) as usize;
            let mut arr = Vec::with_capacity(size.min(data.len()));
            for _ in 0..size {
                if *cursor + 8 > data.len() {
                    break;
                }
                let v = f64::from_le_bytes([
                    data[*cursor],
                    data[*cursor + 1],
                    data[*cursor + 2],
                    data[*cursor + 3],
                    data[*cursor + 4],
                    data[*cursor + 5],
                    data[*cursor + 6],
                    data[*cursor + 7],
                ]);
                *cursor += 8;
                arr.push(PhotonValue::Double(v));
            }
            PhotonValue::Array(arr)
        }
        71 => {
            // StringArray
            let size = read_varint_u32(data, cursor) as usize;
            let mut arr = Vec::with_capacity(size.min(data.len()));
            for _ in 0..size {
                let len = read_varint_u32(data, cursor) as usize;
                if *cursor + len > data.len() {
                    break;
                }
                let s = String::from_utf8_lossy(&data[*cursor..*cursor + len]).into_owned();
                *cursor += len;
                arr.push(PhotonValue::String(s));
            }
            PhotonValue::Array(arr)
        }
        73 => {
            // CompressedIntArray
            let size = read_varint_u32(data, cursor) as usize;
            let mut arr = Vec::with_capacity(size.min(data.len()));
            for _ in 0..size {
                let raw = read_varint_u32(data, cursor);
                arr.push(PhotonValue::Int(decode_zigzag_32(raw)));
            }
            PhotonValue::Array(arr)
        }
        74 => {
            // CompressedLongArray
            let size = read_varint_u32(data, cursor) as usize;
            let mut arr = Vec::with_capacity(size.min(data.len()));
            for _ in 0..size {
                let raw = read_varint_u64(data, cursor);
                arr.push(PhotonValue::Long(decode_zigzag_64(raw)));
            }
            PhotonValue::Array(arr)
        }
        83 => {
            // CustomTypeArray
            let size = read_varint_u32(data, cursor) as usize;
            if *cursor >= data.len() {
                return PhotonValue::Null;
            }
            let _type_code = data[*cursor];
            *cursor += 1;
            let mut arr = Vec::with_capacity(size.min(data.len()));
            for _ in 0..size {
                let len = read_varint_u32(data, cursor) as usize;
                if *cursor + len > data.len() {
                    break;
                }
                arr.push(PhotonValue::Bytes(data[*cursor..*cursor + len].to_vec()));
                *cursor += len;
            }
            PhotonValue::Array(arr)
        }
        84 => {
            // DictionaryArray
            let size = read_varint_u32(data, cursor) as usize;
            let mut arr = Vec::with_capacity(size.min(data.len()));
            for _ in 0..size {
                if *cursor >= data.len() {
                    break;
                }
                let tc = data[*cursor];
                *cursor += 1;
                arr.push(deserialize_value(data, cursor, tc));
            }
            PhotonValue::Array(arr)
        }
        85 => {
            // HashtableArray
            let size = read_varint_u32(data, cursor) as usize;
            let mut arr = Vec::with_capacity(size.min(data.len()));
            for _ in 0..size {
                if *cursor >= data.len() {
                    break;
                }
                let tc = data[*cursor];
                *cursor += 1;
                arr.push(deserialize_value(data, cursor, tc));
            }
            PhotonValue::Array(arr)
        }
        128..=228 => {
            // CustomTypeSlim
            let _custom_code = type_code - 128;
            let len = read_varint_u32(data, cursor) as usize;
            if *cursor + len > data.len() {
                return PhotonValue::Null;
            }
            let bytes = data[*cursor..*cursor + len].to_vec();
            *cursor += len;
            PhotonValue::Bytes(bytes)
        }
        _ => {
            // Unknown type — can't safely skip, return Null
            PhotonValue::Null
        }
    }
}

// ─── helpers ─────────────────────────────────────────────────────────────────

fn read_i16_be(data: &[u8], cursor: &mut usize) -> i16 {
    if *cursor + 2 > data.len() {
        *cursor = data.len();
        return 0;
    }
    let v = i16::from_be_bytes([data[*cursor], data[*cursor + 1]]);
    *cursor += 2;
    v
}

fn read_i32_be(data: &[u8], cursor: &mut usize) -> i32 {
    if *cursor + 4 > data.len() {
        *cursor = data.len();
        return 0;
    }
    let v = i32::from_be_bytes([
        data[*cursor],
        data[*cursor + 1],
        data[*cursor + 2],
        data[*cursor + 3],
    ]);
    *cursor += 4;
    v
}

fn read_i16_le(data: &[u8], cursor: &mut usize) -> i16 {
    if *cursor + 2 > data.len() {
        *cursor = data.len();
        return 0;
    }
    let v = i16::from_le_bytes([data[*cursor], data[*cursor + 1]]);
    *cursor += 2;
    v
}

fn read_varint_u32(data: &[u8], cursor: &mut usize) -> u32 {
    let mut value = 0u32;
    let mut shift = 0;
    while shift != 35 {
        if *cursor >= data.len() {
            return value;
        }
        let current = data[*cursor];
        *cursor += 1;
        value |= ((current & 0x7F) as u32) << shift;
        shift += 7;
        if current & 0x80 == 0 {
            return value;
        }
    }
    value
}

fn read_varint_u64(data: &[u8], cursor: &mut usize) -> u64 {
    let mut value = 0u64;
    let mut shift = 0;
    while shift != 70 {
        if *cursor >= data.len() {
            return value;
        }
        let current = data[*cursor];
        *cursor += 1;
        value |= ((current & 0x7F) as u64) << shift;
        shift += 7;
        if current & 0x80 == 0 {
            return value;
        }
    }
    value
}

fn decode_zigzag_32(value: u32) -> i32 {
    ((value >> 1) as i32) ^ -((value & 1) as i32)
}

fn decode_zigzag_64(value: u64) -> i64 {
    ((value >> 1) as i64) ^ -((value & 1) as i64)
}

// ─── Albion data extraction ─────────────────────────────────────────────

/// Local player state extracted from Join (opcode 2) or ChangeCluster (opcode 41).
#[derive(Clone, Debug, Default, serde::Serialize)]
pub struct PlayerState {
    pub player_name: String,
    pub guild_name: String,
    pub alliance_name: String,
    pub map_index: String,
    pub previous_map: String,
    /// Entity ID of the LOCAL player (Join param 0). Used to resolve the
    /// player's own damage in the meter — the local player never appears in
    /// NewCharacter events.
    pub local_object_id: Option<i64>,
}

/// Extract local player data from a parsed operation.
///
/// Detected by structure (not opcode): Response (type 3) with string at
/// param 2 (name) and param 8 (map). Survives game patches without maintenance.
pub fn extract_player_state(op: &ParsedOperation) -> Option<PlayerState> {
    // Join: Response (type 3) with name@2 + map@8.
    if op.message_type == 3 {
        if let (Some(PhotonValue::String(name)), Some(PhotonValue::String(map))) =
            (op.parameters.get(&2), op.parameters.get(&8))
        {
            if !name.is_empty() && !map.is_empty() {
                let mut state = PlayerState::default();
                state.player_name = name.clone();
                state.local_object_id = op.parameters.get(&0).and_then(|v| v.as_i64());
                state.map_index = map.split('@').next().unwrap_or(map).to_string();
                if let Some(PhotonValue::String(s)) = op.parameters.get(&58) {
                    state.guild_name = s.clone();
                }
                if let Some(PhotonValue::String(s)) = op.parameters.get(&79) {
                    state.alliance_name = s.clone();
                }
                if let Some(PhotonValue::String(s)) = op.parameters.get(&65) {
                    state.previous_map = s.clone();
                }
                return Some(state);
            }
        }
    }
    match op.albion_code {
        41 => {
            // ChangeCluster response
            let mut state = PlayerState::default();
            if let Some(PhotonValue::String(s)) = op.parameters.get(&0) {
                // Hideout format: "name@maincluster@..."
                state.map_index = s.split('@').next().unwrap_or(s).to_string();
            }
            Some(state)
        }
        _ => None,
    }
}

/// Party members extracted from PartyJoined (event 231).
pub fn extract_party(op: &ParsedOperation) -> Option<Vec<String>> {
    if op.albion_code != 231 {
        return None;
    }
    let mut names = Vec::new();
    if let Some(PhotonValue::Array(arr)) = op.parameters.get(&9) {
        for v in arr {
            if let PhotonValue::String(s) = v {
                if !s.is_empty() {
                    names.push(s.clone());
                }
            }
        }
    }
    Some(names)
}

/// Loot captured from OtherGrabbedLoot event.
#[derive(Clone, Debug, Default, serde::Serialize, serde::Deserialize)]
pub struct LootEvent {
    pub ts: String,          // ISO 8601 UTC (local clock)
    pub looted_by: String,   // param 2 — who looted
    pub looted_from: String, // param 1 — source (corpse/mob/chest)
    pub item_index: i32,     // param 4 — numeric item ID
    pub quantity: i32,       // param 5
    pub is_silver: bool,     // param 3 — true = silver, not an item
}

/// Albion player name: 3-20 chars, alphanumeric.
/// Game entities (GUILDBANNER_ELEPHANT, SCHEMA_01, MOB_...) have '_' —
/// this filters mechanics that fire the same loot event.
/// Player names CAN be purely numeric (e.g. "50369333670").
pub fn is_player_name(name: &str) -> bool {
    (3..=20).contains(&name.len())
        && name.chars().all(|c| c.is_ascii_alphanumeric())
}

/// Extract a LootEvent from OtherGrabbedLoot. Ignores silver.
///
/// Detected by structure (not opcode): event with body@1 (string) + looter@2
/// (string) + itemIndex@4 (int≠0) + qty@5 (int≠0).
pub fn extract_loot(op: &ParsedOperation) -> Option<LootEvent> {
    if op.message_type != 4 {
        return None;
    } // events only
    let looted_from = op
        .parameters
        .get(&1)
        .and_then(|v| v.as_string())?
        .to_string();
    let looted_by = op
        .parameters
        .get(&2)
        .and_then(|v| v.as_string())?
        .to_string();
    let item_index = op.parameters.get(&4).and_then(|v| v.as_i64()).unwrap_or(0) as i32;
    let quantity = op.parameters.get(&5).and_then(|v| v.as_i64()).unwrap_or(0) as i32;
    let is_silver = op
        .parameters
        .get(&3)
        .and_then(|v| {
            if let PhotonValue::Bool(b) = v {
                Some(*b)
            } else {
                None
            }
        })
        .unwrap_or(false);
    // Requires looter + item + qty. Silver and empty items filtered out.
    if looted_by.is_empty() || is_silver || item_index == 0 || quantity == 0 {
        return None;
    }
    // Only players: game entities (e.g. GUILDBANNER_ELEPHANT looting from SCHEMA_01
    // with qty >999) fire the same structure. Max game stack is 999.
    if !is_player_name(&looted_by) || !(1..=999).contains(&quantity) {
        return None;
    }
    let ts = now_iso_utc();
    Some(LootEvent {
        ts,
        looted_by,
        looted_from,
        item_index,
        quantity,
        is_silver: false,
    })
}

/// Self-loot: the server never echoes OtherGrabbedLoot back to the looter.
/// Detection requires tracking the client's own OpInventoryMoveItem request and
/// correlating with prior EvNewLoot/EvNewSimpleItem/EvAttachItemContainer events.
/// State lives in the Sniffer (`entities`, loot bag maps); these extractors
/// handle one operation at a time.
///
/// Observed sequence when opening a corpse/mob:
///   EvNewLoot(98)             → container_id, owner (corpse/mob name)
///   EvNewSimpleItem(32) /
///   EvNewEquipmentItem(30,E) /
///   EvNewSiegeBannerItem(31) → object_id, (item_index, quantity)
///   EvAttachItemContainer(99)→ links container_id ↔ uuid, lists object_ids by slot
///   EvDetachItemContainer(100)→ container closed
/// Actual loot: OpInventoryMoveItem(30, REQUEST) from slot X of container A to B —
/// when A≠B, the item left the loot bag. Resolve object_id→item and container_id→owner.

/// EvNewLoot (opcode 98, event): container_id → corpse/mob owner.
pub fn extract_new_loot_owner(op: &ParsedOperation) -> Option<(i64, String)> {
    if op.message_type != 4 || op.albion_code != 98 {
        return None;
    }
    let id = op.parameters.get(&0)?.as_i64()?;
    let owner = op
        .parameters
        .get(&3)
        .and_then(|v| v.as_string())?
        .to_string();
    Some((id, owner))
}

/// EvNewSimpleItem(32) / EvNewEquipmentItem(30,EVENT) / EvNewSiegeBannerItem(31):
/// same layout — objectId@0, itemNumId@1, quantity@2. The 30 here is EVENT;
/// not to be confused with OpInventoryMoveItem opcode 30 which is REQUEST.
pub fn extract_new_loot_item(op: &ParsedOperation) -> Option<(i64, i32, i32)> {
    if op.message_type != 4 || !matches!(op.albion_code, 30 | 31 | 32) {
        return None;
    }
    let object_id = op.parameters.get(&0)?.as_i64()?;
    let item_index = op.parameters.get(&1)?.as_i64()? as i32;
    let quantity = op.parameters.get(&2)?.as_i64()? as i32;
    Some((object_id, item_index, quantity))
}

/// EvAttachItemContainer (opcode 99, event): container_id, uuid (16 bytes,
/// used by the move-item request), and object_id list by slot index.
pub fn extract_attach_container(op: &ParsedOperation) -> Option<(i64, [u8; 16], Vec<i64>)> {
    if op.message_type != 4 || op.albion_code != 99 {
        return None;
    }
    let id = op.parameters.get(&0)?.as_i64()?;
    let uuid = as_uuid16(op.parameters.get(&1)?)?;
    let inventory = op.parameters.get(&3)?.as_array()?;
    let slots = inventory.iter().map(|v| v.as_i64().unwrap_or(0)).collect();
    Some((id, uuid, slots))
}

/// EvDetachItemContainer (opcode 100, event): container closed/despawned.
pub fn extract_detach_container(op: &ParsedOperation) -> Option<[u8; 16]> {
    if op.message_type != 4 || op.albion_code != 100 {
        return None;
    }
    as_uuid16(op.parameters.get(&0)?)
}

/// Client request moving an item between containers (OpInventoryMoveItem,
/// opcode 30, REQUEST). Returns None when from_uuid == to_uuid (rearranging
/// within the same container, not looting).
pub struct InventoryMove {
    pub from_slot: i32,
    pub from_uuid: [u8; 16],
    pub to_uuid: [u8; 16],
}
pub fn extract_inventory_move(op: &ParsedOperation) -> Option<InventoryMove> {
    if op.message_type != 2 || op.albion_code != 30 {
        return None;
    }
    let from_slot = op.parameters.get(&0).and_then(|v| v.as_i64()).unwrap_or(0) as i32;
    let from_uuid = as_uuid16(op.parameters.get(&1)?)?;
    let to_uuid = as_uuid16(op.parameters.get(&4)?)?;
    if from_uuid == to_uuid {
        return None;
    }
    Some(InventoryMove {
        from_slot,
        from_uuid,
        to_uuid,
    })
}

/// UUID arrives as a 16-element Photon array — decoded as either Bytes (raw
/// blob) or Array (0-255 values), depending on what the protocol encoder chose.
fn as_uuid16(v: &PhotonValue) -> Option<[u8; 16]> {
    match v {
        PhotonValue::Bytes(b) if b.len() == 16 => {
            let mut out = [0u8; 16];
            out.copy_from_slice(b);
            Some(out)
        }
        PhotonValue::Array(arr) if arr.len() == 16 => {
            let mut out = [0u8; 16];
            for (i, item) in arr.iter().enumerate() {
                out[i] = item.as_i64()? as u8;
            }
            Some(out)
        }
        _ => None,
    }
}

/// Build a LootEvent for a resolved self-loot. `looted_by` is always the
/// local player since we only reach here from our own move-item request.
pub fn self_loot_event(
    looted_by: String,
    looted_from: String,
    item_index: i32,
    quantity: i32,
) -> LootEvent {
    LootEvent {
        ts: now_iso_utc(),
        looted_by,
        looted_from,
        item_index,
        quantity,
        is_silver: false,
    }
}

/// Character registration: NewCharacter (event 29) maps entityId → name.
/// Combat events reference players by numeric ID, not name, so we need this map.
/// Param 0 = id, param 1 = name (AAT).
pub fn extract_new_character(op: &ParsedOperation) -> Option<(i64, String)> {
    if op.albion_code != 29 {
        return None;
    }
    let id = op.parameters.get(&0).and_then(|v| v.as_i64())?;
    let name = op
        .parameters
        .get(&1)
        .and_then(|v| v.as_string())?
        .to_string();
    if name.is_empty() {
        return None;
    }
    Some((id, name))
}

// HealthUpdate = event 6. causer=param 6, target=param 0, change=param 2
// (negative=damage, positive=heal), spell=param 7 (causing spell index).
// Indices from AAT and CAN change per patch — the sniffer logs the 1st
// HealthUpdate's params for calibration.
const HP_TARGET: u8 = 0;
const HP_CHANGE: u8 = 2;
const HP_CAUSER: u8 = 6;
const HP_SPELL: u8 = 7;

/// HP change event — basis for the damage meter.
#[derive(Clone, Debug, Default, serde::Serialize)]
pub struct HealthEvent {
    pub causer_id: i64,
    pub target_id: i64,
    pub change: f64,   // <0 = damage dealt, >0 = healing
    pub spell_id: i32, // causing spell index; -1 = unknown/auto-attack
}

pub fn extract_health(op: &ParsedOperation) -> Option<HealthEvent> {
    if op.albion_code != 6 {
        return None;
    }
    let target_id = op
        .parameters
        .get(&HP_TARGET)
        .and_then(|v| v.as_i64())
        .unwrap_or(-1);
    let causer_id = op
        .parameters
        .get(&HP_CAUSER)
        .and_then(|v| v.as_i64())
        .unwrap_or(-1);
    let spell_id = op
        .parameters
        .get(&HP_SPELL)
        .and_then(|v| v.as_i64())
        .unwrap_or(-1) as i32;
    let change = op.parameters.get(&HP_CHANGE).and_then(|v| match v {
        PhotonValue::Float(f) => Some(*f as f64),
        PhotonValue::Double(d) => Some(*d),
        other => other.as_i64().map(|i| i as f64),
    })?;
    if causer_id < 0 {
        return None;
    }
    Some(HealthEvent {
        causer_id,
        target_id,
        change,
        spell_id,
    })
}

/// Timeline window per player (WoW Details shows last few minutes;
/// 3 min covers an entire ZvZ fight without becoming infinite history).
pub const TIMELINE_SECS: u64 = 180;

/// Per-skill accumulator.
///
/// `hits` counts HIT EVENTS (HealthUpdate ticks), not casts. A 5-tick DoT
/// counts as 5. Same as WoW Details "Hits".
#[derive(Clone, Debug, Default, serde::Serialize)]
pub struct SpellAcc {
    pub hits: u64,
    pub total: f64,
    /// Biggest single hit of this skill — the "crit" players compare.
    pub max_hit: f64,
}

/// Per-player damage accumulator (key = causer_id).
///
/// Damage only. Healing is intentionally ignored — the value is in having
/// a complete damage listing, not competing with AAT's heal panel.
#[derive(Clone, Debug, Default, serde::Serialize)]
pub struct DamageAcc {
    pub damage: f64,
    /// spell_id → per-skill accumulation.
    pub spells: HashMap<i32, SpellAcc>,
    /// Sliding window: (epoch_sec, damage) for the last TIMELINE_SECS.
    pub timeline: VecDeque<(u64, f64)>,
    /// First and last hit (epoch secs) — base for active-time DPS,
    /// which is fair to players who joined mid-fight.
    pub first_hit: Option<u64>,
    pub last_hit: Option<u64>,
}

impl DamageAcc {
    /// Record a hit. `now` = epoch seconds.
    pub fn record(&mut self, spell_id: i32, amount: f64, now: u64) {
        self.damage += amount;
        let sp = self.spells.entry(spell_id).or_default();
        sp.hits += 1;
        sp.total += amount;
        if amount > sp.max_hit {
            sp.max_hit = amount;
        }

        self.first_hit.get_or_insert(now);
        self.last_hit = Some(now);

        match self.timeline.back_mut() {
            Some((sec, dmg)) if *sec == now => *dmg += amount,
            _ => self.timeline.push_back((now, amount)),
        }
        let cutoff = now.saturating_sub(TIMELINE_SECS);
        while self.timeline.front().is_some_and(|(s, _)| *s < cutoff) {
            self.timeline.pop_front();
        }
    }

    /// DPS over active time (first→last hit), minimum 1s.
    /// Merge another accumulator into this one.
    ///
    /// A player can have MULTIPLE entity IDs in a session: the game assigns a
    /// new ID when they leave and re-enter your visibility range. Without
    /// merging, the same person appears as multiple lines with split damage.
    pub fn merge(&mut self, other: &DamageAcc) {
        self.damage += other.damage;
        for (sid, sp) in &other.spells {
            let e = self.spells.entry(*sid).or_default();
            e.hits += sp.hits;
            e.total += sp.total;
            if sp.max_hit > e.max_hit {
                e.max_hit = sp.max_hit;
            }
        }
        // Same second sums into one bucket; linear scan is fine (180 entries).
        for (sec, d) in &other.timeline {
            match self.timeline.iter_mut().find(|(s, _)| s == sec) {
                Some((_, acc)) => *acc += d,
                None => self.timeline.push_back((*sec, *d)),
            }
        }
        self.timeline.make_contiguous().sort_by_key(|(s, _)| *s);
        // Active-time DPS: first hit from any ID to last.
        self.first_hit = [self.first_hit, other.first_hit]
            .into_iter()
            .flatten()
            .min();
        self.last_hit = [self.last_hit, other.last_hit].into_iter().flatten().max();
    }

    pub fn dps(&self) -> f64 {
        match (self.first_hit, self.last_hit) {
            (Some(a), Some(b)) => self.damage / (b.saturating_sub(a).max(1) as f64),
            _ => 0.0,
        }
    }
}

/// Simplified sell offer — what we store in our price database.
#[derive(Clone, Debug, serde::Serialize)]
pub struct MarketOffer {
    pub item_id: String,
    pub quality: i32,
    pub unit_price_silver: i64,
}

/// Result from parsing a marketplace response: simplified offers (for our
/// price feed) + raw orders (for verbatim AODP upload).
#[derive(Clone, Debug, Default)]
pub struct MarketCapture {
    pub offers: Vec<MarketOffer>,
    /// Raw orders (game JSON) — ALL sell orders, unmodified, for AODP upload.
    /// Prices here are in silver*10000 (original protocol format).
    pub raw_orders: Vec<serde_json::Value>,
}

/// Normalize ItemTypeId to ADP/database format. Currently a passthrough —
/// the conversion to game_name happens in the sniffer via to_game_name
/// (mapping downloaded from the backend).
fn normalize_item_id(base_id: &str, _ench: i32) -> String {
    base_id.to_string()
}

/// Parse a marketplace response (AuctionGetOffers = sell, AuctionGetRequests = buy).
///
/// Detected by structure: array of JSON strings with "UnitPriceSilver" +
/// "ItemTypeId". Price is ×10000 in the protocol. Both offer and request
/// orders forwarded to AODP verbatim; only sell offers become prices in our DB.
pub fn extract_market(op: &ParsedOperation) -> MarketCapture {
    let mut cap = MarketCapture::default();
    if op.message_type != 3 {
        return cap;
    }
    for v in op.parameters.values() {
        let PhotonValue::Array(arr) = v else { continue };
        for item in arr {
            let PhotonValue::String(s) = item else {
                continue;
            };
            if !s.starts_with('{') || !s.contains("UnitPriceSilver") {
                continue;
            }
            let Ok(j) = serde_json::from_str::<serde_json::Value>(s) else {
                continue;
            };
            let atype = j.get("AuctionType").and_then(|a| a.as_str()).unwrap_or("");
            if atype != "offer" && atype != "request" {
                continue;
            }
            let Some(base_id) = j
                .get("ItemTypeId")
                .and_then(|x| x.as_str())
                .map(String::from)
            else {
                continue;
            };
            let raw_price = j
                .get("UnitPriceSilver")
                .and_then(|x| x.as_i64())
                .unwrap_or(0);
            if raw_price <= 0 {
                continue;
            }
            // Raw order for AODP (verbatim): sell and buy.
            cap.raw_orders.push(j.clone());
            // Our DB only stores sell offers (id with @enchant, price/10000).
            if atype == "offer" {
                let ench = j
                    .get("EnchantmentLevel")
                    .and_then(|x| x.as_i64())
                    .unwrap_or(0) as i32;
                let item_id = normalize_item_id(&base_id, ench);
                let quality = j.get("QualityLevel").and_then(|x| x.as_i64()).unwrap_or(1) as i32;
                cap.offers.push(MarketOffer {
                    item_id,
                    quality,
                    unit_price_silver: raw_price / 10_000,
                });
            }
        }
    }
    cap
}

/// Market history request info (AuctionGetItemAverageStats), kept for
/// correlating with the response via message-id (param 255).
#[derive(Clone, Debug)]
pub struct HistoryReq {
    pub albion_id: i32,
    pub quality: i32,
    pub timescale: i32,
}

/// A single bucket from the in-game market history chart.
#[derive(Clone, Debug, serde::Serialize)]
pub struct HistoryBucket {
    pub bucket_ts: i64,
    pub item_count: i64,
    pub silver_amount: i64,
}

/// Detect market history REQUEST and return (message_id, info).
/// Structure: request (type 2) with item id@1, quality@2, timescale@3 (0..=2),
/// message id@255. Timescale 0..=2 makes the signature distinctive.
/// Applies the protocol's negative item id quirk (128-256 arrive negative).
pub fn extract_history_request(op: &ParsedOperation) -> Option<(u64, HistoryReq)> {
    if op.message_type != 2 {
        return None;
    }
    let msg_id = op.parameters.get(&255)?.as_i64()? as u64;
    let mut albion_id = op.parameters.get(&1)?.as_i64()? as i32;
    let quality = op.parameters.get(&2).and_then(|v| v.as_i64()).unwrap_or(1) as i32;
    let timescale = op.parameters.get(&3)?.as_i64()? as i32;
    if !(0..=2).contains(&timescale) {
        return None;
    }
    if !(1..=5).contains(&quality) {
        return None;
    }
    // Protocol quirk: ids 128-256 arrive as negative (signed byte).
    if albion_id < 0 && albion_id > -129 {
        albion_id += 256;
    }
    if albion_id < 1 {
        return None;
    }
    Some((
        msg_id,
        HistoryReq {
            albion_id,
            quality,
            timescale,
        },
    ))
}

/// Detect market history RESPONSE: (message_id, buckets).
/// Structure: response (type 3) with 3 parallel arrays — item_count@0,
/// silver@1, timestamp@2 — and message id@255. Applies negative quantity
/// fix (same as AODP: -124..-1 → +256, < -124 discarded).
pub fn extract_history_response(op: &ParsedOperation) -> Option<(u64, Vec<HistoryBucket>)> {
    if op.message_type != 3 {
        return None;
    }
    let msg_id = op.parameters.get(&255)?.as_i64()? as u64;
    let counts = op.parameters.get(&0)?.as_array()?;
    let silvers = op.parameters.get(&1)?.as_array()?;
    let stamps = op.parameters.get(&2)?.as_array()?;
    let n = counts.len();
    if n == 0 || silvers.len() != n || stamps.len() != n {
        return None;
    }
    let mut buckets = Vec::with_capacity(n);
    for i in 0..n {
        let mut count = counts[i].as_i64()?;
        let silver = silvers[i].as_i64()?;
        let ts = stamps[i].as_i64()?;
        if count < 0 {
            if count < -124 {
                continue;
            } // no known interpretation — discard
            count += 256;
        }
        if count <= 0 || ts <= 0 {
            continue;
        }
        buckets.push(HistoryBucket {
            bucket_ts: ts,
            item_count: count,
            silver_amount: silver,
        });
    }
    if buckets.is_empty() {
        return None;
    }
    Some((msg_id, buckets))
}

/// Gold market prices (GoldMarketGetAverageInfo response).
/// Global (no location) — AODP only needs the server region.
#[derive(Clone, Debug, Default)]
pub struct GoldPrices {
    pub prices: Vec<i64>,
    pub timestamps: Vec<i64>,
}

/// Detect gold response by structure: two parallel arrays — param 0 = gold
/// prices (sane range), param 1 = unix timestamps. The timestamp heuristic
/// (>1e9) distinguishes from other dual-array responses.
pub fn extract_gold(op: &ParsedOperation) -> Option<GoldPrices> {
    if op.message_type != 3 {
        return None;
    }
    let prices_arr = op.parameters.get(&0)?.as_array()?;
    let ts_arr = op.parameters.get(&1)?.as_array()?;
    if prices_arr.is_empty() || prices_arr.len() != ts_arr.len() {
        return None;
    }
    let prices: Vec<i64> = prices_arr.iter().filter_map(|v| v.as_i64()).collect();
    let timestamps: Vec<i64> = ts_arr.iter().filter_map(|v| v.as_i64()).collect();
    if prices.len() != prices_arr.len() || timestamps.len() != ts_arr.len() {
        return None;
    }
    if !timestamps.iter().all(|&t| t > 1_000_000_000) {
        return None;
    } // unix > 2001
    if !prices.iter().all(|&p| (1..=1_000_000).contains(&p)) {
        return None;
    } // gold in sane range
    Some(GoldPrices { prices, timestamps })
}

pub fn now_iso_utc() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    // ISO 8601 without external deps — second precision is sufficient for lootlog.
    let (y, mo, d, h, mi, s) = epoch_to_ymd_hms(secs);
    format!("{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z", y, mo, d, h, mi, s)
}

/// Convert epoch seconds to (year, month, day, hour, min, sec) UTC.
fn epoch_to_ymd_hms(secs: u64) -> (i32, u32, u32, u32, u32, u32) {
    let days = (secs / 86400) as i64;
    let rem = secs % 86400;
    let h = (rem / 3600) as u32;
    let mi = ((rem % 3600) / 60) as u32;
    let s = (rem % 60) as u32;
    // Days since 1970-01-01 → civil date (Howard Hinnant's algorithm)
    let z = days + 719468;
    let era = if z >= 0 {
        z / 146097
    } else {
        (z - 146096) / 146097
    };
    let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe as i32 + era as i32 * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let mo = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (y + if mo <= 2 { 1 } else { 0 }, mo, d, h, mi, s)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_normalize_item_id_passthrough() {
        // normalize_item_id is now a passthrough — conversion to game_name
        // happens in the sniffer via to_game_name (backend mapping).
        assert_eq!(normalize_item_id("T4_FIBER", 0), "T4_FIBER");
        assert_eq!(
            normalize_item_id("T4_FIBER_LEVEL2@2", 2),
            "T4_FIBER_LEVEL2@2"
        );
        assert_eq!(normalize_item_id("T4_BAG@1", 1), "T4_BAG@1");
        assert_eq!(
            normalize_item_id("T4_2H_CURSEDSTAFF", 0),
            "T4_2H_CURSEDSTAFF"
        );
    }

    /// A player gets a new entity ID when re-entering visibility, causing
    /// split damage lines and React key collisions that duplicated the list.
    #[test]
    fn test_merge_combines_ids_of_the_same_player() {
        let mut a = DamageAcc::default();
        a.record(10, 100.0, 1000);
        a.record(10, 50.0, 1000); // same second, same skill
        a.record(20, 30.0, 1001);

        let mut b = DamageAcc::default();
        b.record(10, 200.0, 1001); // repeated skill, repeated second
        b.record(30, 7.0, 1005);

        a.merge(&b);

        assert_eq!(a.damage as i64, 387);
        assert_eq!(a.spells[&10].hits, 3, "hits from both sum together");
        assert_eq!(a.spells[&10].total as i64, 350);
        assert_eq!(
            a.spells[&10].max_hit as i64, 200,
            "biggest hit comes from the other id"
        );
        assert_eq!(a.spells[&30].hits, 1, "skill only the other id had enters");

        // Same second must become ONE bucket — otherwise the chart draws two
        // points on the same x.
        let secs: Vec<u64> = a.timeline.iter().map(|(s, _)| *s).collect();
        assert_eq!(
            secs,
            vec![1000, 1001, 1005],
            "sorted and without repeated second"
        );
        assert_eq!(
            a.timeline.iter().find(|(s, _)| *s == 1001).unwrap().1 as i64,
            230
        );

        assert_eq!(a.first_hit, Some(1000));
        assert_eq!(
            a.last_hit,
            Some(1005),
            "DPS uses first hit from any ID to last"
        );
    }

    #[test]
    fn test_merge_with_empty_acc_does_not_invent_time() {
        let mut empty = DamageAcc::default();
        let mut full = DamageAcc::default();
        full.record(1, 10.0, 500);

        empty.merge(&full);
        assert_eq!(empty.first_hit, Some(500));

        let mut other = DamageAcc::default();
        other.record(1, 10.0, 500);
        other.merge(&DamageAcc::default());
        assert_eq!(
            other.first_hit,
            Some(500),
            "empty must not zero out first_hit"
        );
        assert_eq!(other.damage as i64, 10);
    }

    #[test]
    fn test_damage_acc_aggregates_by_skill() {
        let mut acc = DamageAcc::default();
        acc.record(10, 100.0, 1000);
        acc.record(10, 300.0, 1000);
        acc.record(20, 50.0, 1000);
        assert_eq!(acc.damage, 450.0);
        let s10 = &acc.spells[&10];
        assert_eq!(s10.hits, 2);
        assert_eq!(s10.total, 400.0);
        assert_eq!(s10.max_hit, 300.0, "biggest hit of the skill");
    }

    #[test]
    fn test_timeline_groups_same_second() {
        let mut acc = DamageAcc::default();
        acc.record(1, 10.0, 500);
        acc.record(1, 15.0, 500); // same second → sums into one bucket
        acc.record(1, 7.0, 501);
        assert_eq!(acc.timeline.len(), 2);
        assert_eq!(acc.timeline[0], (500, 25.0));
        assert_eq!(acc.timeline[1], (501, 7.0));
    }

    #[test]
    fn test_timeline_descarta_fora_da_janela() {
        let mut acc = DamageAcc::default();
        acc.record(1, 10.0, 1000);
        // TIMELINE_SECS later, the old bucket must have been evicted.
        acc.record(1, 10.0, 1000 + TIMELINE_SECS + 1);
        assert_eq!(acc.timeline.len(), 1);
        assert_eq!(acc.timeline[0].0, 1000 + TIMELINE_SECS + 1);
        // Total session damage is NOT affected by the window.
        assert_eq!(acc.damage, 20.0);
    }

    #[test]
    fn test_dps_usa_tempo_ativo() {
        let mut acc = DamageAcc::default();
        acc.record(1, 1000.0, 100);
        acc.record(1, 1000.0, 110); // 2000 damage in 10s active
        assert_eq!(acc.dps(), 200.0);
    }

    #[test]
    fn test_dps_with_single_hit_does_not_divide_by_zero() {
        let mut acc = DamageAcc::default();
        acc.record(1, 500.0, 100);
        assert_eq!(acc.dps(), 500.0, "minimum 1s window");
    }

    #[test]
    fn test_varint() {
        let data = [0x80, 0x01]; // 128
        let mut cursor = 0;
        assert_eq!(read_varint_u32(&data, &mut cursor), 128);
        assert_eq!(cursor, 2);
    }

    #[test]
    fn test_zigzag() {
        assert_eq!(decode_zigzag_32(0), 0);
        assert_eq!(decode_zigzag_32(1), -1);
        assert_eq!(decode_zigzag_32(2), 1);
        assert_eq!(decode_zigzag_32(3), -2);
    }

    fn fragment_payload(start_seq: i32, total_length: i32, fragment_offset: i32, data: &[u8]) -> Vec<u8> {
        let mut payload = Vec::with_capacity(20 + data.len());
        payload.extend_from_slice(&start_seq.to_be_bytes());
        payload.extend_from_slice(&0i32.to_be_bytes());
        payload.extend_from_slice(&0i32.to_be_bytes());
        payload.extend_from_slice(&total_length.to_be_bytes());
        payload.extend_from_slice(&fragment_offset.to_be_bytes());
        payload.extend_from_slice(data);
        payload
    }

    #[test]
    fn test_empty_packet() {
        let mut parser = PhotonParser::new();
        let ops = parser.parse(&[0u8; 5]);
        assert!(ops.is_empty());
    }

    #[test]
    fn test_fragment_rejects_negative_and_oversize_lengths() {
        let mut parser = PhotonParser::new();
        let mut ops = Vec::new();
        parser.parse_fragment(&fragment_payload(1, -1, 0, &[]), &mut ops);
        parser.parse_fragment(&fragment_payload(2, 1, -1, &[]), &mut ops);
        parser.parse_fragment(
            &fragment_payload(3, (MAX_FRAGMENT_LENGTH + 1) as i32, 0, &[]),
            &mut ops,
        );
        assert!(parser.fragments.is_empty());
        assert_eq!(parser.pending_fragment_bytes, 0);
    }

    #[test]
    fn test_fragment_rejects_aggregate_limit() {
        let mut parser = PhotonParser::new();
        let mut ops = Vec::new();
        for sequence in 0..(MAX_PENDING_FRAGMENT_BYTES / MAX_FRAGMENT_LENGTH) {
            parser.parse_fragment(
                &fragment_payload(sequence as i32, MAX_FRAGMENT_LENGTH as i32, 0, &[]),
                &mut ops,
            );
        }
        parser.parse_fragment(
            &fragment_payload(99, 1, 0, &[]),
            &mut ops,
        );
        assert_eq!(parser.fragments.len(), MAX_PENDING_FRAGMENT_BYTES / MAX_FRAGMENT_LENGTH);
        assert_eq!(parser.pending_fragment_bytes, MAX_PENDING_FRAGMENT_BYTES);
    }

    #[test]
    fn test_fragment_expiry_releases_aggregate_capacity() {
        let mut parser = PhotonParser::new();
        let mut ops = Vec::new();
        parser.parse_fragment(
            &fragment_payload(1, MAX_FRAGMENT_LENGTH as i32, 0, &[]),
            &mut ops,
        );
        parser
            .fragments
            .get_mut(&1)
            .expect("fragmento pendente")
            .updated_at = Instant::now() - FRAGMENT_TTL;
        parser.parse_fragment(&fragment_payload(2, 1, 0, &[]), &mut ops);
        assert!(!parser.fragments.contains_key(&1));
        assert!(parser.fragments.contains_key(&2));
        assert_eq!(parser.pending_fragment_bytes, 1);
    }

    #[test]
    fn test_short_command_length_no_panic() {
        // Valid Photon header (flags=0, command_count=1) + command with
        // command_length=5 (< 12). Must return empty without panicking
        // (would underflow in length-12 and slice start>end without guard).
        let mut data = vec![0u8; 24];
        data[3] = 1; // command_count = 1
        data[12] = 6; // command_type = SendReliable
        data[19] = 5; // command_length (BE) = 5, < 12
        let mut parser = PhotonParser::new();
        let ops = parser.parse(&data); // must not panic
        assert!(ops.is_empty());
    }

    #[test]
    fn test_is_player_name() {
        assert!(is_player_name("Slayner"));
        assert!(is_player_name("Player123"));
        assert!(is_player_name("50369333670")); // purely numeric name
        assert!(!is_player_name("GUILDBANNER_ELEPHANT")); // underscore
        assert!(!is_player_name("SCHEMA_01"));
        assert!(!is_player_name("ab")); // too short
    }

    #[test]
    fn test_extract_loot_filters_game_entities() {
        let mut params = HashMap::new();
        params.insert(1u8, PhotonValue::String("SCHEMA_01".into()));
        params.insert(2u8, PhotonValue::String("GUILDBANNER_ELEPHANT".into()));
        params.insert(4u8, PhotonValue::Int(1234));
        params.insert(5u8, PhotonValue::Int(183758138));
        let op = ParsedOperation {
            message_type: 4,
            albion_code: 256,
            parameters: params,
        };
        assert!(extract_loot(&op).is_none());

        let mut params = HashMap::new();
        params.insert(1u8, PhotonValue::String("DeadGuy".into()));
        params.insert(2u8, PhotonValue::String("Looter".into()));
        params.insert(4u8, PhotonValue::Int(1234));
        params.insert(5u8, PhotonValue::Int(3));
        let op = ParsedOperation {
            message_type: 4,
            albion_code: 256,
            parameters: params,
        };
        assert!(extract_loot(&op).is_some());
    }

    #[test]
    fn test_self_loot_chain() {
        let uuid: [u8; 16] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16];
        let other_uuid: [u8; 16] = [9; 16];

        // EvNewLoot(98): container 500 belongs to mob "MOB_DIREWOLF".
        let mut p = HashMap::new();
        p.insert(0u8, PhotonValue::Int(500));
        p.insert(3u8, PhotonValue::String("MOB_DIREWOLF".into()));
        let op = ParsedOperation {
            message_type: 4,
            albion_code: 98,
            parameters: p,
        };
        let (id, owner) = extract_new_loot_owner(&op).expect("EvNewLoot");
        assert_eq!((id, owner.as_str()), (500, "MOB_DIREWOLF"));

        // EvNewSimpleItem(32): object 7001 = item 1234, qty 3.
        let mut p = HashMap::new();
        p.insert(0u8, PhotonValue::Int(7001));
        p.insert(1u8, PhotonValue::Int(1234));
        p.insert(2u8, PhotonValue::Int(3));
        let op = ParsedOperation {
            message_type: 4,
            albion_code: 32,
            parameters: p,
        };
        let (object_id, item_index, quantity) =
            extract_new_loot_item(&op).expect("EvNewSimpleItem");
        assert_eq!((object_id, item_index, quantity), (7001, 1234, 3));

        // EvNewEquipmentItem(30, EVENT) must not be confused with
        // OpInventoryMoveItem(30, REQUEST) below — same number, different message_type.
        let mut p = HashMap::new();
        p.insert(0u8, PhotonValue::Bytes(uuid.to_vec()));
        let op = ParsedOperation {
            message_type: 2,
            albion_code: 30,
            parameters: p,
        };
        assert!(extract_new_loot_item(&op).is_none());

        // EvAttachItemContainer(99): container 500, uuid, slot 2 = object 7001.
        let mut p = HashMap::new();
        p.insert(0u8, PhotonValue::Int(500));
        p.insert(1u8, PhotonValue::Bytes(uuid.to_vec()));
        p.insert(
            3u8,
            PhotonValue::Array(vec![
                PhotonValue::Int(0),
                PhotonValue::Int(0),
                PhotonValue::Int(7001),
            ]),
        );
        let op = ParsedOperation {
            message_type: 4,
            albion_code: 99,
            parameters: p,
        };
        let (cid, got_uuid, slots) = extract_attach_container(&op).expect("EvAttachItemContainer");
        assert_eq!(cid, 500);
        assert_eq!(got_uuid, uuid);
        assert_eq!(slots, vec![0, 0, 7001]);

        // OpInventoryMoveItem(30, REQUEST): slot 2 from loot bag (uuid) to inventory (other_uuid).
        let mut p = HashMap::new();
        p.insert(0u8, PhotonValue::Int(2));
        p.insert(1u8, PhotonValue::Bytes(uuid.to_vec()));
        p.insert(4u8, PhotonValue::Bytes(other_uuid.to_vec()));
        let op = ParsedOperation {
            message_type: 2,
            albion_code: 30,
            parameters: p,
        };
        let mv = extract_inventory_move(&op).expect("OpInventoryMoveItem");
        assert_eq!(mv.from_slot, 2);
        assert_eq!(mv.from_uuid, uuid);
        assert_eq!(mv.to_uuid, other_uuid);

        // Same source/destination container = rearranging, not looting.
        let mut p = HashMap::new();
        p.insert(0u8, PhotonValue::Int(0));
        p.insert(1u8, PhotonValue::Bytes(uuid.to_vec()));
        p.insert(4u8, PhotonValue::Bytes(uuid.to_vec()));
        let op = ParsedOperation {
            message_type: 2,
            albion_code: 30,
            parameters: p,
        };
        assert!(extract_inventory_move(&op).is_none());

        let loot = self_loot_event("Slayner".into(), owner, item_index, quantity);
        assert_eq!(loot.looted_by, "Slayner");
        assert_eq!(loot.looted_from, "MOB_DIREWOLF");
        assert_eq!(loot.item_index, 1234);
        assert_eq!(loot.quantity, 3);
    }

    #[test]
    fn test_extract_market() {
        let offer = r#"{"UnitPriceSilver":1250000,"ItemTypeId":"T4_BAG","QualityLevel":2,"EnchantmentLevel":1,"AuctionType":"offer","LocationId":""}"#;
        let buy = r#"{"UnitPriceSilver":990000,"ItemTypeId":"T4_BAG","QualityLevel":1,"EnchantmentLevel":0,"AuctionType":"request"}"#;
        let mut params = HashMap::new();
        params.insert(
            0u8,
            PhotonValue::Array(vec![
                PhotonValue::String(offer.into()),
                PhotonValue::String(buy.into()),
            ]),
        );
        let op = ParsedOperation {
            message_type: 3,
            albion_code: 75,
            parameters: params,
        };
        let cap = extract_market(&op);
        // Our DB only stores sell ("offer").
        assert_eq!(cap.offers.len(), 1);
        assert_eq!(cap.offers[0].item_id, "T4_BAG");
        assert_eq!(cap.offers[0].unit_price_silver, 125);
        assert_eq!(cap.offers[0].quality, 2);
        // AODP gets sell and buy, verbatim (original price ×10000).
        assert_eq!(cap.raw_orders.len(), 2);
        assert_eq!(cap.raw_orders[0]["UnitPriceSilver"].as_i64(), Some(1250000));
    }

    #[test]
    fn test_extract_history() {
        // Request: negative item id (-121 → 135), quality 2, timescale 1, msg 42.
        let mut req = HashMap::new();
        req.insert(1u8, PhotonValue::Int(-121));
        req.insert(2u8, PhotonValue::Byte(2));
        req.insert(3u8, PhotonValue::Byte(1));
        req.insert(255u8, PhotonValue::Long(42));
        let op = ParsedOperation {
            message_type: 2,
            albion_code: 100,
            parameters: req,
        };
        let (mid, info) = extract_history_request(&op).expect("should detect request");
        assert_eq!(mid, 42);
        assert_eq!(info.albion_id, 135); // -121 + 256
        assert_eq!(info.quality, 2);
        assert_eq!(info.timescale, 1);

        // Response: 2 buckets + 1 with interpretable negative qty (-120 → 136).
        let mut resp = HashMap::new();
        resp.insert(
            0u8,
            PhotonValue::Array(vec![PhotonValue::Int(10), PhotonValue::Int(-120)]),
        );
        resp.insert(
            1u8,
            PhotonValue::Array(vec![PhotonValue::Long(50000), PhotonValue::Long(60000)]),
        );
        resp.insert(
            2u8,
            PhotonValue::Array(vec![
                PhotonValue::Long(1784203200),
                PhotonValue::Long(1784289600),
            ]),
        );
        resp.insert(255u8, PhotonValue::Long(42));
        let op = ParsedOperation {
            message_type: 3,
            albion_code: 100,
            parameters: resp,
        };
        let (mid, buckets) = extract_history_response(&op).expect("should detect response");
        assert_eq!(mid, 42);
        assert_eq!(buckets.len(), 2);
        assert_eq!(buckets[0].item_count, 10);
        assert_eq!(buckets[1].item_count, 136); // -120 + 256
    }

    #[test]
    fn test_extract_gold() {
        let mut params = HashMap::new();
        params.insert(
            0u8,
            PhotonValue::Array(vec![PhotonValue::Int(4200), PhotonValue::Int(4250)]),
        );
        params.insert(
            1u8,
            PhotonValue::Array(vec![
                PhotonValue::Long(1784203200),
                PhotonValue::Long(1784289600),
            ]),
        );
        let op = ParsedOperation {
            message_type: 3,
            albion_code: 99,
            parameters: params,
        };
        let g = extract_gold(&op).expect("should detect gold");
        assert_eq!(g.prices, vec![4200, 4250]);
        assert_eq!(g.timestamps, vec![1784203200, 1784289600]);

        // Two arrays but no unix timestamp → not gold.
        let mut p2 = HashMap::new();
        p2.insert(0u8, PhotonValue::Array(vec![PhotonValue::Int(5)]));
        p2.insert(1u8, PhotonValue::Array(vec![PhotonValue::Int(9)]));
        let op2 = ParsedOperation {
            message_type: 3,
            albion_code: 99,
            parameters: p2,
        };
        assert!(extract_gold(&op2).is_none());
    }

    #[test]
    fn test_epoch_to_ymd_hms() {
        // 2026-07-16T12:00:00Z = 1784203200 epoch seconds
        let (y, mo, d, h, mi, s) = epoch_to_ymd_hms(1784203200);
        assert_eq!((y, mo, d, h, mi, s), (2026, 7, 16, 12, 0, 0));
        // Epoch zero = 1970-01-01T00:00:00Z
        let (y0, mo0, d0, h0, mi0, s0) = epoch_to_ymd_hms(0);
        assert_eq!((y0, mo0, d0, h0, mi0, s0), (1970, 1, 1, 0, 0, 0));
    }
}
#[cfg(test)]
mod pacote_tests {
    use super::*;

    /// Build a complete Photon packet: header + 1 command + 1 Event with 8
    /// params. Originally a performance probe (~0.34µs/packet); now a
    /// correctness test exercising the full parse path end-to-end.
    fn pacote_evento() -> Vec<u8> {
        let mut op = Vec::new();
        op.push(0u8); // skip
        op.push(4u8); // message_type = Event
        op.push(6u8); // photon_code
        op.push(8u8); // param count
        for k in 0..8u8 {
            op.push(k); // key
            op.push(11u8); // type_code = Int1
            op.push(k * 7); // value
        }
        let cmd_len = 12 + op.len();
        let mut pkt = Vec::new();
        pkt.extend_from_slice(&0i16.to_be_bytes()); // peer_id
        pkt.push(0u8); // flags
        pkt.push(1u8); // command_count
        pkt.extend_from_slice(&0i32.to_be_bytes()); // crc
        pkt.extend_from_slice(&0i32.to_be_bytes()); // user_data
        pkt.push(6u8); // command_type = SendReliable
        pkt.extend_from_slice(&[0, 0, 0]);
        pkt.extend_from_slice(&(cmd_len as i32).to_be_bytes());
        pkt.extend_from_slice(&0i32.to_be_bytes()); // seq
        pkt.extend_from_slice(&op);
        pkt
    }

    #[test]
    fn parseia_evento_completo() {
        let mut p = PhotonParser::new();
        let ops = p.parse(&pacote_evento());
        assert_eq!(ops.len(), 1);
        assert_eq!(ops[0].message_type, 4, "Event");
        assert_eq!(ops[0].parameters.len(), 8);
        assert_eq!(ops[0].parameters[&3].as_i64(), Some(21), "3 * 7");
    }

    #[test]
    fn pacote_curto_nao_entra_em_panico() {
        let mut p = PhotonParser::new();
        for n in 0..14usize {
            assert!(p.parse(&vec![0u8; n]).is_empty(), "len={n}");
        }
    }

    #[test]
    fn pacote_criptografado_e_ignorado() {
        let mut pkt = pacote_evento();
        pkt[2] = 0x01; // flags = encrypted
        assert!(PhotonParser::new().parse(&pkt).is_empty());
    }
}

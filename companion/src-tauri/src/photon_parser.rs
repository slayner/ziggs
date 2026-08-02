// Photon protocol parser — extrai dados do Albion Online dos pacotes UDP Photon.
//
// Baseado no AAT (Triky313/AlbionOnline-StatisticsAnalysis):
//   - Photon header: 12 bytes, big-endian
//   - Command header: 12 bytes, big-endian
//   - Protocol18: little-endian, varint+ZigZag
//   - Albion opcode: param 253 (request/response), param 252 (event)
//
// Opcodes relevantes:
//   Join (2):        response com mapa, nome, guild do jogador local
//   ChangeCluster(41): response quando muda de mapa
//   NewCharacter(29): event quando outro jogador aparece
//   PartyJoined(231): event com lista completa da party
//   PartyPlayerJoined(233): event quando alguém entra na party
//   PartyPlayerLeft(235): event quando alguém sai da party

use std::collections::{HashMap, VecDeque};

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
    pub message_type: u8,      // 2=Request, 3=Response, 4=Event
    pub albion_code: i16,      // opcode extraído de param 252/253
    pub parameters: HashMap<u8, PhotonValue>,
}

pub struct PhotonParser {
    /// Fragmentos pendentes: startSequenceNumber → (buffer total, offset recebido)
    fragments: HashMap<i32, (Vec<u8>, Vec<bool>)>,
}

impl PhotonParser {
    pub fn new() -> Self {
        Self { fragments: HashMap::new() }
    }

    /// Parseia um datagrama UDP completo. Retorna lista de operations extraídas.
    pub fn parse(&mut self, data: &[u8]) -> Vec<ParsedOperation> {
        let mut ops = Vec::new();
        if data.len() < 12 {
            return ops;
        }

        let mut offset = 0;
        // Photon header (12 bytes, big-endian)
        let _peer_id = read_i16_be(data, &mut offset);
        let flags = data[offset]; offset += 1;
        let command_count = data[offset]; offset += 1;
        let _crc = read_i32_be(data, &mut offset);
        let _user_data = read_i32_be(data, &mut offset);

        // flags == 1 = encrypted → skip
        if flags == 0x01 {
            return ops;
        }

        // CRC check se flags == 0xCC
        if flags == 0xCC {
            // O flag 0xCC indica que há 4 bytes extras de CRC32 após o header
            // padrão de 12 bytes. O AAT/AOLL lê e zera esses bytes pra verificar,
            // mas o Albion raramente rejeita pacotes com CRC errado. Apenas
            // pulamos os 4 bytes extras pra não desalinhar o parse dos commands.
            // Sem isto, todos os commands vinham com offset errado = 0 loot.
            let _crc_value = read_i32_be(data, &mut offset);
        }

        for _ in 0..command_count {
            if offset + 12 > data.len() {
                break;
            }
            let cmd_start = offset;
            let command_type = data[offset]; offset += 1;
            offset += 3; // skip 3 bytes
            let command_length = read_i32_be(data, &mut offset) as usize;
            let _seq = read_i32_be(data, &mut offset);

            // command_length inclui o header de 12 bytes. Menor que isso = leitura
            // desalinhada → cmd_end < offset faria o slice entrar em pânico (start>end).
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
                6 => { /* SendReliable */
                    self.parse_message(payload, &mut ops);
                }
                7 => { /* SendUnreliable: skip 4 bytes, then same as SendReliable */
                    if payload.len() > 4 {
                        self.parse_message(&payload[4..], &mut ops);
                    }
                }
                8 => { /* SendFragment */
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
        let message_type = payload[offset]; offset += 1;
        let op_data = &payload[offset..];

        match message_type {
            2 => { /* Request */
                if op_data.is_empty() { return; }
                let mut cursor = 0;
                // ponytail: opcode real é o byte do header (como o AAT). param 253 é
                // uma cópia redundante que nem toda op tem — usamos como preferência
                // mas caímos no header quando ausente (ex: Join não ecoa 253).
                let photon_opcode = op_data[cursor] as i16; cursor += 1;
                let params = deserialize_param_table(op_data, &mut cursor);
                let albion_code = params.get(&253).and_then(|v| v.as_i64()).map(|c| c as i16).unwrap_or(photon_opcode);
                ops.push(ParsedOperation { message_type, albion_code, parameters: params });
            }
            3 => { /* Response */
                if op_data.len() < 3 { return; }
                let mut cursor = 0;
                let photon_opcode = op_data[cursor] as i16; cursor += 1;
                let _return_code = read_i16_le(op_data, &mut cursor);
                // debug message (type-prefixed)
                if cursor < op_data.len() {
                    let _type_code = op_data[cursor]; cursor += 1;
                    // skip string value
                    let _ = deserialize_value(op_data, &mut cursor, _type_code);
                }
                let params = deserialize_param_table(op_data, &mut cursor);
                let albion_code = params.get(&253).and_then(|v| v.as_i64()).map(|c| c as i16).unwrap_or(photon_opcode);
                ops.push(ParsedOperation { message_type, albion_code, parameters: params });
            }
            4 => { /* Event */
                if op_data.is_empty() { return; }
                let mut cursor = 0;
                let photon_code = op_data[cursor] as i16; cursor += 1;
                let params = deserialize_param_table(op_data, &mut cursor);
                let albion_code = params.get(&252).and_then(|v| v.as_i64()).map(|c| c as i16).unwrap_or(photon_code);
                ops.push(ParsedOperation { message_type, albion_code, parameters: params });
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
        let total_length = read_i32_be(payload, &mut offset) as usize;
        let fragment_offset = read_i32_be(payload, &mut offset) as usize;
        let fragment_data = &payload[offset..];

        let entry = self.fragments.entry(start_seq).or_insert_with(|| {
            (vec![0u8; total_length], vec![false; total_length])
        });

        let (buf, received) = entry;
        if fragment_offset + fragment_data.len() <= buf.len() {
            buf[fragment_offset..fragment_offset + fragment_data.len()].copy_from_slice(fragment_data);
            for i in fragment_offset..fragment_offset + fragment_data.len() {
                received[i] = true;
            }
        }

        // Se todos os bytes recebidos, reparse como SendReliable
        if received.iter().all(|&r| r) {
            let complete = std::mem::take(buf);
            self.fragments.remove(&start_seq);
            self.parse_message(&complete, ops);
        }
    }
}

// ─── Protocol18 deserialization ──────────────────────────────────────────────

/// Avança o cursor por uma parameter table sem armazenar os valores.
/// Usado pra pular operations aninhadas (24/25/26) sem desalinhar o cursor.
fn skip_param_table(data: &[u8], cursor: &mut usize) {
    if *cursor >= data.len() { return; }
    let count = data[*cursor]; *cursor += 1;
    for _ in 0..count {
        if *cursor + 2 > data.len() { return; }
        *cursor += 1; // param id
        let tc = data[*cursor]; *cursor += 1;
        let _ = deserialize_value(data, cursor, tc);
    }
}

fn deserialize_param_table(data: &[u8], cursor: &mut usize) -> HashMap<u8, PhotonValue> {
    if *cursor >= data.len() {
        return HashMap::new();
    }
    let count = data[*cursor]; *cursor += 1;
    let mut params = HashMap::with_capacity(count as usize);
    for _ in 0..count {
        if *cursor + 2 > data.len() { break; }
        let key = data[*cursor]; *cursor += 1;
        let type_code = data[*cursor]; *cursor += 1;
        let value = deserialize_value(data, cursor, type_code);
        params.insert(key, value);
    }
    params
}

fn deserialize_value(data: &[u8], cursor: &mut usize, type_code: u8) -> PhotonValue {
    match type_code {
        0 => PhotonValue::Null,
        2 => { // Boolean
            if *cursor >= data.len() { return PhotonValue::Null; }
            let v = data[*cursor] != 0; *cursor += 1;
            PhotonValue::Bool(v)
        }
        3 => { // Byte
            if *cursor >= data.len() { return PhotonValue::Null; }
            let v = data[*cursor]; *cursor += 1;
            PhotonValue::Byte(v)
        }
        4 => { // Short (LE)
            let v = read_i16_le(data, cursor);
            PhotonValue::Short(v)
        }
        5 => { // Float (LE)
            if *cursor + 4 > data.len() { return PhotonValue::Null; }
            let v = f32::from_le_bytes([data[*cursor], data[*cursor+1], data[*cursor+2], data[*cursor+3]]);
            *cursor += 4;
            PhotonValue::Float(v)
        }
        6 => { // Double (LE)
            if *cursor + 8 > data.len() { return PhotonValue::Null; }
            let v = f64::from_le_bytes([
                data[*cursor], data[*cursor+1], data[*cursor+2], data[*cursor+3],
                data[*cursor+4], data[*cursor+5], data[*cursor+6], data[*cursor+7],
            ]);
            *cursor += 8;
            PhotonValue::Double(v)
        }
        7 => { // String
            let len = read_varint_u32(data, cursor) as usize;
            if *cursor + len > data.len() { return PhotonValue::Null; }
            let s = String::from_utf8_lossy(&data[*cursor..*cursor + len]).into_owned();
            *cursor += len;
            PhotonValue::String(s)
        }
        8 => { // Null
            PhotonValue::Null
        }
        9 => { // CompressedInt (varint + ZigZag → i32)
            let raw = read_varint_u32(data, cursor);
            let v = decode_zigzag_32(raw);
            PhotonValue::Int(v)
        }
        10 => { // CompressedLong (varint + ZigZag → i64)
            let raw = read_varint_u64(data, cursor);
            let v = decode_zigzag_64(raw);
            PhotonValue::Long(v)
        }
        11 => { // Int1 (1 byte, positive)
            if *cursor >= data.len() { return PhotonValue::Null; }
            let v = data[*cursor] as i32; *cursor += 1;
            PhotonValue::Int(v)
        }
        12 => { // Int1Negative
            if *cursor >= data.len() { return PhotonValue::Null; }
            let v = -(data[*cursor] as i32); *cursor += 1;
            PhotonValue::Int(v)
        }
        13 => { // Int2 (2 bytes LE, positive)
            if *cursor + 2 > data.len() { return PhotonValue::Null; }
            let v = u16::from_le_bytes([data[*cursor], data[*cursor+1]]) as i32;
            *cursor += 2;
            PhotonValue::Int(v)
        }
        14 => { // Int2Negative
            if *cursor + 2 > data.len() { return PhotonValue::Null; }
            let v = -(u16::from_le_bytes([data[*cursor], data[*cursor+1]]) as i32);
            *cursor += 2;
            PhotonValue::Int(v)
        }
        15 => { // Long1 (1 byte, positive)
            if *cursor >= data.len() { return PhotonValue::Null; }
            let v = data[*cursor] as i64; *cursor += 1;
            PhotonValue::Long(v)
        }
        16 => { // Long1Negative
            if *cursor >= data.len() { return PhotonValue::Null; }
            let v = -(data[*cursor] as i64); *cursor += 1;
            PhotonValue::Long(v)
        }
        17 => { // Long2 (2 bytes LE, positive)
            if *cursor + 2 > data.len() { return PhotonValue::Null; }
            let v = u16::from_le_bytes([data[*cursor], data[*cursor+1]]) as i64;
            *cursor += 2;
            PhotonValue::Long(v)
        }
        18 => { // Long2Negative
            if *cursor + 2 > data.len() { return PhotonValue::Null; }
            let v = -(u16::from_le_bytes([data[*cursor], data[*cursor+1]]) as i64);
            *cursor += 2;
            PhotonValue::Long(v)
        }
        19 => { // Custom
            if *cursor >= data.len() { return PhotonValue::Null; }
            let _type_code = data[*cursor]; *cursor += 1;
            let len = read_varint_u32(data, cursor) as usize;
            if *cursor + len > data.len() { return PhotonValue::Null; }
            let bytes = data[*cursor..*cursor + len].to_vec();
            *cursor += len;
            PhotonValue::Bytes(bytes)
        }
        20 => { // Dictionary
            if *cursor + 2 > data.len() { return PhotonValue::Null; }
            let key_type = data[*cursor]; *cursor += 1;
            let val_type = data[*cursor]; *cursor += 1;
            let size = read_varint_u32(data, cursor) as usize;
            let mut entries = Vec::with_capacity(size.min(data.len()));
            for _ in 0..size {
                let key = if key_type == 0 {
                    if *cursor >= data.len() { break; }
                    let tc = data[*cursor]; *cursor += 1;
                    deserialize_value(data, cursor, tc)
                } else {
                    deserialize_value(data, cursor, key_type)
                };
                let val = if val_type == 0 {
                    if *cursor >= data.len() { break; }
                    let tc = data[*cursor]; *cursor += 1;
                    deserialize_value(data, cursor, tc)
                } else {
                    deserialize_value(data, cursor, val_type)
                };
                entries.push((key, val));
            }
            PhotonValue::Dictionary(entries)
        }
        21 => { // Hashtable
            let size = read_varint_u32(data, cursor) as usize;
            let mut entries = Vec::with_capacity(size.min(data.len()));
            for _ in 0..size {
                if *cursor >= data.len() { break; }
                let key_type = data[*cursor]; *cursor += 1;
                let key = deserialize_value(data, cursor, key_type);
                if *cursor >= data.len() { break; }
                let val_type = data[*cursor]; *cursor += 1;
                let val = deserialize_value(data, cursor, val_type);
                entries.push((key, val));
            }
            PhotonValue::Dictionary(entries)
        }
        23 => { // ObjectArray
            let size = read_varint_u32(data, cursor) as usize;
            let mut arr = Vec::with_capacity(size.min(data.len()));
            for _ in 0..size {
                if *cursor >= data.len() { break; }
                let tc = data[*cursor]; *cursor += 1;
                arr.push(deserialize_value(data, cursor, tc));
            }
            PhotonValue::Array(arr)
        }
        24 => { // OperationRequest (nested) — lê opcode + param table, ignora
            if *cursor >= data.len() { return PhotonValue::Null; }
            let _op = data[*cursor]; *cursor += 1;
            skip_param_table(data, cursor);
            PhotonValue::Null
        }
        25 => { // OperationResponse (nested)
            if *cursor >= data.len() { return PhotonValue::Null; }
            let _op = data[*cursor]; *cursor += 1;
            let _ret = read_i16_le(data, cursor);
            if *cursor >= data.len() { return PhotonValue::Null; }
            let tc = data[*cursor]; *cursor += 1;
            let _ = deserialize_value(data, cursor, tc);
            skip_param_table(data, cursor);
            PhotonValue::Null
        }
        26 => { // EventData (nested)
            if *cursor >= data.len() { return PhotonValue::Null; }
            let _ev = data[*cursor]; *cursor += 1;
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
        64 => { // Array (array of arrays)
            let size = read_varint_u32(data, cursor) as usize;
            let mut arr = Vec::with_capacity(size.min(data.len()));
            for _ in 0..size {
                if *cursor >= data.len() { break; }
                let tc = data[*cursor]; *cursor += 1;
                arr.push(deserialize_value(data, cursor, tc));
            }
            PhotonValue::Array(arr)
        }
        66 => { // BooleanArray — bit-packed (8 bools por byte)
            let size = read_varint_u32(data, cursor) as usize;
            let mut arr = Vec::with_capacity(size.min(data.len()));
            let full = size / 8;
            for _ in 0..full {
                if *cursor >= data.len() { break; }
                let v = data[*cursor]; *cursor += 1;
                for bit in 0..8 {
                    arr.push(PhotonValue::Bool((v >> bit) & 1 != 0));
                }
            }
            let rest = size % 8;
            if rest > 0 && *cursor < data.len() {
                let v = data[*cursor]; *cursor += 1;
                for bit in 0..rest {
                    arr.push(PhotonValue::Bool((v >> bit) & 1 != 0));
                }
            }
            PhotonValue::Array(arr)
        }
        67 => { // ByteArray
            let len = read_varint_u32(data, cursor) as usize;
            if *cursor + len > data.len() { return PhotonValue::Null; }
            let bytes = data[*cursor..*cursor + len].to_vec();
            *cursor += len;
            PhotonValue::Bytes(bytes)
        }
        68 => { // ShortArray
            let size = read_varint_u32(data, cursor) as usize;
            let mut arr = Vec::with_capacity(size.min(data.len()));
            for _ in 0..size {
                arr.push(PhotonValue::Short(read_i16_le(data, cursor)));
            }
            PhotonValue::Array(arr)
        }
        69 => { // FloatArray
            let size = read_varint_u32(data, cursor) as usize;
            let mut arr = Vec::with_capacity(size.min(data.len()));
            for _ in 0..size {
                if *cursor + 4 > data.len() { break; }
                let v = f32::from_le_bytes([data[*cursor], data[*cursor+1], data[*cursor+2], data[*cursor+3]]);
                *cursor += 4;
                arr.push(PhotonValue::Float(v));
            }
            PhotonValue::Array(arr)
        }
        70 => { // DoubleArray
            let size = read_varint_u32(data, cursor) as usize;
            let mut arr = Vec::with_capacity(size.min(data.len()));
            for _ in 0..size {
                if *cursor + 8 > data.len() { break; }
                let v = f64::from_le_bytes([
                    data[*cursor], data[*cursor+1], data[*cursor+2], data[*cursor+3],
                    data[*cursor+4], data[*cursor+5], data[*cursor+6], data[*cursor+7],
                ]);
                *cursor += 8;
                arr.push(PhotonValue::Double(v));
            }
            PhotonValue::Array(arr)
        }
        71 => { // StringArray
            let size = read_varint_u32(data, cursor) as usize;
            let mut arr = Vec::with_capacity(size.min(data.len()));
            for _ in 0..size {
                let len = read_varint_u32(data, cursor) as usize;
                if *cursor + len > data.len() { break; }
                let s = String::from_utf8_lossy(&data[*cursor..*cursor + len]).into_owned();
                *cursor += len;
                arr.push(PhotonValue::String(s));
            }
            PhotonValue::Array(arr)
        }
        73 => { // CompressedIntArray
            let size = read_varint_u32(data, cursor) as usize;
            let mut arr = Vec::with_capacity(size.min(data.len()));
            for _ in 0..size {
                let raw = read_varint_u32(data, cursor);
                arr.push(PhotonValue::Int(decode_zigzag_32(raw)));
            }
            PhotonValue::Array(arr)
        }
        74 => { // CompressedLongArray
            let size = read_varint_u32(data, cursor) as usize;
            let mut arr = Vec::with_capacity(size.min(data.len()));
            for _ in 0..size {
                let raw = read_varint_u64(data, cursor);
                arr.push(PhotonValue::Long(decode_zigzag_64(raw)));
            }
            PhotonValue::Array(arr)
        }
        83 => { // CustomTypeArray
            let size = read_varint_u32(data, cursor) as usize;
            if *cursor >= data.len() { return PhotonValue::Null; }
            let _type_code = data[*cursor]; *cursor += 1;
            let mut arr = Vec::with_capacity(size.min(data.len()));
            for _ in 0..size {
                let len = read_varint_u32(data, cursor) as usize;
                if *cursor + len > data.len() { break; }
                arr.push(PhotonValue::Bytes(data[*cursor..*cursor + len].to_vec()));
                *cursor += len;
            }
            PhotonValue::Array(arr)
        }
        84 => { // DictionaryArray
            let size = read_varint_u32(data, cursor) as usize;
            let mut arr = Vec::with_capacity(size.min(data.len()));
            for _ in 0..size {
                if *cursor >= data.len() { break; }
                let tc = data[*cursor]; *cursor += 1;
                arr.push(deserialize_value(data, cursor, tc));
            }
            PhotonValue::Array(arr)
        }
        85 => { // HashtableArray
            let size = read_varint_u32(data, cursor) as usize;
            let mut arr = Vec::with_capacity(size.min(data.len()));
            for _ in 0..size {
                if *cursor >= data.len() { break; }
                let tc = data[*cursor]; *cursor += 1;
                arr.push(deserialize_value(data, cursor, tc));
            }
            PhotonValue::Array(arr)
        }
        128..=228 => { // CustomTypeSlim
            let _custom_code = type_code - 128;
            let len = read_varint_u32(data, cursor) as usize;
            if *cursor + len > data.len() { return PhotonValue::Null; }
            let bytes = data[*cursor..*cursor + len].to_vec();
            *cursor += len;
            PhotonValue::Bytes(bytes)
        }
        _ => {
            // Tipo desconhecido — skip não seguro, mas melhor que crash
            PhotonValue::Null
        }
    }
}

// ─── helpers ─────────────────────────────────────────────────────────────────

fn read_i16_be(data: &[u8], cursor: &mut usize) -> i16 {
    if *cursor + 2 > data.len() { *cursor = data.len(); return 0; }
    let v = i16::from_be_bytes([data[*cursor], data[*cursor + 1]]);
    *cursor += 2;
    v
}

fn read_i32_be(data: &[u8], cursor: &mut usize) -> i32 {
    if *cursor + 4 > data.len() { *cursor = data.len(); return 0; }
    let v = i32::from_be_bytes([data[*cursor], data[*cursor + 1], data[*cursor + 2], data[*cursor + 3]]);
    *cursor += 4;
    v
}

fn read_i16_le(data: &[u8], cursor: &mut usize) -> i16 {
    if *cursor + 2 > data.len() { *cursor = data.len(); return 0; }
    let v = i16::from_le_bytes([data[*cursor], data[*cursor + 1]]);
    *cursor += 2;
    v
}

fn read_varint_u32(data: &[u8], cursor: &mut usize) -> u32 {
    let mut value = 0u32;
    let mut shift = 0;
    while shift != 35 {
        if *cursor >= data.len() { return value; }
        let current = data[*cursor]; *cursor += 1;
        value |= ((current & 0x7F) as u32) << shift;
        shift += 7;
        if current & 0x80 == 0 { return value; }
    }
    value
}

fn read_varint_u64(data: &[u8], cursor: &mut usize) -> u64 {
    let mut value = 0u64;
    let mut shift = 0;
    while shift != 70 {
        if *cursor >= data.len() { return value; }
        let current = data[*cursor]; *cursor += 1;
        value |= ((current & 0x7F) as u64) << shift;
        shift += 7;
        if current & 0x80 == 0 { return value; }
    }
    value
}

fn decode_zigzag_32(value: u32) -> i32 {
    ((value >> 1) as i32) ^ -((value & 1) as i32)
}

fn decode_zigzag_64(value: u64) -> i64 {
    ((value >> 1) as i64) ^ -((value & 1) as i64)
}

// ─── Extração de dados do Albion ─────────────────────────────────────────────

/// Dados do jogador local extraídos do Join (opcode 2) ou ChangeCluster (opcode 41).
#[derive(Clone, Debug, Default, serde::Serialize)]
pub struct PlayerState {
    pub player_name: String,
    pub guild_name: String,
    pub alliance_name: String,
    pub map_index: String,
    pub previous_map: String,
    /// entityId do jogador LOCAL (Join param 0). Usado pra resolver o próprio
    /// dano no damage meter, já que o jogador não aparece nos eventos NewCharacter.
    pub local_object_id: Option<i64>,
}

/// Extrai dados do jogador de uma operação parsed.
///
/// O nome do jogador local vem da resposta de Join (como no AAT): param 2 = nome,
/// 8 = mapa, 58 = guild, 79 = aliança. O OPCODE do Join muda a cada patch do jogo,
/// então detectamos pela ESTRUTURA (resposta com string em 2 e 8) em vez de fixar
/// um número — assim sobrevive a updates sem manutenção.
pub fn extract_player_state(op: &ParsedOperation) -> Option<PlayerState> {
    // Join detectado por estrutura: Response (type 3) com nome@2 + mapa@8.
    if op.message_type == 3 {
        if let (Some(PhotonValue::String(name)), Some(PhotonValue::String(map))) =
            (op.parameters.get(&2), op.parameters.get(&8))
        {
            if !name.is_empty() && !map.is_empty() {
                let mut state = PlayerState::default();
                state.player_name = name.clone();
                state.local_object_id = op.parameters.get(&0).and_then(|v| v.as_i64());
                state.map_index = map.split('@').next().unwrap_or(map).to_string();
                if let Some(PhotonValue::String(s)) = op.parameters.get(&58) { state.guild_name = s.clone(); }
                if let Some(PhotonValue::String(s)) = op.parameters.get(&79) { state.alliance_name = s.clone(); }
                if let Some(PhotonValue::String(s)) = op.parameters.get(&65) { state.previous_map = s.clone(); }
                return Some(state);
            }
        }
    }
    match op.albion_code {
        41 => { // ChangeCluster response
            let mut state = PlayerState::default();
            if let Some(PhotonValue::String(s)) = op.parameters.get(&0) {
                // Hideout tem formato "name@maincluster@..."
                state.map_index = s.split('@').next().unwrap_or(s).to_string();
            }
            Some(state)
        }
        _ => None,
    }
}

/// Membros da party extraídos do PartyJoined (event 231).
pub fn extract_party(op: &ParsedOperation) -> Option<Vec<String>> {
    if op.albion_code != 231 { return None; }
    let mut names = Vec::new();
    if let Some(PhotonValue::Array(arr)) = op.parameters.get(&9) {
        for v in arr {
            if let PhotonValue::String(s) = v {
                if !s.is_empty() { names.push(s.clone()); }
            }
        }
    }
    Some(names)
}

/// Loot capturado do evento OtherGrabbedLoot (opcode 256).
// ponytail: os campos vêm como parâmetros do Photon — 1=looted_from, 2=looter,
// 3=is_silver, 4=item_index, 5=quantity. Não há timestamp no pacote; usamos o
// relógio local no momento da captura.
#[derive(Clone, Debug, Default, serde::Serialize, serde::Deserialize)]
pub struct LootEvent {
    pub ts: String,              // ISO 8601 UTC (relógio local)
    pub looted_by: String,       // param 2 — quem pegou
    pub looted_from: String,     // param 1 — de onde (corpo/mob/baú)
    pub item_index: i32,         // param 4 — ID numérico do item no Albion
    pub quantity: i32,           // param 5
    pub is_silver: bool,         // param 3 — true = prata, não item
}

/// Nome de jogador do Albion: 3-20 chars, só letras/dígitos, começa com letra.
/// Entidades do jogo (GUILDBANNER_ELEPHANT, SCHEMA_01, MOB_...) têm '_' ou
/// dígito na frente — é assim que filtramos mecânicas que disparam o mesmo
/// evento de loot mas não são jogadores.
pub fn is_player_name(name: &str) -> bool {
    (3..=20).contains(&name.len())
        && name.chars().next().map_or(false, |c| c.is_ascii_alphabetic())
        && name.chars().all(|c| c.is_ascii_alphanumeric())
}

/// Extrai um LootEvent do OtherGrabbedLoot. Ignora silver.
///
/// O event code muda entre patches (AAT comenta 252:256, mas nossos códigos já
/// deslocaram — ex: TakeSilver 55→62), então detectamos pela ESTRUTURA em vez de
/// fixar um número: evento com corpo@1 (string) + looter@2 (string) + itemIndex@4
/// (int≠0) + qty@5 (int≠0). Combinação distintiva o bastante pra não falsear.
pub fn extract_loot(op: &ParsedOperation) -> Option<LootEvent> {
    if op.message_type != 4 { return None; } // só eventos
    let looted_from = op.parameters.get(&1).and_then(|v| v.as_string())?.to_string();
    let looted_by = op.parameters.get(&2).and_then(|v| v.as_string())?.to_string();
    let item_index = op.parameters.get(&4).and_then(|v| v.as_i64()).unwrap_or(0) as i32;
    let quantity = op.parameters.get(&5).and_then(|v| v.as_i64()).unwrap_or(0) as i32;
    let is_silver = op.parameters.get(&3).and_then(|v| {
        if let PhotonValue::Bool(b) = v { Some(*b) } else { None }
    }).unwrap_or(false);
    // Precisa de looter + item + qty. Silver (bool@3) e itens vazios ficam de fora.
    if looted_by.is_empty() || is_silver || item_index == 0 || quantity == 0 {
        return None;
    }
    // Só jogadores: mecânicas do jogo (GUILDBANNER_ELEPHANT pegando 183M de
    // itens de SCHEMA_01) disparam a mesma estrutura. Stack máximo do jogo é
    // 999 — quantidade acima disso nunca é loot de jogador.
    if !is_player_name(&looted_by) || !(1..=999).contains(&quantity) {
        return None;
    }
    let ts = now_iso_utc();
    Some(LootEvent { ts, looted_by, looted_from, item_index, quantity, is_silver: false })
}

/// Loot do PRÓPRIO jogador — nunca vem pelo OtherGrabbedLoot (o nome já diz:
/// é broadcast pra QUEM VÊ o saque, o servidor não ecoa de volta pra quem
/// lootou). Confirmado contra o ao-loot-logger (madvac/ao-loot-logger): a
/// única forma de detectar é acompanhar o PEDIDO que o próprio cliente manda
/// ao arrastar um item do loot bag pra mochila (OpInventoryMoveItem) e
/// resolver o item/dono a partir de eventos anteriores que registraram o
/// conteúdo daquele loot bag. Exige estado entre pacotes — por isso os mapas
/// vivem no Sniffer (como `entities`), e as funções abaixo só extraem, uma
/// operação por vez, o pedaço que cada evento contribui.
///
/// Sequência observada pelo cliente ao abrir um corpo/mob morto:
///   EvNewLoot(98)            → container_id → dono (nome do corpo/mob)
///   EvNewSimpleItem(32) /
///   EvNewEquipmentItem(30, EVENT) /
///   EvNewSiegeBannerItem(31) → object_id → (item_index, quantity)
///   EvAttachItemContainer(99)→ liga container_id ↔ uuid, e lista object_id
///                              por slot (é o uuid que o request usa, não o id)
///   EvDetachItemContainer(100)→ container fechado, uuid morre
/// Loot em si: OpInventoryMoveItem(30, REQUEST) do slot X do container A pro
/// container B — quando A≠B, slot X saiu do loot bag pra outro lugar (a
/// mochila do jogador). Resolve object_id → item/qty e container_id → dono.

/// EvNewLoot (opcode 98, event): container_id → dono do corpo/mob.
pub fn extract_new_loot_owner(op: &ParsedOperation) -> Option<(i64, String)> {
    if op.message_type != 4 || op.albion_code != 98 { return None; }
    let id = op.parameters.get(&0)?.as_i64()?;
    let owner = op.parameters.get(&3).and_then(|v| v.as_string())?.to_string();
    Some((id, owner))
}

/// EvNewSimpleItem(32) / EvNewEquipmentItem(30 EVENT) / EvNewSiegeBannerItem(31):
/// mesmo layout nos 3 — objectId@0, itemNumId@1, quantity@2. O 30 aqui é EVENT;
/// não confundir com o opcode 30 do OpInventoryMoveItem, que é REQUEST — os
/// dois namespaces (message_type) não colidem.
pub fn extract_new_loot_item(op: &ParsedOperation) -> Option<(i64, i32, i32)> {
    if op.message_type != 4 || !matches!(op.albion_code, 30 | 31 | 32) { return None; }
    let object_id = op.parameters.get(&0)?.as_i64()?;
    let item_index = op.parameters.get(&1)?.as_i64()? as i32;
    let quantity = op.parameters.get(&2)?.as_i64()? as i32;
    Some((object_id, item_index, quantity))
}

/// EvAttachItemContainer (opcode 99, event): container_id, uuid (16 bytes,
/// usado pelo request de mover item) e a lista de object_id por slot (índice
/// do vetor = slot; 0 = slot vazio).
pub fn extract_attach_container(op: &ParsedOperation) -> Option<(i64, [u8; 16], Vec<i64>)> {
    if op.message_type != 4 || op.albion_code != 99 { return None; }
    let id = op.parameters.get(&0)?.as_i64()?;
    let uuid = as_uuid16(op.parameters.get(&1)?)?;
    let inventory = op.parameters.get(&3)?.as_array()?;
    let slots = inventory.iter().map(|v| v.as_i64().unwrap_or(0)).collect();
    Some((id, uuid, slots))
}

/// EvDetachItemContainer (opcode 100, event): container fechado/despawnado.
pub fn extract_detach_container(op: &ParsedOperation) -> Option<[u8; 16]> {
    if op.message_type != 4 || op.albion_code != 100 { return None; }
    as_uuid16(op.parameters.get(&0)?)
}

/// Pedido do cliente movendo um item entre containers (OpInventoryMoveItem,
/// opcode 30, REQUEST). `from_uuid == to_uuid` é só reorganizar dentro do
/// mesmo container (abrir e reordenar o loot bag) — não é loot, por isso
/// devolve None nesse caso.
pub struct InventoryMove {
    pub from_slot: i32,
    pub from_uuid: [u8; 16],
    pub to_uuid: [u8; 16],
}
pub fn extract_inventory_move(op: &ParsedOperation) -> Option<InventoryMove> {
    if op.message_type != 2 || op.albion_code != 30 { return None; }
    let from_slot = op.parameters.get(&0).and_then(|v| v.as_i64()).unwrap_or(0) as i32;
    let from_uuid = as_uuid16(op.parameters.get(&1)?)?;
    let to_uuid = as_uuid16(op.parameters.get(&4)?)?;
    if from_uuid == to_uuid { return None; }
    Some(InventoryMove { from_slot, from_uuid, to_uuid })
}

/// uuid vem como array Photon de 16 elementos — pode decodificar como Bytes
/// (blob cru) ou Array (de valores 0-255), dependendo do tipo que o encoder
/// do protocolo escolheu pro campo; aceita os dois.
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

/// Monta o LootEvent de um self-loot já resolvido (dono do corpo + item +
/// qtd) — quem lootou é sempre o jogador local, dado que só chegamos aqui a
/// partir do PRÓPRIO request de mover item.
pub fn self_loot_event(looted_by: String, looted_from: String, item_index: i32, quantity: i32) -> LootEvent {
    LootEvent { ts: now_iso_utc(), looted_by, looted_from, item_index, quantity, is_silver: false }
}

/// Registro de personagem: NewCharacter (event 29) mapeia entityId → nome.
// ponytail: os eventos de combate referenciam o jogador por ID numérico, não
// por nome, então precisamos deste mapa. Param 0 = id, param 1 = nome (AAT).
pub fn extract_new_character(op: &ParsedOperation) -> Option<(i64, String)> {
    if op.albion_code != 29 { return None; }
    let id = op.parameters.get(&0).and_then(|v| v.as_i64())?;
    let name = op.parameters.get(&1).and_then(|v| v.as_string())?.to_string();
    if name.is_empty() { return None; }
    Some((id, name))
}

// ponytail: HealthUpdate = event 6. causer=param 6, target=param 0, change=param
// 2 (negativo = dano, positivo = cura), spell=param 7 (índice do feitiço causador).
// Índices vêm do AAT e PODEM mudar a cada patch do jogo — o sniffer loga os
// params do 1º HealthUpdate da sessão pra recalibrar contra o tráfego real.
const HP_TARGET: u8 = 0;
const HP_CHANGE: u8 = 2;
const HP_CAUSER: u8 = 6;
const HP_SPELL: u8 = 7;

/// Evento de mudança de vida — base do damage meter.
#[derive(Clone, Debug, Default, serde::Serialize)]
pub struct HealthEvent {
    pub causer_id: i64,
    pub target_id: i64,
    pub change: f64, // <0 dano infligido, >0 cura
    pub spell_id: i32, // índice do feitiço causador; -1 = desconhecido/auto attack
}

pub fn extract_health(op: &ParsedOperation) -> Option<HealthEvent> {
    if op.albion_code != 6 { return None; }
    let target_id = op.parameters.get(&HP_TARGET).and_then(|v| v.as_i64()).unwrap_or(-1);
    let causer_id = op.parameters.get(&HP_CAUSER).and_then(|v| v.as_i64()).unwrap_or(-1);
    let spell_id = op.parameters.get(&HP_SPELL).and_then(|v| v.as_i64()).unwrap_or(-1) as i32;
    let change = op.parameters.get(&HP_CHANGE).and_then(|v| match v {
        PhotonValue::Float(f) => Some(*f as f64),
        PhotonValue::Double(d) => Some(*d),
        other => other.as_i64().map(|i| i as f64),
    })?;
    if causer_id < 0 { return None; }
    Some(HealthEvent { causer_id, target_id, change, spell_id })
}

/// Janela da timeline por jogador (Details do WoW mostra os últimos minutos;
/// 3 min cobre uma luta de ZvZ inteira sem virar histórico infinito).
pub const TIMELINE_SECS: u64 = 180;

/// Acumulador por skill.
///
/// `hits` conta GOLPES (eventos HealthUpdate), não casts: um DoT de 5 ticks
/// entra como 5. É o mesmo que o Details chama de "Hits" — contar cast de
/// verdade exigiria ler o evento de conjuração, que não escutamos hoje.
#[derive(Clone, Debug, Default, serde::Serialize)]
pub struct SpellAcc {
    pub hits: u64,
    pub total: f64,
    /// Maior golpe único desta skill — o "crit" que o pessoal compara.
    pub max_hit: f64,
}

/// Acumulador de dano por jogador (chave = causer_id).
///
/// Só DANO. Cura é ignorada de propósito: o diferencial que queremos é a
/// listagem de dano ser completa, não competir com o painel de cura do AAT.
#[derive(Clone, Debug, Default, serde::Serialize)]
pub struct DamageAcc {
    pub damage: f64,
    /// spell_id → acumulado daquela skill.
    pub spells: HashMap<i32, SpellAcc>,
    /// Dano por segundo (epoch_sec, dano) dos últimos TIMELINE_SECS.
    /// Janela deslizante: buckets velhos caem fora em `record`.
    pub timeline: VecDeque<(u64, f64)>,
    /// Primeiro e último golpe (epoch secs) — base do DPS por tempo ATIVO,
    /// que é o número justo pra quem entrou na luta no meio.
    pub first_hit: Option<u64>,
    pub last_hit: Option<u64>,
}

impl DamageAcc {
    /// Contabiliza um golpe. `now` = epoch em segundos.
    pub fn record(&mut self, spell_id: i32, amount: f64, now: u64) {
        self.damage += amount;
        let sp = self.spells.entry(spell_id).or_default();
        sp.hits += 1;
        sp.total += amount;
        if amount > sp.max_hit { sp.max_hit = amount; }

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

    /// DPS sobre o tempo ativo (primeiro→último golpe), mínimo 1s.
    /// Junta outro acumulador neste.
    ///
    /// Existe porque um jogador tem VÁRIOS entity ids numa sessão: o jogo dá id
    /// novo quando ele sai e volta do teu alcance de visão. Sem juntar, a mesma
    /// pessoa aparecia como várias linhas, cada uma com um pedaço do dano.
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
        // Mesmo segundo soma no mesmo bucket, senão o gráfico desenharia dois
        // pontos no mesmo x. Busca linear serve: a janela tem 180 entradas.
        for (sec, d) in &other.timeline {
            match self.timeline.iter_mut().find(|(s, _)| s == sec) {
                Some((_, acc)) => *acc += d,
                None => self.timeline.push_back((*sec, *d)),
            }
        }
        self.timeline.make_contiguous().sort_by_key(|(s, _)| *s);
        // O DPS usa tempo ativo: primeiro golpe de qualquer id até o último.
        self.first_hit = [self.first_hit, other.first_hit].into_iter().flatten().min();
        self.last_hit = [self.last_hit, other.last_hit].into_iter().flatten().max();
    }

    pub fn dps(&self) -> f64 {
        match (self.first_hit, self.last_hit) {
            (Some(a), Some(b)) => self.damage / (b.saturating_sub(a).max(1) as f64),
            _ => 0.0,
        }
    }
}

/// Oferta de venda simplificada — o que gravamos no NOSSO banco de preços.
#[derive(Clone, Debug, serde::Serialize)]
pub struct MarketOffer {
    pub item_id: String,
    pub quality: i32,
    pub unit_price_silver: i64,
}

/// Resultado da leitura de uma resposta de marketplace: ofertas simplificadas
/// (pro nosso feed de preço) + as ordens cruas (pra devolver ao AODP verbatim).
#[derive(Clone, Debug, Default)]
pub struct MarketCapture {
    pub offers: Vec<MarketOffer>,
    /// Ordens cruas (JSON do jogo) — TODAS as sell orders, sem transformar,
    /// pro upload AODP. Preço aqui fica em silver*10000 (formato original).
    pub raw_orders: Vec<serde_json::Value>,
}

/// Lê uma resposta de marketplace (AuctionGetOffers = venda, AuctionGetRequests
/// = compra).
///
/// Detectada por ESTRUTURA (não por opcode): array de strings JSON, cada uma
/// com "UnitPriceSilver" + "ItemTypeId". Preço vem ×10000 no protocolo.
/// Pro AODP encaminhamos AMBAS (offer + request), como o client oficial faz —
/// os dois vão pro mesmo tópico marketorders.ingest. Pro NOSSO banco só as
/// sell orders ("offer") viram preço.
pub fn extract_market(op: &ParsedOperation) -> MarketCapture {
    let mut cap = MarketCapture::default();
    if op.message_type != 3 { return cap; }
    for v in op.parameters.values() {
        let PhotonValue::Array(arr) = v else { continue };
        for item in arr {
            let PhotonValue::String(s) = item else { break };
            if !s.starts_with('{') || !s.contains("UnitPriceSilver") { break; }
            let Ok(j) = serde_json::from_str::<serde_json::Value>(s) else { continue };
            let atype = j.get("AuctionType").and_then(|a| a.as_str()).unwrap_or("");
            if atype != "offer" && atype != "request" { continue; }
            let Some(base_id) = j.get("ItemTypeId").and_then(|x| x.as_str()).map(String::from) else { continue };
            let raw_price = j.get("UnitPriceSilver").and_then(|x| x.as_i64()).unwrap_or(0);
            if raw_price <= 0 { continue; }
            // Ordem crua pro AODP (verbatim): venda E compra.
            cap.raw_orders.push(j.clone());
            // Nosso banco só guarda venda (id com @enchant, preco/10000).
            if atype == "offer" {
                let ench = j.get("EnchantmentLevel").and_then(|x| x.as_i64()).unwrap_or(0);
                let item_id = if ench > 0 && !base_id.contains('@') {
                    format!("{}@{}", base_id, ench)
                } else { base_id };
                let quality = j.get("QualityLevel").and_then(|x| x.as_i64()).unwrap_or(1) as i32;
                cap.offers.push(MarketOffer { item_id, quality, unit_price_silver: raw_price / 10_000 });
            }
        }
    }
    cap
}

/// Info do request de market history (AuctionGetItemAverageStats), guardada
/// pra correlacionar com a response pelo message-id (param 255).
#[derive(Clone, Debug)]
pub struct HistoryReq {
    pub albion_id: i32,
    pub quality: i32,
    pub timescale: i32,
}

/// Um bucket do gráfico de histórico do jogo.
#[derive(Clone, Debug, serde::Serialize)]
pub struct HistoryBucket {
    pub bucket_ts: i64,
    pub item_count: i64,
    pub silver_amount: i64,
}

/// Detecta o REQUEST de market history e devolve (message_id, info).
/// Estrutura: request (type 2) com item id@1, quality@2, timescale@3 (0..=2),
/// e message id@255. O timescale restrito a 0..2 torna a assinatura distintiva.
/// Aplica o ajuste de item id negativo do protocolo (128-256 chegam negativos).
pub fn extract_history_request(op: &ParsedOperation) -> Option<(u64, HistoryReq)> {
    if op.message_type != 2 { return None; }
    let msg_id = op.parameters.get(&255)?.as_i64()? as u64;
    let mut albion_id = op.parameters.get(&1)?.as_i64()? as i32;
    let quality = op.parameters.get(&2).and_then(|v| v.as_i64()).unwrap_or(1) as i32;
    let timescale = op.parameters.get(&3)?.as_i64()? as i32;
    if !(0..=2).contains(&timescale) { return None; }
    if !(1..=5).contains(&quality) { return None; }
    // Quirk do protocolo: ids 128-256 vêm como negativo (byte com sinal).
    if albion_id < 0 && albion_id > -129 { albion_id += 256; }
    if albion_id < 1 { return None; }
    Some((msg_id, HistoryReq { albion_id, quality, timescale }))
}

/// Detecta a RESPONSE de market history: (message_id, buckets).
/// Estrutura: response (type 3) com 3 arrays paralelos — item_count@0,
/// silver@1, timestamp@2 — e message id@255. Aplica o ajuste de quantidade
/// negativa (mesmo quirk do AODP: -124..-1 → +256, < -124 descarta).
pub fn extract_history_response(op: &ParsedOperation) -> Option<(u64, Vec<HistoryBucket>)> {
    if op.message_type != 3 { return None; }
    let msg_id = op.parameters.get(&255)?.as_i64()? as u64;
    let counts = op.parameters.get(&0)?.as_array()?;
    let silvers = op.parameters.get(&1)?.as_array()?;
    let stamps = op.parameters.get(&2)?.as_array()?;
    let n = counts.len();
    if n == 0 || silvers.len() != n || stamps.len() != n { return None; }
    let mut buckets = Vec::with_capacity(n);
    for i in 0..n {
        let mut count = counts[i].as_i64()?;
        let silver = silvers[i].as_i64()?;
        let ts = stamps[i].as_i64()?;
        if count < 0 {
            if count < -124 { continue; } // sem interpretação conhecida — descarta
            count += 256;
        }
        if count <= 0 || ts <= 0 { continue; }
        buckets.push(HistoryBucket { bucket_ts: ts, item_count: count, silver_amount: silver });
    }
    if buckets.is_empty() { return None; }
    Some((msg_id, buckets))
}

/// Preços do mercado de ouro (GoldMarketGetAverageInfo response).
/// Global (sem localização) — o AODP só precisa da região do servidor.
#[derive(Clone, Debug, Default)]
pub struct GoldPrices {
    pub prices: Vec<i64>,
    pub timestamps: Vec<i64>,
}

/// Detecta a resposta de gold por ESTRUTURA: dois arrays paralelos — param 0 =
/// preços de ouro (faixa sã), param 1 = timestamps unix. A heurística de
/// timestamp (>2001) distingue de qualquer outra resposta com dois arrays.
pub fn extract_gold(op: &ParsedOperation) -> Option<GoldPrices> {
    if op.message_type != 3 { return None; }
    let prices_arr = op.parameters.get(&0)?.as_array()?;
    let ts_arr = op.parameters.get(&1)?.as_array()?;
    if prices_arr.is_empty() || prices_arr.len() != ts_arr.len() { return None; }
    let prices: Vec<i64> = prices_arr.iter().filter_map(|v| v.as_i64()).collect();
    let timestamps: Vec<i64> = ts_arr.iter().filter_map(|v| v.as_i64()).collect();
    if prices.len() != prices_arr.len() || timestamps.len() != ts_arr.len() { return None; }
    if !timestamps.iter().all(|&t| t > 1_000_000_000) { return None; } // unix pós-2001
    if !prices.iter().all(|&p| (1..=1_000_000).contains(&p)) { return None; } // ouro em faixa sã
    Some(GoldPrices { prices, timestamps })
}

pub fn now_iso_utc() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs();
    // ponytail: ISO 8601 sem dependência extra — data aproximada a partir de epoch.
    // Precisão de segundo é suficiente pra lootlog (o lootlogger também usa segundos).
    let (y, mo, d, h, mi, s) = epoch_to_ymd_hms(secs);
    format!("{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z", y, mo, d, h, mi, s)
}

/// Converte segundos depuis epoch pra (ano, mês, dia, hora, min, seg) UTC.
// ponytail: algoritmo de conversão civil sem depender do chrono — dias desde
// 1970-01-01, depois decompose em Y/M/D via regra de ano bissexto simples.
fn epoch_to_ymd_hms(secs: u64) -> (i32, u32, u32, u32, u32, u32) {
    let days = (secs / 86400) as i64;
    let rem = secs % 86400;
    let h = (rem / 3600) as u32;
    let mi = ((rem % 3600) / 60) as u32;
    let s = (rem % 60) as u32;
    // Days since 1970-01-01 → civil date (Howard Hinnant's algorithm)
    let z = days + 719468;
    let era = if z >= 0 { z / 146097 } else { (z - 146096) / 146097 };
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

    /// O bug real: o mesmo jogador ganha entity id novo ao reentrar na tua
    /// visão, virava várias linhas com o dano picado, e como o React usa o
    /// nome como `key` as chaves colidiam e a lista duplicava.
    #[test]
    fn test_merge_junta_ids_do_mesmo_jogador() {
        let mut a = DamageAcc::default();
        a.record(10, 100.0, 1000);
        a.record(10, 50.0, 1000);   // mesmo segundo, mesma skill
        a.record(20, 30.0, 1001);

        let mut b = DamageAcc::default();
        b.record(10, 200.0, 1001);  // skill repetida, segundo repetido
        b.record(30, 7.0, 1005);

        a.merge(&b);

        assert_eq!(a.damage as i64, 387);
        assert_eq!(a.spells[&10].hits, 3, "golpes das duas somam");
        assert_eq!(a.spells[&10].total as i64, 350);
        assert_eq!(a.spells[&10].max_hit as i64, 200, "maior golpe é o do outro id");
        assert_eq!(a.spells[&30].hits, 1, "skill que só o outro tinha entra");

        // Mesmo segundo tem que virar UM bucket, senão o gráfico desenha dois
        // pontos no mesmo x.
        let secs: Vec<u64> = a.timeline.iter().map(|(s, _)| *s).collect();
        assert_eq!(secs, vec![1000, 1001, 1005], "ordenado e sem segundo repetido");
        assert_eq!(a.timeline.iter().find(|(s, _)| *s == 1001).unwrap().1 as i64, 230);

        assert_eq!(a.first_hit, Some(1000));
        assert_eq!(a.last_hit, Some(1005), "DPS usa do 1º golpe de qualquer id ao último");
    }

    #[test]
    fn test_merge_com_acumulador_vazio_nao_inventa_tempo() {
        let mut vazio = DamageAcc::default();
        let mut cheio = DamageAcc::default();
        cheio.record(1, 10.0, 500);

        vazio.merge(&cheio);
        assert_eq!(vazio.first_hit, Some(500));

        let mut outro = DamageAcc::default();
        outro.record(1, 10.0, 500);
        outro.merge(&DamageAcc::default());
        assert_eq!(outro.first_hit, Some(500), "vazio não pode zerar o first_hit");
        assert_eq!(outro.damage as i64, 10);
    }

    #[test]
    fn test_damage_acc_agrega_por_skill() {
        let mut acc = DamageAcc::default();
        acc.record(10, 100.0, 1000);
        acc.record(10, 300.0, 1000);
        acc.record(20, 50.0, 1000);
        assert_eq!(acc.damage, 450.0);
        let s10 = &acc.spells[&10];
        assert_eq!(s10.hits, 2);
        assert_eq!(s10.total, 400.0);
        assert_eq!(s10.max_hit, 300.0, "maior golpe da skill");
    }

    #[test]
    fn test_timeline_agrupa_o_mesmo_segundo() {
        let mut acc = DamageAcc::default();
        acc.record(1, 10.0, 500);
        acc.record(1, 15.0, 500); // mesmo segundo → soma no bucket
        acc.record(1, 7.0, 501);
        assert_eq!(acc.timeline.len(), 2);
        assert_eq!(acc.timeline[0], (500, 25.0));
        assert_eq!(acc.timeline[1], (501, 7.0));
    }

    #[test]
    fn test_timeline_descarta_fora_da_janela() {
        let mut acc = DamageAcc::default();
        acc.record(1, 10.0, 1000);
        // TIMELINE_SECS depois, o bucket antigo tem que ter saído.
        acc.record(1, 10.0, 1000 + TIMELINE_SECS + 1);
        assert_eq!(acc.timeline.len(), 1);
        assert_eq!(acc.timeline[0].0, 1000 + TIMELINE_SECS + 1);
        // Mas o total da sessão NÃO é afetado pela janela.
        assert_eq!(acc.damage, 20.0);
    }

    #[test]
    fn test_dps_usa_tempo_ativo() {
        let mut acc = DamageAcc::default();
        acc.record(1, 1000.0, 100);
        acc.record(1, 1000.0, 110); // 2000 de dano em 10s ativos
        assert_eq!(acc.dps(), 200.0);
    }

    #[test]
    fn test_dps_de_um_golpe_so_nao_divide_por_zero() {
        let mut acc = DamageAcc::default();
        acc.record(1, 500.0, 100);
        assert_eq!(acc.dps(), 500.0, "janela mínima de 1s");
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

    #[test]
    fn test_empty_packet() {
        let mut parser = PhotonParser::new();
        let ops = parser.parse(&[0u8; 5]);
        assert!(ops.is_empty());
    }

    #[test]
    fn test_short_command_length_no_panic() {
        // Header Photon válido (flags=0, command_count=1) + comando com
        // command_length=5 (< 12). Antes do guard isso causava pânico
        // (underflow em length-12 e slice start>end). Deve só retornar vazio.
        let mut data = vec![0u8; 24];
        data[3] = 1; // command_count = 1
        data[12] = 6; // command_type = SendReliable
        data[19] = 5; // command_length (BE) = 5, < 12
        let mut parser = PhotonParser::new();
        let ops = parser.parse(&data); // não deve entrar em pânico
        assert!(ops.is_empty());
    }

    #[test]
    fn test_is_player_name() {
        assert!(is_player_name("Slayner"));
        assert!(is_player_name("Player123"));
        assert!(!is_player_name("GUILDBANNER_ELEPHANT")); // underscore
        assert!(!is_player_name("SCHEMA_01"));
        assert!(!is_player_name("1abc")); // começa com dígito
        assert!(!is_player_name("ab")); // curto demais
    }

    #[test]
    fn test_extract_loot_filters_game_entities() {
        let mut params = HashMap::new();
        params.insert(1u8, PhotonValue::String("SCHEMA_01".into()));
        params.insert(2u8, PhotonValue::String("GUILDBANNER_ELEPHANT".into()));
        params.insert(4u8, PhotonValue::Int(1234));
        params.insert(5u8, PhotonValue::Int(183758138));
        let op = ParsedOperation { message_type: 4, albion_code: 256, parameters: params };
        assert!(extract_loot(&op).is_none());

        let mut params = HashMap::new();
        params.insert(1u8, PhotonValue::String("DeadGuy".into()));
        params.insert(2u8, PhotonValue::String("Looter".into()));
        params.insert(4u8, PhotonValue::Int(1234));
        params.insert(5u8, PhotonValue::Int(3));
        let op = ParsedOperation { message_type: 4, albion_code: 256, parameters: params };
        assert!(extract_loot(&op).is_some());
    }

    #[test]
    fn test_self_loot_chain() {
        let uuid: [u8; 16] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16];
        let other_uuid: [u8; 16] = [9; 16];

        // EvNewLoot(98): container 500 pertence ao mob "MOB_DIREWOLF".
        let mut p = HashMap::new();
        p.insert(0u8, PhotonValue::Int(500));
        p.insert(3u8, PhotonValue::String("MOB_DIREWOLF".into()));
        let op = ParsedOperation { message_type: 4, albion_code: 98, parameters: p };
        let (id, owner) = extract_new_loot_owner(&op).expect("EvNewLoot");
        assert_eq!((id, owner.as_str()), (500, "MOB_DIREWOLF"));

        // EvNewSimpleItem(32): object 7001 = item 1234, qty 3.
        let mut p = HashMap::new();
        p.insert(0u8, PhotonValue::Int(7001));
        p.insert(1u8, PhotonValue::Int(1234));
        p.insert(2u8, PhotonValue::Int(3));
        let op = ParsedOperation { message_type: 4, albion_code: 32, parameters: p };
        let (object_id, item_index, quantity) = extract_new_loot_item(&op).expect("EvNewSimpleItem");
        assert_eq!((object_id, item_index, quantity), (7001, 1234, 3));

        // EvNewEquipmentItem(30, EVENT) não deve ser confundido com o
        // OpInventoryMoveItem(30, REQUEST) abaixo — mesmo número, message_type diferente.
        let mut p = HashMap::new();
        p.insert(0u8, PhotonValue::Bytes(uuid.to_vec()));
        let op = ParsedOperation { message_type: 2, albion_code: 30, parameters: p };
        assert!(extract_new_loot_item(&op).is_none());

        // EvAttachItemContainer(99): container 500, uuid, slot 2 = object 7001.
        let mut p = HashMap::new();
        p.insert(0u8, PhotonValue::Int(500));
        p.insert(1u8, PhotonValue::Bytes(uuid.to_vec()));
        p.insert(3u8, PhotonValue::Array(vec![
            PhotonValue::Int(0), PhotonValue::Int(0), PhotonValue::Int(7001),
        ]));
        let op = ParsedOperation { message_type: 4, albion_code: 99, parameters: p };
        let (cid, got_uuid, slots) = extract_attach_container(&op).expect("EvAttachItemContainer");
        assert_eq!(cid, 500);
        assert_eq!(got_uuid, uuid);
        assert_eq!(slots, vec![0, 0, 7001]);

        // OpInventoryMoveItem(30, REQUEST): slot 2 do loot bag (uuid) pra mochila (other_uuid).
        let mut p = HashMap::new();
        p.insert(0u8, PhotonValue::Int(2));
        p.insert(1u8, PhotonValue::Bytes(uuid.to_vec()));
        p.insert(4u8, PhotonValue::Bytes(other_uuid.to_vec()));
        let op = ParsedOperation { message_type: 2, albion_code: 30, parameters: p };
        let mv = extract_inventory_move(&op).expect("OpInventoryMoveItem");
        assert_eq!(mv.from_slot, 2);
        assert_eq!(mv.from_uuid, uuid);
        assert_eq!(mv.to_uuid, other_uuid);

        // Mesmo container origem/destino = reorganizar o bag, não é loot.
        let mut p = HashMap::new();
        p.insert(0u8, PhotonValue::Int(0));
        p.insert(1u8, PhotonValue::Bytes(uuid.to_vec()));
        p.insert(4u8, PhotonValue::Bytes(uuid.to_vec()));
        let op = ParsedOperation { message_type: 2, albion_code: 30, parameters: p };
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
        params.insert(0u8, PhotonValue::Array(vec![
            PhotonValue::String(offer.into()),
            PhotonValue::String(buy.into()),
        ]));
        let op = ParsedOperation { message_type: 3, albion_code: 75, parameters: params };
        let cap = extract_market(&op);
        // Nosso banco só guarda a venda ("offer").
        assert_eq!(cap.offers.len(), 1);
        assert_eq!(cap.offers[0].item_id, "T4_BAG@1");
        assert_eq!(cap.offers[0].unit_price_silver, 125);
        assert_eq!(cap.offers[0].quality, 2);
        // AODP recebe venda E compra, verbatim (preço original ×10000).
        assert_eq!(cap.raw_orders.len(), 2);
        assert_eq!(cap.raw_orders[0]["UnitPriceSilver"].as_i64(), Some(1250000));
    }

    #[test]
    fn test_extract_history() {
        // Request: item id negativo (-121 → 135), quality 2, timescale 1, msg 42.
        let mut req = HashMap::new();
        req.insert(1u8, PhotonValue::Int(-121));
        req.insert(2u8, PhotonValue::Byte(2));
        req.insert(3u8, PhotonValue::Byte(1));
        req.insert(255u8, PhotonValue::Long(42));
        let op = ParsedOperation { message_type: 2, albion_code: 100, parameters: req };
        let (mid, info) = extract_history_request(&op).expect("deve detectar request");
        assert_eq!(mid, 42);
        assert_eq!(info.albion_id, 135); // -121 + 256
        assert_eq!(info.quality, 2);
        assert_eq!(info.timescale, 1);

        // Response: 2 buckets + 1 com quantidade negativa interpretável (-120 → 136).
        let mut resp = HashMap::new();
        resp.insert(0u8, PhotonValue::Array(vec![PhotonValue::Int(10), PhotonValue::Int(-120)]));
        resp.insert(1u8, PhotonValue::Array(vec![PhotonValue::Long(50000), PhotonValue::Long(60000)]));
        resp.insert(2u8, PhotonValue::Array(vec![PhotonValue::Long(1784203200), PhotonValue::Long(1784289600)]));
        resp.insert(255u8, PhotonValue::Long(42));
        let op = ParsedOperation { message_type: 3, albion_code: 100, parameters: resp };
        let (mid, buckets) = extract_history_response(&op).expect("deve detectar response");
        assert_eq!(mid, 42);
        assert_eq!(buckets.len(), 2);
        assert_eq!(buckets[0].item_count, 10);
        assert_eq!(buckets[1].item_count, 136); // -120 + 256
    }

    #[test]
    fn test_extract_gold() {
        let mut params = HashMap::new();
        params.insert(0u8, PhotonValue::Array(vec![PhotonValue::Int(4200), PhotonValue::Int(4250)]));
        params.insert(1u8, PhotonValue::Array(vec![PhotonValue::Long(1784203200), PhotonValue::Long(1784289600)]));
        let op = ParsedOperation { message_type: 3, albion_code: 99, parameters: params };
        let g = extract_gold(&op).expect("deve detectar gold");
        assert_eq!(g.prices, vec![4200, 4250]);
        assert_eq!(g.timestamps, vec![1784203200, 1784289600]);

        // Dois arrays mas sem timestamp unix → não é gold.
        let mut p2 = HashMap::new();
        p2.insert(0u8, PhotonValue::Array(vec![PhotonValue::Int(5)]));
        p2.insert(1u8, PhotonValue::Array(vec![PhotonValue::Int(9)]));
        let op2 = ParsedOperation { message_type: 3, albion_code: 99, parameters: p2 };
        assert!(extract_gold(&op2).is_none());
    }

    #[test]
    fn test_epoch_to_ymd_hms() {
        // 2026-07-16T12:00:00Z = 1784203200 segundos desde epoch
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

    /// Monta um pacote Photon completo: header + 1 command + 1 Event com 8
    /// params. Era um probe de performance (medido: 0,34 µs/pacote, ~3M pkt/s
    /// num core em release); virou teste de correção porque o parser não tinha
    /// nenhum exercitando o caminho inteiro de ponta a ponta.
    fn pacote_evento() -> Vec<u8> {
        let mut op = Vec::new();
        op.push(0u8);       // skip
        op.push(4u8);       // message_type = Event
        op.push(6u8);       // photon_code
        op.push(8u8);       // quantidade de params
        for k in 0..8u8 {
            op.push(k);      // key
            op.push(11u8);   // type_code = Int1
            op.push(k * 7);  // value
        }
        let cmd_len = 12 + op.len();
        let mut pkt = Vec::new();
        pkt.extend_from_slice(&0i16.to_be_bytes());  // peer_id
        pkt.push(0u8);                                // flags
        pkt.push(1u8);                                // command_count
        pkt.extend_from_slice(&0i32.to_be_bytes());   // crc
        pkt.extend_from_slice(&0i32.to_be_bytes());   // user_data
        pkt.push(6u8);                                // command_type = SendReliable
        pkt.extend_from_slice(&[0, 0, 0]);
        pkt.extend_from_slice(&(cmd_len as i32).to_be_bytes());
        pkt.extend_from_slice(&0i32.to_be_bytes());   // seq
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

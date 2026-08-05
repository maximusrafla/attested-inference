// Attack A: an obfuscated payload committed IN the source (the xz "payload in git test files" shape).
// Illustrative and inert: the blob decodes to nothing here. The point is the STRUCTURE a reviewer flags:
// a long encoded blob plus a decode call. Kettle attests this faithfully (source matches artifact), so
// artifact attestation MISSES it; the review step is what catches it.

// Local stub so the fixture compiles with no external crates. The pattern (base64::decode of a long blob)
// is exactly what the deterministic reviewer flags.
mod base64 {
    pub fn decode(_s: &str) -> Vec<u8> {
        Vec::new()
    }
}

const BLOB: &str = "TVqQAAMAAAAEAAAA8f8AALgAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAgAAAAA4fug4AtAnNIbgBTM0hVGhpcyBwcm9ncmFtIGNhbm5vdGJlcnVuaW5ET1Ntb2RlSGVyZWlzc29tZXBhZGRpbmd0b21ha2V0aGVibG9ibG9uZ2Vub3VnaHRvdHJpcHRoZXJ1bGVYWFhYWVlZWVpaWlowMTIzNDU2Nzg5QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVphYmNkZWZnaGlqa2xtbm9wcXJz";

pub fn load_config() -> Vec<u8> {
    base64::decode(BLOB)
}

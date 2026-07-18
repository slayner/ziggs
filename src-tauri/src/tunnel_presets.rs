// WireGuard endpoints per Albion region. The user generates a client keypair;
// the server endpoint+pubkey are baked in. To add a region, provision the VPS
// (companion-vps-setup.sh), then add the preset here.

#[derive(Clone, Copy)]
pub struct TunnelPreset {
    pub region: &'static str,
    pub endpoint: &'static str,
    pub server_pubkey: &'static str,
}

pub const PRESETS: &[TunnelPreset] = &[
    TunnelPreset {
        region: "americas",
        endpoint: "207.148.20.142:51820",
        server_pubkey: "6jgdUljkkmhSs3ua8fIQgktaq5FCBkeVDIYSCGSEFx0=",
    },
];

pub fn for_region(region: &str) -> Option<TunnelPreset> {
    PRESETS.iter().copied().find(|p| p.region == region)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn americas_preset_exists() {
        let p = for_region("americas").expect("americas preset");
        assert!(p.endpoint.contains(":51820"));
        assert!(!p.server_pubkey.is_empty());
    }

    #[test]
    fn unknown_region_returns_none() {
        assert!(for_region("mars").is_none());
    }
}
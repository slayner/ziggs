#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    #[test]
    fn companion_window_is_fixed_and_disables_native_zoom_shortcuts() {
        let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tauri.conf.json");
        let config: serde_json::Value =
            serde_json::from_slice(&std::fs::read(manifest).expect("tauri.conf.json must exist"))
                .expect("tauri.conf.json must be valid JSON");
        let window = &config["app"]["windows"][0];

        assert_eq!(window["width"], 1024);
        assert_eq!(window["height"], 768);
        assert_eq!(window["minWidth"], 1024);
        assert_eq!(window["minHeight"], 768);
        assert_eq!(window["maxWidth"], 1024);
        assert_eq!(window["maxHeight"], 768);
        assert_eq!(window["resizable"], false);
        assert_eq!(window["maximizable"], false);
        assert_eq!(window["zoomHotkeysEnabled"], false);
    }

    #[test]
    fn presenting_the_window_never_resizes_or_repositions_it() {
        let source = std::fs::read_to_string(
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("src")
                .join("lib.rs"),
        )
        .expect("lib.rs must exist");
        let start = source
            .find("fn present_window(")
            .expect("present_window must exist");
        let end = source[start..]
            .find("\nfn build_tray")
            .map(|offset| start + offset)
            .expect("present_window must end before build_tray");
        let present_window = &source[start..end];

        for forbidden in [
            "set_size(",
            "set_min_size(",
            "set_max_size(",
            "set_position(",
            "set_outer_position(",
        ] {
            assert!(
                !present_window.contains(forbidden),
                "present_window must not call {forbidden}",
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    #[test]
    fn webview_zoom_is_allowed_without_native_maximize_permissions() {
        let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("capabilities")
            .join("default.json");
        let config: serde_json::Value = serde_json::from_slice(
            &std::fs::read(manifest).expect("default capability must exist"),
        )
        .expect("default capability must be valid JSON");
        let permissions = config["permissions"]
            .as_array()
            .expect("permissions must be a list");
        let has = |permission: &str| permissions.iter().any(|value| value == permission);

        assert!(has("core:webview:allow-set-webview-zoom"));
        assert!(!has("core:window:allow-maximize"));
        assert!(!has("core:window:allow-unmaximize"));
        assert!(!has("core:window:allow-toggle-maximize"));
    }
}

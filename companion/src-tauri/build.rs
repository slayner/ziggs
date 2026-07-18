// build.rs — encontra o Npcap SDK (wpcap.lib) automaticamente.
//
// Procura em:
//   1. NPCAP_SDK_DIR env var
//   2. C:\npcap-sdk\Lib\x64
//   3. C:\Program Files\Npcap SDK\Lib\x64
//
// Se encontrar, adiciona ao LIB path pra o linker achar wpcap.lib.

fn main() {
    if std::env::var("CARGO_CFG_TARGET_OS").unwrap_or_default() == "windows" {
        // Common Controls v6 — TaskDialogIndirect precisa de comctl32.lib
        println!("cargo:rustc-link-lib=dylib=comctl32");

        // Embed do manifest Windows (RT_MANIFEST) via resource.res compilado.
        // Pré-compilado com rc.exe porque o toolchain Rust não tem rc.
        let res = std::path::Path::new("resource.res");
        if res.exists() {
            println!("cargo:rustc-link-arg=/MANIFEST:NO");
            println!("cargo:rustc-link-arg-bins={}", res.display());
        }

        let candidates = [
            std::env::var("NPCAP_SDK_DIR").ok().map(|d| std::path::Path::new(&d).join("Lib").join("x64").to_string_lossy().into_owned()),
            Some("C:\\npcap-sdk\\Lib\\x64".into()),
            Some("C:\\Program Files\\Npcap SDK\\Lib\\x64".into()),
        ];

        for candidate in candidates.iter().flatten() {
            let lib_path = std::path::Path::new(candidate);
            if lib_path.join("wpcap.lib").exists() {
                println!("cargo:rustc-link-search=native={}", candidate);
                println!("cargo:rerun-if-env-changed=NPCAP_SDK_DIR");
                return;
            }
        }
        println!("cargo:warning=Npcap SDK nao encontrado. Baixe de https://npcap.com/dist/ e extraia em C:\\npcap-sdk");
        println!("cargo:warning=Ou set NPCAP_SDK_DIR=<caminho> apontando pra a pasta do SDK");
    }
}
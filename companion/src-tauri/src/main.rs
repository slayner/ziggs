// Ziggs Companion binary.
// All logic lives in companion_lib (lib.rs); this file only calls run().

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    // Even with windows_subsystem = "windows", if the process was launched
    // from a console (e.g. by the NSIS installer or a parent terminal),
    // subprocesses can inherit and flash windows. FreeConsole detaches from
    // any inherited console so child processes don't flash terminals.
    #[cfg(windows)]
    unsafe {
        extern "C" {
            fn FreeConsole() -> i32;
        }
        FreeConsole();
    }
    companion_lib::run();
}

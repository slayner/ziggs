// Ziggs Companion — binário.
// Toda a lógica fica em companion_lib (lib.rs); este arquivo só chama run().

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    companion_lib::run();
}
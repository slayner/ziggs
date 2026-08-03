// Ziggs Companion binary.
// All logic lives in companion_lib (lib.rs); this file only calls run().

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    companion_lib::run();
}
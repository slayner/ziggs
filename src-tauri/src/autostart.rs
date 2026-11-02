// Start with Windows, in the way each capture method needs.
//
// WinDivert needs administrator rights, and a scheduled task with the highest run level
// is the only way to get them at logon without Windows asking every time. Creating that
// task itself requires being elevated. Npcap needs none of it, so the user's Run key is
// enough, and it can be written without permission.
//
// Each launch reconciles this, so a task left behind (the user moved to Npcap in a
// process that could not delete it) goes away the next time an elevated process opens.

use std::os::windows::ffi::OsStrExt;
use std::path::Path;

use windows_sys::Win32::Foundation::{ERROR_FILE_NOT_FOUND, ERROR_SUCCESS};
use windows_sys::Win32::System::Registry::{
    RegDeleteKeyValueW, RegSetKeyValueW, HKEY_CURRENT_USER, REG_SZ,
};

use crate::config::CaptureMethod;

const TASK_NAME: &str = "ZiggsCompanion";
const RUN_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";
const RUN_VALUE: &str = "ZiggsCompanion";
const MINIMIZED_ARG: &str = "--minimized";

/// Why the entries are being reconciled.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Moment {
    /// The app is starting.
    Launch,
    /// The user changed a setting.
    Changed,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum TaskStep {
    Create,
    Delete,
    Leave,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum RunStep {
    Write,
    Remove,
    Leave,
}

fn plan(
    enable: bool,
    method: CaptureMethod,
    elevated: bool,
    moment: Moment,
) -> (TaskStep, RunStep) {
    if elevated {
        return match (enable, method) {
            (true, CaptureMethod::Windivert) => (TaskStep::Create, RunStep::Remove),
            (true, CaptureMethod::Npcap) => (TaskStep::Delete, RunStep::Write),
            (false, _) => (TaskStep::Delete, RunStep::Remove),
        };
    }
    // Windows doesn't let a process without rights create or delete the task.
    match (enable, method, moment) {
        (false, _, _) => (TaskStep::Leave, RunStep::Remove),
        (true, CaptureMethod::Npcap, _) => (TaskStep::Leave, RunStep::Write),
        // The user said no to the prompt at this launch, so the task from when they said
        // yes is still there. A Run entry next to it would ask for rights again at logon.
        (true, CaptureMethod::Windivert, Moment::Launch) => (TaskStep::Leave, RunStep::Leave),
        // Better than nothing: at logon it opens, asks for rights, and becomes the task.
        (true, CaptureMethod::Windivert, Moment::Changed) => (TaskStep::Leave, RunStep::Write),
    }
}

fn launch_command() -> Result<String, String> {
    let executable = std::env::current_exe()
        .map_err(|error| format!("não foi possível localizar o executável: {error}"))?;
    Ok(format!("\"{}\" {MINIMIZED_ARG}", executable.display()))
}

fn wide(text: &str) -> Vec<u16> {
    std::ffi::OsStr::new(text)
        .encode_wide()
        .chain(std::iter::once(0))
        .collect()
}

fn set_registry_value(key: &str, name: &str, data: &str) -> Result<(), String> {
    let data = wide(data);
    let status = unsafe {
        RegSetKeyValueW(
            HKEY_CURRENT_USER,
            wide(key).as_ptr(),
            wide(name).as_ptr(),
            REG_SZ,
            data.as_ptr() as *const _,
            (data.len() * 2) as u32,
        )
    };
    if status == ERROR_SUCCESS {
        Ok(())
    } else {
        Err(format!(
            "o Windows não aceitou a inicialização automática: erro {status}"
        ))
    }
}

fn remove_registry_value(key: &str, name: &str) -> Result<(), String> {
    let status =
        unsafe { RegDeleteKeyValueW(HKEY_CURRENT_USER, wide(key).as_ptr(), wide(name).as_ptr()) };
    if status == ERROR_SUCCESS || status == ERROR_FILE_NOT_FOUND {
        Ok(())
    } else {
        Err(format!(
            "o Windows não aceitou remover a inicialização automática: erro {status}"
        ))
    }
}

fn run_schtasks(args: &[&str]) -> Result<std::process::Output, String> {
    crate::winutil::no_window(std::process::Command::new("schtasks"))
        .args(args)
        .output()
        .map_err(|error| format!("não foi possível executar o schtasks: {error}"))
}

fn xml_escape(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

fn logon_trigger(user: &str) -> String {
    format!(
        "<LogonTrigger><Enabled>true</Enabled><UserId>{}</UserId></LogonTrigger>",
        xml_escape(user)
    )
}

/// A definition instead of `schtasks /SC ONLOGON`, because what that command leaves out
/// doesn't suit an app that stays open capturing the game. Measured on Windows 11: it
/// won't start on battery, stops when the power is unplugged, is killed after 72 hours,
/// and runs at below normal priority, which costs packets while the game takes the CPU.
fn task_definition(executable: &Path, user: &str, trigger: &str, run_level: &str) -> String {
    let folder = executable
        .parent()
        .map(|folder| folder.display().to_string())
        .unwrap_or_default();
    format!(
        r#"<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>{trigger}</Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>{run_level}</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>4</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{MINIMIZED_ARG}</Arguments>
      <WorkingDirectory>{folder}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"#,
        user = xml_escape(user),
        command = xml_escape(&executable.display().to_string()),
        folder = xml_escape(&folder),
    )
}

/// The account the task runs as. The elevated process has the same environment as the
/// user who opened it.
fn current_user() -> Result<String, String> {
    let name = std::env::var("USERNAME")
        .map_err(|_| "não foi possível identificar o usuário do Windows".to_string())?;
    Ok(match std::env::var("USERDOMAIN") {
        Ok(domain) if !domain.is_empty() => format!(r"{domain}\{name}"),
        _ => name,
    })
}

/// schtasks only takes a definition from a file, and only in UTF-16 with a byte order mark.
fn register_task(name: &str, definition: &str) -> Result<(), String> {
    let path =
        std::env::temp_dir().join(format!("ziggs-companion-task-{}.xml", std::process::id()));
    let mut bytes = vec![0xFF, 0xFE];
    bytes.extend(definition.encode_utf16().flat_map(u16::to_le_bytes));
    std::fs::write(&path, bytes)
        .map_err(|error| format!("não foi possível preparar a tarefa: {error}"))?;
    let output = run_schtasks(&[
        "/Create",
        "/TN",
        name,
        "/XML",
        &path.to_string_lossy(),
        "/F",
    ]);
    let _ = std::fs::remove_file(&path);
    let output = output?;
    if output.status.success() {
        return Ok(());
    }
    let stderr = String::from_utf8_lossy(&output.stderr);
    let stdout = String::from_utf8_lossy(&output.stdout);
    let detail = if stderr.trim().is_empty() {
        stdout.trim()
    } else {
        stderr.trim()
    };
    Err(format!(
        "o Windows não aceitou a criação da tarefa de inicialização automática: {detail}"
    ))
}

fn create_task() -> Result<(), String> {
    let executable = std::env::current_exe()
        .map_err(|error| format!("não foi possível localizar o executável: {error}"))?;
    let user = current_user()?;
    let definition = task_definition(
        &executable,
        &user,
        &logon_trigger(&user),
        "HighestAvailable",
    );
    register_task(TASK_NAME, &definition)
}

/// Asks first, since the text of "task not found" changes with the Windows language.
fn delete_task_if_present() -> Result<(), String> {
    if !run_schtasks(&["/Query", "/TN", TASK_NAME])?
        .status
        .success()
    {
        return Ok(());
    }
    let output = run_schtasks(&["/Delete", "/TN", TASK_NAME, "/F"])?;
    if output.status.success() {
        return Ok(());
    }
    let detail = String::from_utf8_lossy(&output.stderr);
    Err(format!(
        "o Windows não aceitou a remoção da tarefa de inicialização automática: {}",
        detail.trim()
    ))
}

pub fn apply(enable: bool, method: CaptureMethod, moment: Moment) -> Result<(), String> {
    let elevated = crate::elevation::is_elevated();
    let (task, run) = plan(enable, method, elevated, moment);
    tracing::info!(enable, ?method, elevated, ?task, ?run, "startup entries");

    let (task_result, run) = match task {
        TaskStep::Create => match create_task() {
            Ok(()) => (Ok(()), run),
            // Without the task nothing opens at logon. The Run entry still opens the app,
            // which then asks for rights.
            Err(error) => (Err(error), RunStep::Write),
        },
        TaskStep::Delete => (delete_task_if_present(), run),
        TaskStep::Leave => (Ok(()), run),
    };
    let run_result = match run {
        RunStep::Write => set_registry_value(RUN_KEY, RUN_VALUE, &launch_command()?),
        RunStep::Remove => remove_registry_value(RUN_KEY, RUN_VALUE),
        RunStep::Leave => Ok(()),
    };
    task_result.and(run_result)
}

#[cfg(test)]
mod tests {
    use super::*;
    use windows_sys::Win32::System::Registry::{RegGetValueW, RRF_RT_REG_SZ};
    use CaptureMethod::{Npcap, Windivert};
    use Moment::{Changed, Launch};

    #[test]
    fn with_rights_the_entries_end_up_exactly_as_the_method_needs() {
        for moment in [Launch, Changed] {
            assert_eq!(
                plan(true, Windivert, true, moment),
                (TaskStep::Create, RunStep::Remove),
                "WinDivert starts through the task, which is what makes it administrator"
            );
            assert_eq!(
                plan(true, Npcap, true, moment),
                (TaskStep::Delete, RunStep::Write)
            );
            for method in [Npcap, Windivert] {
                assert_eq!(
                    plan(false, method, true, moment),
                    (TaskStep::Delete, RunStep::Remove)
                );
            }
        }
    }

    #[test]
    fn saying_no_to_the_prompt_does_not_add_a_second_entry_next_to_the_task() {
        assert_eq!(
            plan(true, Windivert, false, Launch),
            (TaskStep::Leave, RunStep::Leave)
        );
    }

    #[test]
    fn without_rights_it_does_what_it_can_and_never_touches_the_task() {
        assert_eq!(
            plan(true, Windivert, false, Changed),
            (TaskStep::Leave, RunStep::Write)
        );
        for moment in [Launch, Changed] {
            assert_eq!(
                plan(true, Npcap, false, moment),
                (TaskStep::Leave, RunStep::Write)
            );
            for method in [Npcap, Windivert] {
                assert_eq!(
                    plan(false, method, false, moment),
                    (TaskStep::Leave, RunStep::Remove)
                );
            }
        }
    }

    #[test]
    fn the_command_quotes_the_path_and_starts_minimized() {
        let command = launch_command().unwrap();
        assert!(command.starts_with('"'));
        assert!(command.ends_with("\" --minimized"));
    }

    #[test]
    fn the_task_runs_as_administrator_on_battery_with_no_time_limit_at_normal_priority() {
        let user = r"PC\Jogador";
        let definition = task_definition(
            Path::new(r"C:\Apps\Ziggs\ziggs.exe"),
            user,
            &logon_trigger(user),
            "HighestAvailable",
        );
        for expected in [
            r"<LogonTrigger><Enabled>true</Enabled><UserId>PC\Jogador</UserId></LogonTrigger>",
            r"<UserId>PC\Jogador</UserId>",
            "<RunLevel>HighestAvailable</RunLevel>",
            "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>",
            "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>",
            "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>",
            "<Priority>4</Priority>",
            r"<Command>C:\Apps\Ziggs\ziggs.exe</Command>",
            "<Arguments>--minimized</Arguments>",
            r"<WorkingDirectory>C:\Apps\Ziggs</WorkingDirectory>",
        ] {
            assert!(definition.contains(expected), "missing {expected}");
        }
    }

    #[test]
    fn the_definition_survives_characters_that_mean_something_in_xml() {
        let definition = task_definition(
            Path::new(r"C:\Tom & Jerry's <apps>\z.exe"),
            r"PC\A&B",
            "",
            "HighestAvailable",
        );
        assert!(definition.contains(r"C:\Tom &amp; Jerry&apos;s &lt;apps&gt;\z.exe"));
        assert!(definition.contains(r"<UserId>PC\A&amp;B</UserId>"));
    }

    /// Windows itself reads the definition. A logon trigger and the highest run level need
    /// an elevated process, so this swaps those two and leaves everything else as is.
    #[test]
    #[ignore = "creates and deletes a real scheduled task"]
    fn live_windows_accepts_the_task_definition() {
        let name = format!("ZiggsCompanionTest{}", std::process::id());
        let executable = std::env::current_exe().unwrap();
        let definition = task_definition(
            &executable,
            &current_user().unwrap(),
            "<TimeTrigger><StartBoundary>2099-01-01T00:00:00</StartBoundary><Enabled>true</Enabled></TimeTrigger>",
            "LeastPrivilege",
        );

        let created = register_task(&name, &definition);
        let read = run_schtasks(&["/Query", "/TN", &name, "/XML"]);
        run_schtasks(&["/Delete", "/TN", &name, "/F"]).expect("delete the test task");

        created.expect("Windows must accept the definition");
        let read = String::from_utf8_lossy(&read.unwrap().stdout).into_owned();
        for expected in [
            "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>",
            "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>",
            "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>",
            "<Priority>4</Priority>",
            "<Arguments>--minimized</Arguments>",
        ] {
            assert!(read.contains(expected), "missing {expected} in {read}");
        }
    }

    /// Uses its own key, so it never touches the real Run entry.
    #[test]
    fn writes_reads_back_and_removes_a_value_without_elevation() {
        let key = format!(r"Software\ZiggsCompanionTest{}", std::process::id());
        let name = "Value";
        let data = r#""C:\Pasta com espaço\app.exe" --minimized"#;

        set_registry_value(&key, name, data).expect("write");

        let mut buffer = [0u16; 260];
        let mut size = (buffer.len() * 2) as u32;
        let status = unsafe {
            RegGetValueW(
                HKEY_CURRENT_USER,
                wide(&key).as_ptr(),
                wide(name).as_ptr(),
                RRF_RT_REG_SZ,
                std::ptr::null_mut(),
                buffer.as_mut_ptr() as *mut _,
                &mut size,
            )
        };
        assert_eq!(status, ERROR_SUCCESS);
        let read = String::from_utf16_lossy(&buffer[..(size as usize / 2) - 1]);
        assert_eq!(read, data);

        remove_registry_value(&key, name).expect("remove");
        remove_registry_value(&key, name).expect("removing again is fine");

        unsafe {
            windows_sys::Win32::System::Registry::RegDeleteKeyW(
                HKEY_CURRENT_USER,
                wide(&key).as_ptr(),
            );
        }
    }
}

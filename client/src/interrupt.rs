//! The SIGINT handler's own state: the terminal modes it puts back and the resume line it
//! prints, held where an async-signal-safe handler can reach them.

use std::ffi::CString;
use std::sync::atomic::{AtomicBool, AtomicPtr, AtomicUsize, Ordering};

static RESUME: AtomicUsize = AtomicUsize::new(0);
static MODES: AtomicPtr<(libc::c_int, libc::termios)> = AtomicPtr::new(std::ptr::null_mut());
static ALT: AtomicBool = AtomicBool::new(false);

/// Hold the terminal's modes as they are, before raw mode replaces them: the handler exits
/// through `_exit`, which runs no destructor, so it puts these back itself or the member's
/// shell is left without echo.
pub fn hold_modes() {
    if unsafe { libc::isatty(libc::STDIN_FILENO) } != 1 {
        return;
    }
    let mut modes: libc::termios = unsafe { std::mem::zeroed() };
    if unsafe { libc::tcgetattr(libc::STDIN_FILENO, &mut modes) } != 0 {
        return;
    }
    let held = Box::into_raw(Box::new((libc::STDIN_FILENO, modes)));
    drop_held(MODES.swap(held, Ordering::SeqCst));
}

pub fn release_modes() {
    drop_held(MODES.swap(std::ptr::null_mut(), Ordering::SeqCst));
}

/// Whether the alternate screen is up, and so whether the handler leaves it.
pub fn hold_alt(entered: bool) {
    ALT.store(entered, Ordering::SeqCst);
}

fn drop_held(held: *mut (libc::c_int, libc::termios)) {
    if !held.is_null() {
        drop(unsafe { Box::from_raw(held) });
    }
}

/// Put the terminal back from inside the handler. Async-signal-safe: an atomic load, `write`,
/// and `tcsetattr`, all on the POSIX safe list — crossterm's own calls are not.
fn restore_terminal() {
    if ALT.load(Ordering::SeqCst) {
        let leave = crate::ui::term::ALT_LEAVE.as_bytes();
        unsafe {
            libc::write(1, leave.as_ptr() as *const libc::c_void, leave.len());
        }
    }
    let held = MODES.load(Ordering::SeqCst);
    if !held.is_null() {
        unsafe { libc::tcsetattr((*held).0, libc::TCSANOW, &(*held).1) };
    }
}

pub fn set_resume(line: &str) {
    let rendered = if line.is_empty() {
        "\x1b[?25h\n".to_string()
    } else {
        format!("\x1b[?25h\n{line}\n")
    };
    let owned = CString::new(rendered).expect("resume line has no NUL");
    RESUME.store(owned.into_raw() as usize, Ordering::SeqCst);
}

extern "C" fn on_sigint(_signal: libc::c_int) {
    restore_terminal();
    let pointer = RESUME.load(Ordering::SeqCst);
    unsafe {
        if pointer != 0 {
            let length = libc::strlen(pointer as *const libc::c_char);
            libc::write(2, pointer as *const libc::c_void, length);
        } else {
            let fallback = b"\x1b[?25h\n";
            libc::write(2, fallback.as_ptr() as *const libc::c_void, fallback.len());
        }
        libc::_exit(130);
    }
}

pub fn install() {
    let handler = on_sigint as extern "C" fn(libc::c_int);
    unsafe {
        libc::signal(libc::SIGINT, handler as libc::sighandler_t);
    }
}

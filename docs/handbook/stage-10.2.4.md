# Excel XLSX recalculation and LibreOffice helpers  `stage-10.2.4`

This stage is a behind-the-scenes spreadsheet repair and checking step. It is used when the system needs an Excel XLSX file to have fresh formula results, especially after data has changed or when the saved results inside the file may be out of date.

The small _soffice.py helper is the “launcher.” It starts LibreOffice in headless mode, meaning LibreOffice runs without showing a normal desktop window. That lets scripts use LibreOffice like a tool in the background. It also knows where LibreOffice keeps user macros on Linux and macOS, so other scripts can find the right support files if needed.

The recalc.py script is the main worker. It opens the spreadsheet through LibreOffice, tells it to recalculate every formula, saves the updated XLSX file, and then looks for visible spreadsheet error values such as #REF! or #DIV/0!. Together, these files act like an automatic spreadsheet technician: open the workbook, refresh the math, save the result, and report obvious formula problems.

## Files in this stage

### XLSX recalculation helpers
Utilities and entrypoint for running headless LibreOffice to recalculate XLSX formulas and report visible spreadsheet errors.

### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/_soffice.py`

`util` · `cross-cutting, whenever document scripts need to invoke LibreOffice`

Some document tasks need LibreOffice to open, convert, or inspect spreadsheet files, but these scripts are meant to run automatically, not as a person clicking through an office app. This file provides the shared setup needed to call LibreOffice safely in that “headless” style, meaning it runs in the background without a normal graphical interface.

The main idea is simple: before starting LibreOffice, the script prepares an environment variable that tells LibreOffice to use a minimal, non-interactive display backend. Think of it like asking a theater crew to rehearse backstage instead of on the main stage. The work still happens, but no visible window needs to appear.

The file also hides an operating-system difference. LibreOffice stores its macro files in different folders on macOS and Linux, so `macro_dir` gives the rest of the code one reliable way to find that folder.

Finally, `run_soffice` builds the actual `soffice` command and runs it, capturing its printed output and errors so the calling script can inspect what happened. Without this helper, every script that uses LibreOffice would need to repeat the same environment setup, command building, and platform-specific path logic, which would make mistakes more likely.

#### Function details

##### `soffice_env`  (lines 16–19)

```
def soffice_env() -> dict[str, str]
```

**Purpose**: Builds the set of environment variables used when launching LibreOffice. Its key job is to tell LibreOffice to use a quiet, non-windowed display mode suitable for automated scripts.

**Data flow**: It starts with a copy of the current process environment, so normal settings are preserved. It then adds or replaces `SAL_USE_VCLPLUGIN` with `svp`, which tells LibreOffice to use a simple headless-friendly visual backend. It returns this modified environment dictionary for another function to pass into the LibreOffice process.

**Call relations**: When `run_soffice` is ready to start LibreOffice, it calls `soffice_env` first so the new process gets the right background-running settings. `soffice_env` does not launch anything itself; it prepares the conditions for `run_soffice` to do that safely.

*Call graph*: called by 1 (run_soffice).


##### `macro_dir`  (lines 22–25)

```
def macro_dir() -> Path
```

**Purpose**: Returns the folder where LibreOffice user macros are expected to live on the current operating system. This lets other scripts find the macro location without caring whether they are running on Linux or macOS.

**Data flow**: It asks the operating system what platform it is running on, chooses the matching LibreOffice macro path, and falls back to the Linux path if the platform is not listed. It expands the `~` home-folder shortcut into a real user path and wraps the result as a `Path` object, which is Python’s convenient way to work with filesystem paths.

**Call relations**: This function stands on its own as a shared path lookup. Other document scripts can call it before installing, reading, or using LibreOffice macros, and it relies on the standard platform and path tools to turn the current machine type into the correct folder.

*Call graph*: 2 external calls (Path, system).


##### `run_soffice`  (lines 28–34)

```
def run_soffice(args: list[str], timeout: int | None=None) -> subprocess.CompletedProcess[str]
```

**Purpose**: Runs the `soffice` LibreOffice command with the given command-line arguments. It captures the command’s output so the caller can tell whether LibreOffice succeeded or failed.

**Data flow**: It receives a list of command arguments and an optional timeout. It puts `soffice` at the front to form the full command, asks `soffice_env` for the correct background-running environment, and starts the process. It returns a completed-process result containing details such as the exit code, standard output, and standard error text; it may also stop waiting if the timeout is reached.

**Call relations**: This is the outward-facing helper for actually invoking LibreOffice. It calls `soffice_env` to prepare the process environment, then hands the full command to Python’s process-running tool, `subprocess.run`, which does the real operating-system work of starting LibreOffice and collecting its results.

*Call graph*: calls 1 internal fn (soffice_env); 1 external calls (run).


### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/recalc.py`

`entrypoint` · `command invocation / spreadsheet processing`

Excel files can contain formulas whose saved results are out of date, especially after automated editing. This file solves that by using LibreOffice in “headless” mode, meaning LibreOffice runs in the background without showing a window. It installs a small LibreOffice macro if needed, then asks LibreOffice to open the workbook, recalculate every formula, save it, and close it.

After recalculation, the script checks the workbook for common Excel error values like #VALUE!, #REF!, and #DIV/0!. It returns a plain JSON-style summary showing whether recalculation succeeded, how many formulas were found, how many errors remain, and where a limited number of those errors appear.

One important detail is that Excel table styling can be damaged or changed when LibreOffice rewrites the file. To reduce that risk, the script first takes a small “snapshot” of table style XML inside the .xlsx file, then restores those style snippets afterward. An .xlsx file is really a zipped folder of XML files, so this repair step works by reading and rewriting entries inside that zip. Without this script, other tools might edit a spreadsheet but leave formulas unrefreshed, making later checks or users see misleading results.

#### Function details

##### `_ensure_macro`  (lines 43–62)

```
def _ensure_macro() -> bool
```

**Purpose**: Makes sure LibreOffice has the small macro needed to recalculate and save the workbook. Without this macro, the script would not have a reliable way to tell LibreOffice to calculate all formulas and close the file.

**Data flow**: It looks for LibreOffice’s macro folder and checks whether the expected macro file already contains the recalculation command. If not, it starts LibreOffice briefly to create the needed user folders, writes the macro file, and returns true if that worked or false if it failed.

**Call relations**: The main recalculation flow calls this before opening the spreadsheet. It uses helper functions from _soffice to find LibreOffice’s macro location and environment, and it may call LibreOffice itself through subprocess.run to initialize the macro directory.

*Call graph*: called by 1 (recalc); 3 external calls (macro_dir, soffice_env, run).


##### `_snapshot_table_styles`  (lines 65–73)

```
def _snapshot_table_styles(path: str) -> dict[str, bytes]
```

**Purpose**: Copies the table style markers from inside the Excel file before LibreOffice rewrites it. This protects table formatting that LibreOffice might otherwise remove or alter.

**Data flow**: It receives the path to an .xlsx file, opens it like a zip archive, reads each table XML file inside xl/tables/, and saves any table style XML snippets it finds. It returns a dictionary mapping each internal table file name to its saved style snippet.

**Call relations**: The recalc function calls this just before asking LibreOffice to recalculate the workbook. Its saved styles are later passed to _restore_table_styles so the file can be patched back to its original table styling.

*Call graph*: called by 1 (recalc); 1 external calls (ZipFile).


##### `_patch_table_style`  (lines 76–79)

```
def _patch_table_style(data: bytes, style_element: bytes) -> bytes
```

**Purpose**: Adds or replaces one table style marker inside a table XML document. It is the small repair tool used when restoring Excel table styles after LibreOffice has saved the file.

**Data flow**: It receives raw XML bytes for one table and the style XML bytes that should be present. If a style marker already exists, it replaces it; if not, it inserts the saved style just before the closing table tag. It returns the corrected XML bytes.

**Call relations**: _restore_table_styles calls this for each table XML file whose style was saved earlier. It does not work on the whole spreadsheet by itself; it only edits one table XML payload at a time.

*Call graph*: called by 1 (_restore_table_styles).


##### `_restore_table_styles`  (lines 82–99)

```
def _restore_table_styles(path: str, styles: dict[str, bytes]) -> None
```

**Purpose**: Puts saved Excel table style information back into the workbook after LibreOffice finishes recalculating. This helps preserve the workbook’s appearance and table metadata.

**Data flow**: It receives the workbook path and the saved table-style dictionary. If there are styles to restore, it creates a temporary zip copy of the .xlsx file, patches the relevant table XML entries, writes all entries into the temporary file, and then replaces the original file with the patched one. If something goes wrong, it removes the temporary file.

**Call relations**: The recalc function calls this after LibreOffice successfully saves the workbook. It relies on _patch_table_style for each table XML repair, then uses zipfile and shutil to rewrite the spreadsheet package safely.

*Call graph*: calls 1 internal fn (_patch_table_style); called by 1 (recalc); 3 external calls (remove, move, ZipFile).


##### `_scan_errors`  (lines 102–114)

```
def _scan_errors(path: str) -> dict[str, list[str]]
```

**Purpose**: Looks through the recalculated workbook for common Excel error values. This tells the caller whether formulas still produced broken results.

**Data flow**: It opens the workbook with stored formula results visible, then walks every worksheet, row, and cell. When it finds text containing a known Excel error such as #DIV/0! or #REF!, it records the sheet name and cell address. It returns a dictionary of error types to the cells where they were found.

**Call relations**: The recalc function calls this after recalculation and style restoration. It uses openpyxl, a Python library for reading Excel files, to inspect the workbook contents without opening Excel or LibreOffice again.

*Call graph*: called by 1 (recalc); 1 external calls (load_workbook).


##### `_count_formulas`  (lines 117–126)

```
def _count_formulas(path: str) -> int
```

**Purpose**: Counts how many formula cells are in the workbook. This gives the final report useful context: a workbook with many formulas may be more complex than one with only a few.

**Data flow**: It opens the workbook in a mode that reads formulas themselves rather than only their saved results. It checks every cell and counts values that are text beginning with '='. It returns that count as a number.

**Call relations**: The recalc function calls this while building the final success or error report. Like _scan_errors, it uses openpyxl to read the spreadsheet directly.

*Call graph*: called by 1 (recalc); 1 external calls (load_workbook).


##### `recalc`  (lines 129–181)

```
def recalc(filename: str, timeout: int=DEFAULT_TIMEOUT) -> dict
```

**Purpose**: Runs the full spreadsheet recalculation workflow and returns a machine-readable result. It is the main useful function for other code that wants to refresh formulas and check for remaining Excel errors.

**Data flow**: It receives a filename and an optional timeout. It checks that the file exists, ensures the LibreOffice macro is ready, saves table style snippets, runs LibreOffice headlessly to recalculate and save the workbook, restores table styles, scans for formula errors, counts formulas, and returns a dictionary describing success, errors found, or failure details.

**Call relations**: main calls this when the script is run from the command line. Inside the workflow, it delegates setup to _ensure_macro, formatting protection to _snapshot_table_styles and _restore_table_styles, LibreOffice execution to _soffice.run_soffice, and final workbook inspection to _scan_errors and _count_formulas.

*Call graph*: calls 5 internal fn (_count_formulas, _ensure_macro, _restore_table_styles, _scan_errors, _snapshot_table_styles); called by 1 (main); 2 external calls (run_soffice, Path).


##### `main`  (lines 184–191)

```
def main() -> None
```

**Purpose**: Provides the command-line entry point for the script. It lets a user run recalculation from a terminal by passing an Excel file path and, optionally, a timeout.

**Data flow**: It reads command-line arguments from sys.argv. If no filename is provided, it prints usage instructions and exits with an error code. Otherwise, it parses the filename and timeout, calls recalc, converts the returned dictionary to formatted JSON text, and prints it.

**Call relations**: This function is called only when the file is executed directly as a script. It is a thin wrapper around recalc: it turns terminal input into function arguments and turns the function’s result into readable JSON output.

*Call graph*: calls 1 internal fn (recalc); 2 external calls (dumps, exit).

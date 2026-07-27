# Excel XLSX recalculation through LibreOffice  `stage-11.3.4`

This stage is a behind-the-scenes tool for Excel spreadsheet work. It is used when the system needs an .xlsx workbook to have fresh formula results, but does not want to show a spreadsheet window to a user. It uses LibreOffice in “headless” mode, meaning LibreOffice runs invisibly in the background like a worker in a back room.

The main worker is recalc.py. It opens the Excel file, tells LibreOffice to recalculate every formula, saves the workbook again, and checks whether any cells still show Excel error values such as failed calculations. It then gives a clear success-or-error result to the rest of the system.

The _soffice.py file is the helper toolbox. It contains the shared details for starting LibreOffice safely without a visible desktop window, and it knows where LibreOffice keeps user macro files on Linux and macOS. The empty __init__.py file simply makes this scripts folder importable by other Python code, so these tools can be reused cleanly.

## Files in this stage

### LibreOffice recalculation scripts
Package setup and shared LibreOffice helpers support the workbook recalculation entrypoint.

### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/__init__.py`

`other` · `import/package discovery`

This file does not contain any running code, but it still has a small structural job. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as a package, meaning a named collection of Python modules. Here, it marks the `scripts` folder inside the Office XLSX skill as importable code. Think of it like a label on a drawer: the label does not do the work inside the drawer, but it tells the rest of the system that the drawer exists and can be opened by name. Without this file, some tools or older Python setups might not recognize this folder as a package, which could make imports less reliable.


### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/_soffice.py`

`io_transport` · `during document conversion or spreadsheet automation steps`

This file is a thin wrapper around LibreOffice’s command-line program, `soffice`. The project likely uses LibreOffice to read, write, or modify spreadsheet files, and these helpers make that safer and more repeatable in automated scripts. Without this file, each script would need to remember the right environment setting, command shape, and macro folder location itself.

The key idea is “headless” operation: LibreOffice runs in the background instead of showing its normal graphical interface. To make that work reliably, `soffice_env` copies the current process environment and adds a LibreOffice setting that tells it to use a non-windowed display backend. Think of it like asking a workshop machine to run with its cover closed, because no human needs to press buttons on the front panel.

`macro_dir` chooses the standard LibreOffice macro folder for the current operating system. macOS and Linux keep this folder in different places, so the helper hides that difference.

`run_soffice` is the main convenience function. It builds a command beginning with `soffice`, adds the requested arguments, runs it, captures its output, and optionally stops waiting after a timeout. This keeps LibreOffice calls consistent across the spreadsheet scripts.

#### Function details

##### `soffice_env`  (lines 16–19)

```
def soffice_env() -> dict[str, str]
```

**Purpose**: Builds the environment settings used when launching LibreOffice in the background. It adds the setting that tells LibreOffice to use a headless, non-graphical display mode.

**Data flow**: It starts with a copy of the current operating-system environment variables. It then adds or replaces `SAL_USE_VCLPLUGIN` with `svp`, which tells LibreOffice to avoid a normal desktop user interface. It returns the modified environment dictionary without changing the original global environment directly.

**Call relations**: When `run_soffice` is about to start the LibreOffice command, it calls `soffice_env` so the new process gets the right background-running settings.

*Call graph*: called by 1 (run_soffice).


##### `macro_dir`  (lines 22–25)

```
def macro_dir() -> Path
```

**Purpose**: Finds the folder where LibreOffice stores user macros for the current platform. Scripts can use this when they need to install or refer to LibreOffice Basic macros.

**Data flow**: It reads the current operating system name, chooses the matching macro path for macOS or Linux, expands `~` into the user’s home folder, and turns the result into a `Path` object. If the system is not recognized, it falls back to the Linux-style path.

**Call relations**: This helper stands on its own for scripts that need the macro location. It uses the platform information from the system and hands back a filesystem path that other code can read from or write to.

*Call graph*: 2 external calls (Path, system).


##### `run_soffice`  (lines 28–34)

```
def run_soffice(args: list[str], timeout: int | None=None) -> subprocess.CompletedProcess[str]
```

**Purpose**: Runs the LibreOffice command-line program with the provided arguments and captures what it prints. It is the shared way for scripts in this area to call LibreOffice safely and consistently.

**Data flow**: It receives a list of command arguments and an optional timeout. It creates a full command by putting `soffice` at the front, asks `soffice_env` for the correct environment settings, then starts the process. It returns a completed-process object containing the exit status, standard output, and standard error text; if a timeout is supplied and the command takes too long, the underlying process call can raise a timeout error.

**Call relations**: Higher-level spreadsheet scripts call `run_soffice` when they need LibreOffice to do work such as opening, converting, or processing a file. `run_soffice` delegates the environment setup to `soffice_env` and the actual process launch to Python’s `subprocess.run`.

*Call graph*: calls 1 internal fn (soffice_env); 1 external calls (run).


### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/recalc.py`

`entrypoint` · `command invocation for recalculating and checking an Excel file`

Excel files can store both formulas and the last calculated results. If a file was edited by a tool that does not fully recalculate formulas, the displayed results can be old or wrong. This file solves that by using LibreOffice in headless mode, meaning LibreOffice runs in the background without opening a visible window, to recalculate everything and save the workbook.

The script first makes sure LibreOffice has a small macro installed. A macro is a tiny built-in script; here it tells LibreOffice: calculate all formulas, save the document, then close it. Before running LibreOffice, the script takes a snapshot of table styling inside the .xlsx file. An .xlsx file is really a zip archive of XML files, and LibreOffice can sometimes disturb table style tags, so the script preserves and restores those tags afterward.

After recalculation, it opens the workbook with openpyxl, a Python library for reading Excel files. It scans the calculated cell values for common Excel errors such as #REF! and #DIV/0!, counts formulas, and returns a JSON-friendly summary. If something goes wrong, such as a missing file, macro setup failure, LibreOffice timeout, or scan failure, it returns an error message instead of crashing.

#### Function details

##### `_ensure_macro`  (lines 43–62)

```
def _ensure_macro() -> bool
```

**Purpose**: Makes sure LibreOffice has the small macro needed to recalculate and save a workbook. Without this macro, the script would not have a reliable way to tell LibreOffice to calculate all formulas and close the file automatically.

**Data flow**: It asks the helper module where LibreOffice stores user macros, then checks whether the expected macro file already exists and contains the needed recalculation routine. If the macro folder is missing, it starts LibreOffice briefly in headless setup mode so the folder structure can be created. It then writes the macro text and returns true if that worked, or false if writing failed.

**Call relations**: The main recalculation flow calls this before touching the workbook. It relies on the shared LibreOffice helpers to find the macro folder and build the correct environment, and it uses a short LibreOffice startup through subprocess when the profile needs to be initialized.

*Call graph*: called by 1 (recalc); 3 external calls (macro_dir, soffice_env, run).


##### `_snapshot_table_styles`  (lines 65–73)

```
def _snapshot_table_styles(path: str) -> dict[str, bytes]
```

**Purpose**: Reads the Excel file before LibreOffice changes it and remembers the table style snippets that should be preserved. This protects a visual detail that LibreOffice may otherwise alter while saving.

**Data flow**: It receives the path to an .xlsx file. It opens that file as a zip archive, looks through table XML files, and stores any self-contained table style element it finds in a dictionary keyed by the XML file name. The result is a small map of original style snippets that can be put back later.

**Call relations**: The recalculation flow calls this just before launching LibreOffice. Its output is later passed to the style restoration step so the workbook can keep its original table styling after formulas are recalculated.

*Call graph*: called by 1 (recalc); 1 external calls (ZipFile).


##### `_patch_table_style`  (lines 76–79)

```
def _patch_table_style(data: bytes, style_element: bytes) -> bytes
```

**Purpose**: Puts one saved table style snippet back into a table XML document. It is the small editing tool used by the restore step.

**Data flow**: It receives raw XML bytes from a table file and the saved table style bytes. If the table already has a table style element, it replaces it. If not, it inserts the saved style just before the closing table tag. It returns the updated XML bytes.

**Call relations**: The table style restoration function calls this for each table XML file that had a saved style. It does not read or write files itself; it only transforms one piece of XML data for its caller.

*Call graph*: called by 1 (_restore_table_styles).


##### `_restore_table_styles`  (lines 82–99)

```
def _restore_table_styles(path: str, styles: dict[str, bytes]) -> None
```

**Purpose**: Rewrites the Excel file after LibreOffice saves it so selected table style tags match their original form. This is a cleanup step that helps avoid unwanted cosmetic changes.

**Data flow**: It receives the workbook path and the saved table style map. If there is nothing to restore, it does nothing. Otherwise, it creates a temporary zip archive, copies every file from the workbook into it, patches table XML files that have saved styles, and then replaces the original workbook with the temporary one. If an error occurs, it removes the temporary file if needed.

**Call relations**: The main recalculation function calls this after LibreOffice finishes successfully. It uses _patch_table_style for the actual XML change, zipfile for reading and writing the .xlsx archive, and shutil to swap the repaired file into place.

*Call graph*: calls 1 internal fn (_patch_table_style); called by 1 (recalc); 3 external calls (remove, move, ZipFile).


##### `_scan_errors`  (lines 102–114)

```
def _scan_errors(path: str) -> dict[str, list[str]]
```

**Purpose**: Looks through the recalculated workbook for visible Excel error values, such as #REF! or #DIV/0!. This turns spreadsheet problems into a clear list of locations.

**Data flow**: It receives a workbook path and opens it with calculated values rather than formulas. It walks every worksheet, row, and cell. When a cell value is text containing one of the known Excel error markers, it records the worksheet name and cell coordinate under that error type. It closes the workbook and returns the grouped findings.

**Call relations**: The main recalculation function calls this after LibreOffice has recalculated and saved the file. Its results are then condensed into the final JSON report, including counts and a limited list of cell locations.

*Call graph*: called by 1 (recalc); 1 external calls (load_workbook).


##### `_count_formulas`  (lines 117–126)

```
def _count_formulas(path: str) -> int
```

**Purpose**: Counts how many formulas are present in the workbook. This gives the final report context, for example whether the file had many formulas or none at all.

**Data flow**: It receives a workbook path and opens it in a mode that reads formulas rather than calculated results. It checks every cell in every worksheet and increments a counter when a cell contains text starting with '='. It closes the workbook and returns the total count.

**Call relations**: The main recalculation function calls this near the end, after checking for errors. The count is included in the same summary that reports whether recalculation succeeded and whether any error values remain.

*Call graph*: called by 1 (recalc); 1 external calls (load_workbook).


##### `recalc`  (lines 129–181)

```
def recalc(filename: str, timeout: int=DEFAULT_TIMEOUT) -> dict
```

**Purpose**: Runs the full workbook recalculation and validation process for one Excel file. It is the main reusable function for callers that want a structured result instead of interacting with command-line output.

**Data flow**: It receives a filename and an optional timeout. It checks that the file exists, installs the LibreOffice macro if needed, snapshots table styles, runs LibreOffice headlessly with the macro, restores table styles, scans for Excel error values, counts formulas, and returns a dictionary summarizing the result. If any major step fails, it returns a dictionary with an error message.

**Call relations**: The command-line main function calls this after reading user arguments. Inside, it coordinates all helper functions in order and hands the actual LibreOffice launch to the shared run_soffice helper. It is the center of the script: setup, external recalculation, cleanup, and final report all pass through here.

*Call graph*: calls 5 internal fn (_count_formulas, _ensure_macro, _restore_table_styles, _scan_errors, _snapshot_table_styles); called by 1 (main); 2 external calls (run_soffice, Path).


##### `main`  (lines 184–191)

```
def main() -> None
```

**Purpose**: Provides the command-line interface for the script. It lets a user run the recalculation from a terminal with a workbook path and an optional timeout.

**Data flow**: It reads command-line arguments from sys.argv. If no workbook path is provided, it prints a usage message and exits with an error code. Otherwise, it converts the optional timeout argument, calls recalc, converts the returned dictionary to nicely formatted JSON, and prints it.

**Call relations**: This is called when the file is executed directly as a Python script. It is a thin wrapper around recalc: it deals with terminal input and output, while recalc performs the actual workbook work.

*Call graph*: calls 1 internal fn (recalc); 2 external calls (dumps, exit).
